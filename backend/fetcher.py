"""AkShare data fetching and merging logic.

Three APIs used:
  - bond_zh_hs_cov_spot        → real-time quote via Sina getHQNodeDataSimple
                                  (returns English keys: symbol, name, trade, changepercent)
  - bond_zh_cov_info_ths       → static info from THS
                                  (returns: 债券代码, 正股代码, 正股简称, 实际发行量, 转股价格)
  - stock_zh_a_sh_sz_spot_sina → real-time A-share spot prices (used to compute
                                  conversion value and premium rate)

Only sh / sz prefixed codes are kept.

Note: bond_zh_cov_info_ths returns bare 6-digit codes (e.g. "127030") while
bond_zh_hs_cov_spot returns sh/sz-prefixed codes (e.g. "sz127030").  The
_normalize_code helper adds the correct prefix based on the code range so that
the two DataFrames can be joined on a common key.

Conversion value formula:  conv_value  = (stock_price / conv_price) × 100
Premium rate formula:       premium_rate = (price − conv_value) / conv_value × 100
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

_STOCK_COL_MAP = {
    "code":        ["代码", "symbol"],
    "stock_price": ["最新价", "现价", "trade"],
}

_SH_SZ_FULL_PATTERN = re.compile(r"^(sh|sz)\d{6}$", re.IGNORECASE)
# Shanghai convertible bonds start with "11"; Shenzhen ones start with "12", "13", or "18"
_BARE_SH_PATTERN = re.compile(r"^11\d{4}$")
_BARE_SZ_PATTERN = re.compile(r"^1[238]\d{4}$")
# A-share stocks: Shanghai starts with "6"; Shenzhen starts with "0" or "3"
_BARE_STOCK_SH_PATTERN = re.compile(r"^6\d{5}$")
_BARE_STOCK_SZ_PATTERN = re.compile(r"^[03]\d{5}$")


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


def _normalize_stock_code(code: str) -> str | None:
    """Return lowercase sh/sz prefixed A-share stock code, or None if undetermined.

    Handles three input formats:
    - Already-prefixed codes: "sh600519", "SZ000001"    → lowercased as-is
    - Bare 6-digit SH codes:  "6XXXXX"                  → "sh" + code
    - Bare 6-digit SZ codes:  "0XXXXX", "3XXXXX"        → "sz" + code
    """
    code = str(code).strip()
    if _SH_SZ_FULL_PATTERN.match(code):
        return code.lower()
    if _BARE_STOCK_SH_PATTERN.match(code):
        return "sh" + code
    if _BARE_STOCK_SZ_PATTERN.match(code):
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

    # Drop bonds with no valid price
    spot = spot[spot["price"] > 0]

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


def fetch_stocks() -> pd.DataFrame:
    """Fetch A-share spot prices for use in conversion-value calculation.

    Returns a DataFrame with columns:
        stock_code_norm  – sh/sz-prefixed normalized stock code
        stock_price      – latest A-share price (positive values only)
    Returns an empty DataFrame on any fetch error.
    """
    _empty = pd.DataFrame(columns=["stock_code_norm", "stock_price"])
    try:
        logger.info("Fetching stock_zh_a_sh_sz_spot_sina ...")
        df = ak.stock_zh_a_sh_sz_spot_sina()
        logger.info("stock_zh_a_sh_sz_spot_sina returned %d rows", len(df))
    except Exception as exc:
        logger.warning(
            "stock_zh_a_sh_sz_spot_sina fetch failed (%s). Conversion values will be null.", exc
        )
        return _empty

    stocks = _extract(df, _STOCK_COL_MAP)
    stocks = stocks.rename(columns={"code": "stock_code_norm"})
    stocks["stock_code_norm"] = stocks["stock_code_norm"].apply(_normalize_stock_code)
    stocks = stocks.dropna(subset=["stock_code_norm"])
    stocks["stock_price"] = pd.to_numeric(stocks["stock_price"], errors="coerce")
    stocks = stocks[stocks["stock_price"] > 0]
    return stocks.drop_duplicates(subset=["stock_code_norm"])


def fetch_and_merge() -> list[dict]:
    """Fetch all APIs, merge, compute derived fields, and return row dicts for DB upsert."""
    spot = fetch_spot()
    info = fetch_info()
    stocks = fetch_stocks()

    # Add a normalized A-share stock code column for joining with the stocks table.
    # bond_zh_cov_info_ths returns bare 6-digit 正股代码 (e.g. "600519"); normalize to
    # "sh600519" / "sz000001" so they match the keys in the stocks DataFrame.
    info = info.copy()
    info["stock_code_norm"] = info["stock_code"].apply(_normalize_stock_code)
    info = info.merge(stocks, on="stock_code_norm", how="left")
    info.drop(columns=["stock_code_norm"], inplace=True)

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

    # ── Derived fields ────────────────────────────────────────────────────────
    # conv_value   = (正股股价 / 转股价) × 100
    # premium_rate = (转债现价 − conv_value) / conv_value × 100  (%)
    if all(c in merged.columns for c in ("stock_price", "conv_price", "price")):
        conv_price_safe = merged["conv_price"].where(merged["conv_price"] > 0)
        merged["conv_value"] = (merged["stock_price"] / conv_price_safe) * 100
        conv_value_safe = merged["conv_value"].where(merged["conv_value"] > 0)
        merged["premium_rate"] = (merged["price"] - conv_value_safe) / conv_value_safe * 100
    else:
        merged["conv_value"] = None
        merged["premium_rate"] = None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["updated_at"] = now_iso

    # Final column selection
    cols = [
        "code", "name", "price", "change_pct",
        "issue_size", "stock_code", "stock_name", "conv_price",
        "conv_value", "premium_rate", "updated_at",
    ]
    merged = merged[[c for c in cols if c in merged.columns]]
    for c in cols:
        if c not in merged.columns:
            merged[c] = None

    records = merged[cols].to_dict(orient="records")
    logger.info("Merged %d bond records", len(records))
    return records
