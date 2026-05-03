"""SQLite database setup and helpers."""

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "investpro.db"))

DDL = """
CREATE TABLE IF NOT EXISTS bonds (
    code        TEXT PRIMARY KEY,   -- 转债代码 (sh/sz)
    name        TEXT NOT NULL,      -- 转债名称
    price       REAL,               -- 最新价
    change_pct  REAL,               -- 涨跌幅 (%)
    issue_size  REAL,               -- 实际发行量 (亿元)
    stock_code  TEXT,               -- 正股代码
    stock_name  TEXT,               -- 正股名称
    conv_price  REAL,               -- 转股价
    conv_value  REAL,               -- 转股价值
    premium_rate REAL,              -- 转股溢价率 (%)
    updated_at  TEXT NOT NULL       -- 更新时间 (ISO8601)
);

CREATE TABLE IF NOT EXISTS refresh_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,      -- running / success / error
    message     TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    _allowed_migrations = {
        "conv_value": "REAL",
        "premium_rate": "REAL",
    }
    with get_conn() as conn:
        conn.executescript(DDL)
        # Migration: add conv_value / premium_rate columns to existing databases
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(bonds)").fetchall()}
        for col, typedef in _allowed_migrations.items():
            if col not in existing_cols:
                # col and typedef are from the hardcoded dict above, not user input
                conn.execute(f"ALTER TABLE bonds ADD COLUMN {col} {typedef}")


def upsert_bonds(rows: list[dict]) -> int:
    """Insert or replace bond rows. Returns count of rows written."""
    if not rows:
        return 0
    sql = """
        INSERT OR REPLACE INTO bonds
            (code, name, price, change_pct, issue_size, stock_code, stock_name,
             conv_price, conv_value, premium_rate, updated_at)
        VALUES
            (:code, :name, :price, :change_pct, :issue_size, :stock_code, :stock_name,
             :conv_price, :conv_value, :premium_rate, :updated_at)
    """
    with get_conn() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def query_bonds(
    search: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_change: float | None = None,
    max_change: float | None = None,
    sort_by: str = "code",
    sort_dir: str = "asc",
) -> list[dict]:
    # Use a whitelist mapping to prevent any risk of injection in ORDER BY
    allowed_sort = {
        "code": "code",
        "name": "name",
        "price": "price",
        "change_pct": "change_pct",
        "issue_size": "issue_size",
        "stock_code": "stock_code",
        "stock_name": "stock_name",
        "conv_price": "conv_price",
        "conv_value": "conv_value",
        "premium_rate": "premium_rate",
        "updated_at": "updated_at",
    }
    safe_sort = allowed_sort.get(sort_by, "code")
    order = "ASC" if sort_dir.lower() == "asc" else "DESC"

    clauses: list[str] = []
    params: list = []

    if search:
        clauses.append("(code LIKE ? OR name LIKE ? OR stock_code LIKE ? OR stock_name LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if min_price is not None:
        clauses.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        clauses.append("price <= ?")
        params.append(max_price)
    if min_change is not None:
        clauses.append("change_pct >= ?")
        params.append(min_change)
    if max_change is not None:
        clauses.append("change_pct <= ?")
        params.append(max_change)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM bonds {where} ORDER BY {safe_sort} {order}"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def insert_refresh_log(started_at: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO refresh_log (started_at, status) VALUES (?, 'running')",
            (started_at,),
        )
        return cur.lastrowid  # type: ignore[return-value]


def finish_refresh_log(log_id: int, finished_at: str, status: str, message: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE refresh_log SET finished_at=?, status=?, message=? WHERE id=?",
            (finished_at, status, message, log_id),
        )


def get_latest_refresh() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM refresh_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
