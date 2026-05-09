"""Data fetching and merging logic (no akshare dependency).

APIs used:
  - bond_zh_hs_cov_spot         → Sina real-time quotes (base; only currently active bonds appear
                                   here, so delisted bonds are automatically excluded).
                                   Returns English keys: symbol, name, trade, changepercent.
  - bond_zh_cov_info_ths        → THS (同花顺) convertible bond static info: stock code,
                                   stock name, conv_price, issue_size, etc.  Joined onto
                                   the Sina bond list by bond code.
  - stock_zh_a_sh_sz_spot_sina  → Sina A-share real-time quotes (SH + SZ only).
                                   Filtered to only the underlying stocks referenced by the
                                   merged bond list and used exclusively for stock_price,
                                   which feeds the conv_value / premium_rate calculation.
                                   Also provides nmc (流通市值, in 万元) for bond_ratio.
  - fetch_cumulative_conv_ratios → SSE + SZSE official websites cumulative conversion ratio
                                   per bond, used to compute remaining_size.

Merge strategy
──────────────
  spot  (Sina)   ← LEFT table — defines the universe of *active* bonds
    LEFT JOIN info (THS) on bond code      → adds conv_price, stock_code, issue_size
    LEFT JOIN stocks (Sina) on stock_code  → adds stock_price, nmc
    LEFT JOIN conv_ratios (SSE/SZSE) on bare bond code → adds cumulative conv ratio

Conversion value formula:  conv_value    = (stock_price / conv_price) × 100
Premium rate formula:      premium_rate  = (price − conv_value) / conv_value × 100
Remaining size formula:    remaining_size = issue_size × (1 − cumulative_conv_ratio / 100)
Bond ratio formula:        bond_ratio    = remaining_size / (nmc / 10000) × 100  (%)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import pandas as pd

from bond_data import bond_zh_cov_info_ths, bond_zh_hs_cov_spot
from conv_ratio_data import fetch_cumulative_conv_ratios
from stock_data import stock_zh_a_sh_sz_spot_sina

logger = logging.getLogger(__name__)

# Columns we need from each source (key = internal name, value = possible source column names in priority order)
# bond_zh_hs_cov_spot uses Sina's getHQNodeDataSimple endpoint which returns English JSON keys:
#   symbol (sh/sz-prefixed code), name, trade (latest price), changepercent (change %)
_SPOT_COL_MAP = {
    "code":       ["symbol", "代码"],
    "name":       ["name", "名称"],
    "price":      ["trade", "最新价", "现价", "price"],
    "change_pct": ["changepercent", "涨跌幅", "涨跌幅(%)"],
}

# bond_zh_cov_info_ths (THS 同花顺) convertible bond static info
_INFO_COL_MAP = {
    "code":       ["转债代码", "债券代码", "代码"],
    "name":       ["转债名称", "债券名称", "债券简称"],
    "issue_size": ["发行规模(亿元)", "发行规模", "实际发行量", "发行量（亿元）", "发行量"],
    "stock_code":  ["正股代码"],
    "stock_name":  ["正股简称", "正股名称"],
    "conv_price":  ["转股价", "转股价格"],
    "expire_date": ["到期时间"],
}

# stock_zh_a_sh_sz_spot_sina (Sina A-share real-time quotes) — used for stock_price / stock_change_pct / nmc
# Sina returns English field names: symbol (e.g. "sh600000"), trade (latest price), changepercent, nmc (流通市值 万元)
_STOCK_COL_MAP = {
    "stock_code":       ["symbol"],
    "stock_price":      ["trade", "最新价", "现价", "price"],
    "stock_change_pct": ["changepercent", "涨跌幅"],
    "nmc":              ["nmc"],
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


def _coalesce_suffixed(
    df: pd.DataFrame, field: str, preferred_suffix: str, fallback_suffix: str
) -> None:
    """Merge two suffixed columns into *field* in-place.

    Prefers the *preferred_suffix* column value; falls back to *fallback_suffix*
    when the preferred value is null.  Both suffixed columns are then dropped.
    If only one of the two is present, it is simply renamed to *field*.
    """
    pref = f"{field}_{preferred_suffix}"
    fall = f"{field}_{fallback_suffix}"
    if pref in df.columns and fall in df.columns:
        df[field] = df[pref].fillna(df[fall])
        df.drop(columns=[pref, fall], inplace=True)
    elif pref in df.columns:
        df.rename(columns={pref: field}, inplace=True)
    elif fall in df.columns:
        df.rename(columns={fall: field}, inplace=True)


def fetch_spot() -> pd.DataFrame:
    """Fetch real-time quote data from bond_zh_hs_cov_spot."""
    logger.info("Fetching bond_zh_hs_cov_spot ...")
    df = bond_zh_hs_cov_spot()
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
    """Fetch convertible bond static info from THS (bond_zh_cov_info_ths).

    Returns: code, name, issue_size, stock_code, stock_name, conv_price.
    Returns an empty DataFrame (with expected columns) on any error.
    """
    _empty = pd.DataFrame(columns=list(_INFO_COL_MAP.keys()))
    try:
        logger.info("Fetching bond_zh_cov_info_ths ...")
        df = bond_zh_cov_info_ths()
        logger.info("bond_zh_cov_info_ths returned %d rows, columns: %s", len(df), list(df.columns))
    except Exception as exc:
        logger.warning("bond_zh_cov_info_ths fetch failed (%s). Continuing without info data.", exc)
        return _empty

    info = _extract(df, _INFO_COL_MAP)

    # Normalize bond code
    info["code"] = info["code"].apply(_normalize_code)
    info = info.dropna(subset=["code"])
    info = info[info["code"].str.startswith(("sh", "sz"))]

    # Coerce numeric
    info["issue_size"] = pd.to_numeric(info["issue_size"], errors="coerce")
    info["conv_price"] = pd.to_numeric(info["conv_price"], errors="coerce")

    # Normalize stock_code to bare 6-digit string
    info["stock_code"] = info["stock_code"].astype(str).str.strip().str.zfill(6)

    return info.drop_duplicates(subset=["code"])


def fetch_stock_prices(stock_codes: list[str]) -> pd.DataFrame:
    """Fetch current A-share prices and circulating market cap for the given bare 6-digit stock codes.

    Uses stock_zh_a_sh_sz_spot_sina (Sina) and filters to the requested codes.
    The Sina data has sh/sz-prefixed symbols (e.g. "sh600000"); these are stripped
    to bare 6-digit codes before matching against the bond info stock_code column.
    Returns a DataFrame with columns: stock_code, stock_price, stock_change_pct, nmc.
    nmc is Sina's 流通市值 field (in 万元).
    Returns an empty DataFrame on any error.
    """
    _empty = pd.DataFrame(columns=["stock_code", "stock_price", "stock_change_pct", "nmc"])
    if not stock_codes:
        return _empty
    try:
        logger.info("Fetching stock_zh_a_sh_sz_spot_sina ...")
        df = stock_zh_a_sh_sz_spot_sina(verbose=False)
        logger.info("stock_zh_a_sh_sz_spot_sina returned %d rows", len(df))
    except Exception as exc:
        logger.warning("stock_zh_a_sh_sz_spot_sina fetch failed (%s). Stock prices unavailable.", exc)
        return _empty

    stocks = _extract(df, _STOCK_COL_MAP)
    # Strip the sh/sz exchange prefix and left-pad to 6 digits so the code
    # matches the bare 6-digit stock_code coming from bond_zh_cov_info_ths.
    stocks["stock_code"] = (
        stocks["stock_code"]
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"^(sh|sz)", "", regex=True)
        .str.zfill(6)
    )
    stocks["stock_price"] = pd.to_numeric(stocks["stock_price"], errors="coerce")
    stocks["stock_change_pct"] = pd.to_numeric(stocks["stock_change_pct"], errors="coerce")
    stocks["nmc"] = pd.to_numeric(stocks["nmc"], errors="coerce")

    # Filter to only the codes we need
    code_set = set(stock_codes)
    stocks = stocks[stocks["stock_code"].isin(code_set)]
    return stocks.drop_duplicates(subset=["stock_code"])


def fetch_and_merge() -> list[dict]:
    """Fetch all APIs, merge, compute derived fields, and return row dicts for DB upsert."""
    spot = fetch_spot()
    info = fetch_info()

    # ── Merge 1: spot (Sina) LEFT JOIN info (THS) on bond code ────────────────
    # spot is the LEFT table: only bonds currently active on Sina appear in the
    # result, which automatically excludes delisted bonds.
    merged = spot.merge(info, on="code", how="left", suffixes=("_spot", "_info"))

    # Prefer spot name; fall back to THS name
    _coalesce_suffixed(merged, "name", preferred_suffix="spot", fallback_suffix="info")

    # stock_name comes from THS info only (spot does not expose it)
    if "stock_name_info" in merged.columns:
        merged.rename(columns={"stock_name_info": "stock_name"}, inplace=True)
    if "stock_name_spot" in merged.columns:
        merged.drop(columns=["stock_name_spot"], inplace=True)

    # ── Merge 2: result LEFT JOIN A-share real-time quotes on stock_code ───────
    # Collect the unique stock codes so we only pull the rows we need.
    stock_codes = []
    if "stock_code" in merged.columns:
        stock_codes = merged["stock_code"].dropna().unique().tolist()

    stocks = fetch_stock_prices(stock_codes)
    if not stocks.empty:
        merged = merged.merge(stocks, on="stock_code", how="left")
    else:
        merged["stock_price"] = None
        merged["stock_change_pct"] = None
        merged["nmc"] = None

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

    # ── Fetch cumulative conversion ratios from SSE and SZSE ─────────────────
    # remaining_size = issue_size × (1 − cumulative_conv_ratio / 100)
    try:
        conv_ratios = fetch_cumulative_conv_ratios()
    except Exception as exc:
        logger.warning("fetch_cumulative_conv_ratios failed: %s", exc)
        conv_ratios = {}

    if conv_ratios and "code" in merged.columns and "issue_size" in merged.columns:
        # Extract bare 6-digit code from sh/sz-prefixed code for lookup
        bare_codes = (
            merged["code"]
            .astype(str)
            .str.replace(r"^(sh|sz)", "", regex=True)
            .str.zfill(6)
        )
        cum_ratios = bare_codes.map(conv_ratios)
        # remaining_size = issue_size × (1 − cumulative_conv_ratio / 100)
        issue_size_safe = pd.to_numeric(merged["issue_size"], errors="coerce")
        cum_ratio_pct = pd.to_numeric(cum_ratios, errors="coerce").clip(lower=0, upper=100)
        merged["remaining_size"] = issue_size_safe * (1 - cum_ratio_pct / 100)
    else:
        # When conversion ratio data is unavailable, use issue_size as-is
        merged["remaining_size"] = pd.to_numeric(
            merged.get("issue_size"), errors="coerce"
        ) if "issue_size" in merged.columns else None

    # ── bond_ratio = remaining_size / 流通市值 (亿元) × 100 (%) ──────────────
    # nmc is Sina's 流通市值 in 万元; convert to 亿元 by dividing by 10000.
    if "nmc" in merged.columns and "remaining_size" in merged.columns:
        nmc_100m = pd.to_numeric(merged["nmc"], errors="coerce") / 10000  # 万元 → 亿元
        nmc_safe = nmc_100m.where(nmc_100m > 0)
        remaining_safe = pd.to_numeric(merged["remaining_size"], errors="coerce")
        merged["bond_ratio"] = remaining_safe / nmc_safe * 100
    else:
        merged["bond_ratio"] = None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["updated_at"] = now_iso

    # Convert expire_date (datetime.date) to ISO string
    if "expire_date" in merged.columns:
        merged["expire_date"] = merged["expire_date"].apply(
            lambda x: x.isoformat() if hasattr(x, "isoformat") else None
        )

    # Final column selection
    cols = [
        "code", "name", "price", "change_pct",
        "issue_size", "remaining_size", "stock_code", "stock_name",
        "stock_price", "stock_change_pct",
        "conv_price", "conv_value", "premium_rate",
        "bond_ratio", "expire_date", "updated_at",
    ]
    merged = merged[[c for c in cols if c in merged.columns]]
    for c in cols:
        if c not in merged.columns:
            merged[c] = None

    records = merged[cols].to_dict(orient="records")
    logger.info("Merged %d bond records", len(records))
    return records
