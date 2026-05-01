"""AkShare data fetching and merging logic.

Two APIs used:
  - bond_zh_hs_cov_spot  → real-time quote via Sina getHQNodeDataSimple
                           (returns English keys: symbol, name, trade, changepercent)
  - bond_zh_cov_info_ths → static info from THS
                           (returns: 债券代码, 正股代码, 正股简称, 实际发行量, 转股价格)

Only sh / sz prefixed codes are kept.

Note: bond_zh_cov_info_ths returns bare 6-digit codes (e.g. "127030") while
bond_zh_hs_cov_spot returns sh/sz-prefixed codes (e.g. "sz127030").  The
_normalize_code helper adds the correct prefix based on the code range so that
the two DataFrames can be joined on a common key.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

# Columns we need from each source (key = internal name, value = possible source column names in priority order)
# bond_zh_hs_cov_spot uses Sina's getHQNodeDataSimple endpoint which returns English JSON keys:
#   symbol (sh/sz-prefixed code), name, trade (latest price), changepercent (change %)
_SPOT_COL_MAP = {
    "code":       ["symbol", "代码"],
    "name":       ["name", "名称"],
    "price":      ["trade", "最新价", "现价", "price"],
    "change_pct": ["changepercent", "涨跌幅", "涨跌幅(%)"],
    # bond_zh_hs_cov_spot does not expose 正股名称; kept as fallback only
    "stock_name": ["正股名称"],
}

_INFO_COL_MAP = {
    # bond_zh_cov_info_ths returns "债券代码" (bare 6-digit code)
    "code":       ["债券代码", "转债代码", "代码"],
    "issue_size": ["实际发行量", "发行规模", "实际发行额", "发行量（亿元）", "发行量"],
    "stock_code": ["正股代码"],
    # THS interface uses "正股简称", some versions use "正股名称"
    "stock_name": ["正股名称", "正股简称"],
    # THS interface uses "转股价格"
    "conv_price": ["转股价格", "转股价"],
}

_SH_SZ_FULL_PATTERN = re.compile(r"^(sh|sz)\d{6}$", re.IGNORECASE)
# Shanghai convertible bonds start with "11"; Shenzhen ones start with "12", "13", or "18"
_BARE_SH_PATTERN = re.compile(r"^11\d{4}$")
_BARE_SZ_PATTERN = re.compile(r"^1[238]\d{4}$")


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
    """Return lowercase sh/sz prefixed code, or None if the exchange cannot be determined.

    Handles three input formats:
    - Already-prefixed codes: "sh110081", "SZ128XXX"   → lowercased as-is
    - Bare 6-digit SH codes:  "110081", "113XXX"        → "sh" + code
    - Bare 6-digit SZ codes:  "127XXX", "128XXX", "18x" → "sz" + code
    """
    code = str(code).strip()
    if _SH_SZ_FULL_PATTERN.match(code):
        return code.lower()
    if _BARE_SH_PATTERN.match(code):
        return "sh" + code
    if _BARE_SZ_PATTERN.match(code):
        return "sz" + code
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
    """Fetch static info from bond_zh_cov_info_ths.

    Returns an empty DataFrame (with expected columns) on any error so that
    fetch_and_merge can still produce results from spot data alone.
    """
    _empty = pd.DataFrame(columns=["code", "issue_size", "stock_code", "stock_name", "conv_price"])
    try:
        logger.info("Fetching bond_zh_cov_info_ths ...")
        df = ak.bond_zh_cov_info_ths()
        logger.info("bond_zh_cov_info_ths returned %d rows, columns: %s", len(df), list(df.columns))
    except Exception as exc:
        logger.warning("bond_zh_cov_info_ths fetch failed (%s). Continuing without info data.", exc)
        return _empty

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

    # Both DataFrames may carry a "stock_name" column (spot from 正股名称, info from
    # 正股名称/正股简称).  Use suffixes to keep them separate, then coalesce: prefer
    # the info value (more authoritative) and fall back to the spot value.
    merged = spot.merge(info, on="code", how="left", suffixes=("_spot", "_info"))

    # Coalesce stock_name: both, only one, or neither column may be present depending
    # on which AkShare columns were available in each API response.
    has_spot_sn = "stock_name_spot" in merged.columns
    has_info_sn = "stock_name_info" in merged.columns
    if has_spot_sn and has_info_sn:
        # Prefer info; fall back to spot when info is null
        merged["stock_name"] = merged["stock_name_info"].where(
            merged["stock_name_info"].notna(), merged["stock_name_spot"]
        )
        merged.drop(columns=["stock_name_spot", "stock_name_info"], inplace=True)
    elif has_spot_sn:
        merged.rename(columns={"stock_name_spot": "stock_name"}, inplace=True)
    elif has_info_sn:
        merged.rename(columns={"stock_name_info": "stock_name"}, inplace=True)
    # else: neither source had stock_name; the column will be added as None below

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
