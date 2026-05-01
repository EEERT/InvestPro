"""AkShare data fetching and merging logic.

Two APIs used:
  - bond_zh_hs_cov_spot  → real-time quote (price, change_pct)
  - bond_zh_cov_info_ths → static info (issue_size, stock_code, stock_name, conv_price)

Only sh / sz prefixed codes are kept.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

# Columns we need from each source (key = internal name, value = possible source column names in priority order)
_SPOT_COL_MAP = {
    "code":       ["代码"],
    "name":       ["名称"],
    "price":      ["最新价", "现价", "price"],
    "change_pct": ["涨跌幅", "涨跌幅(%)"],
}

_INFO_COL_MAP = {
    "code":       ["转债代码"],
    "issue_size": ["实际发行量", "发行规模", "实际发行额", "发行量（亿元）", "发行量"],
    "stock_code": ["正股代码"],
    "stock_name": ["正股名称"],
    "conv_price": ["转股价", "转股价格"],
}

_SH_SZ_FULL_PATTERN = re.compile(r"^(sh|sz)\d{6}$", re.IGNORECASE)


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _extract(df: pd.DataFrame, col_map: dict[str, list[str]]) -> pd.DataFrame:
    rename: dict[str, str] = {}
    missing: list[str] = []
    for field, candidates in col_map.items():
        found = _pick_col(df, candidates)
        if found:
            rename[found] = field
        else:
            missing.append(field)
    if missing:
        logger.warning("Missing expected columns: %s. Available: %s", missing, list(df.columns))
    result = df[list(rename.keys())].rename(columns=rename)
    for field in col_map:
        if field not in result.columns:
            result[field] = None
    return result


def _normalize_code(code: str) -> str | None:
    """Return lowercase sh/sz prefixed code, or None if not sh/sz."""
    code = str(code).strip()
    if _SH_SZ_FULL_PATTERN.match(code):
        return code.lower()
    # Some APIs return bare 6-digit codes; without prefix we cannot determine exchange
    return None


def fetch_spot() -> pd.DataFrame:
    """Fetch real-time quote data from bond_zh_hs_cov_spot."""
    logger.info("Fetching bond_zh_hs_cov_spot ...")
    df = ak.bond_zh_hs_cov_spot()
    logger.info("bond_zh_hs_cov_spot returned %d rows, columns: %s", len(df), list(df.columns))
    spot = _extract(df, _SPOT_COL_MAP)

    # Normalize code
    spot["code"] = spot["code"].apply(_normalize_code)
    spot = spot.dropna(subset=["code"])

    # Keep only sh/sz
    spot = spot[spot["code"].str.startswith(("sh", "sz"))]

    # Coerce numeric
    spot["price"] = pd.to_numeric(spot["price"], errors="coerce")
    spot["change_pct"] = pd.to_numeric(
        spot["change_pct"].astype(str).str.replace("%", "", regex=False),
        errors="coerce",
    )
    return spot.drop_duplicates(subset=["code"])


def fetch_info() -> pd.DataFrame:
    """Fetch static info from bond_zh_cov_info_ths."""
    logger.info("Fetching bond_zh_cov_info_ths ...")
    df = ak.bond_zh_cov_info_ths()
    logger.info("bond_zh_cov_info_ths returned %d rows, columns: %s", len(df), list(df.columns))
    info = _extract(df, _INFO_COL_MAP)

    # Normalize code
    info["code"] = info["code"].apply(_normalize_code)
    info = info.dropna(subset=["code"])
    info = info[info["code"].str.startswith(("sh", "sz"))]

    # Coerce numeric
    info["issue_size"] = pd.to_numeric(info["issue_size"], errors="coerce")
    info["conv_price"] = pd.to_numeric(info["conv_price"], errors="coerce")
    return info.drop_duplicates(subset=["code"])


def fetch_and_merge() -> list[dict]:
    """Fetch both APIs, merge, and return list of row dicts ready for DB upsert."""
    spot = fetch_spot()
    info = fetch_info()

    merged = spot.merge(info, on="code", how="left")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["updated_at"] = now_iso

    # Final column selection
    cols = ["code", "name", "price", "change_pct", "issue_size", "stock_code", "stock_name", "conv_price", "updated_at"]
    merged = merged[[c for c in cols if c in merged.columns]]
    for c in cols:
        if c not in merged.columns:
            merged[c] = None

    records = merged[cols].to_dict(orient="records")
    logger.info("Merged %d bond records", len(records))
    return records
