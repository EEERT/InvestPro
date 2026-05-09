"""FastAPI application for InvestPro convertible bond viewer."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import database as db
import fetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── refresh rate-limit config ──────────────────────────────────────────────────
REFRESH_COOLDOWN_SECONDS = int(os.environ.get("REFRESH_COOLDOWN", "60"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    logger.info("Database initialised at %s", db.DB_PATH)
    yield


app = FastAPI(title="InvestPro – Convertible Bond Viewer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── in-process refresh state ───────────────────────────────────────────────────
_refresh_lock = asyncio.Lock()
_refresh_running = False


# ── Pydantic response schemas ──────────────────────────────────────────────────

class Bond(BaseModel):
    code: str
    name: str
    price: Optional[float]
    change_pct: Optional[float]
    issue_size: Optional[float]
    remaining_size: Optional[float]
    stock_code: Optional[str]
    stock_name: Optional[str]
    stock_price: Optional[float]
    stock_change_pct: Optional[float]
    conv_price: Optional[float]
    conv_value: Optional[float]
    premium_rate: Optional[float]
    bond_ratio: Optional[float]
    expire_date: Optional[str]
    updated_at: str


class BondListResponse(BaseModel):
    total: int
    items: list[Bond]


class RefreshStatus(BaseModel):
    status: str          # running / success / error / never
    started_at: Optional[str]
    finished_at: Optional[str]
    message: Optional[str]
    cooldown_remaining: int  # seconds until next refresh is allowed


class RefreshResponse(BaseModel):
    accepted: bool
    detail: str


# ── helpers ────────────────────────────────────────────────────────────────────

def _cooldown_remaining() -> int:
    latest = db.get_latest_refresh()
    if not latest:
        return 0
    finished = latest.get("finished_at")
    if not finished:
        return REFRESH_COOLDOWN_SECONDS  # still running
    try:
        finished_dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - finished_dt).total_seconds()
        remaining = REFRESH_COOLDOWN_SECONDS - int(elapsed)
        return max(0, remaining)
    except Exception:
        return 0


def _do_refresh(log_id: int) -> None:
    global _refresh_running
    finished_at = ""
    try:
        records = fetcher.fetch_and_merge()
        count = db.upsert_bonds(records)
        finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.finish_refresh_log(log_id, finished_at, "success", f"Updated {count} records")
        logger.info("Refresh finished: %d records", count)
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.finish_refresh_log(log_id, finished_at, "error", str(exc))
        logger.exception("Refresh failed")
    finally:
        _refresh_running = False


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/api/bonds", response_model=BondListResponse)
def list_bonds(
    search: Annotated[Optional[str], Query(description="Search across code/name/stock")] = None,
    min_price: Annotated[Optional[float], Query()] = None,
    max_price: Annotated[Optional[float], Query()] = None,
    min_change: Annotated[Optional[float], Query()] = None,
    max_change: Annotated[Optional[float], Query()] = None,
    sort_by: Annotated[str, Query()] = "code",
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
) -> BondListResponse:
    rows = db.query_bonds(
        search=search,
        min_price=min_price,
        max_price=max_price,
        min_change=min_change,
        max_change=max_change,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return BondListResponse(total=len(rows), items=rows)  # type: ignore[arg-type]


@app.post("/api/bonds/refresh", response_model=RefreshResponse)
def trigger_refresh(background_tasks: BackgroundTasks) -> RefreshResponse:
    global _refresh_running

    if _refresh_running:
        return RefreshResponse(accepted=False, detail="刷新正在进行中，请稍候")

    remaining = _cooldown_remaining()
    if remaining > 0:
        return RefreshResponse(
            accepted=False,
            detail=f"刷新频率限制：请等待 {remaining} 秒后再试",
        )

    _refresh_running = True
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_id = db.insert_refresh_log(started_at)
    background_tasks.add_task(_do_refresh, log_id)
    return RefreshResponse(accepted=True, detail="刷新已开始")


@app.get("/api/bonds/refresh/status", response_model=RefreshStatus)
def refresh_status() -> RefreshStatus:
    latest = db.get_latest_refresh()
    if not latest:
        return RefreshStatus(
            status="never",
            started_at=None,
            finished_at=None,
            message=None,
            cooldown_remaining=0,
        )
    return RefreshStatus(
        status=latest["status"],
        started_at=latest.get("started_at"),
        finished_at=latest.get("finished_at"),
        message=latest.get("message"),
        cooldown_remaining=_cooldown_remaining(),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
