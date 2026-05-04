"""AkShare data fetching and merging logic.

Two APIs used:
  - bond_zh_hs_cov_spot        → real-time quote via Sina getHQNodeDataSimple
                                  (returns English keys: symbol, name, trade, changepercent)
  - bond_zh_cov                → comprehensive convertible bond data from East Money
                                  (returns: 债券代码, 债券简称, 正股代码, 正股简称, 正股价,
                                   转股价, 债现价, 发行规模 — covers ALL currently listed bonds)

Only sh / sz prefixed codes are kept.

Note: bond_zh_cov returns bare 6-digit codes (e.g. "127030") while
bond_zh_hs_cov_spot returns sh/sz-prefixed codes (e.g. "sz127030").  The
_normalize_code helper adds the correct prefix based on the code range so that
the two DataFrames can be joined on a common key.

Root cause of the sh110081 bug
──────────────────────────────
The original code used `spot.merge(info, on="code", how="left")` — a LEFT JOIN
with Sina's bond_zh_hs_cov_spot as the *primary* (left) table.  Bonds that are
absent from Sina's listing (e.g. 闻泰转债 sh110081 when it enters a mandatory-
redemption period or is otherwise excluded from Sina's node) are silently
dropped from the merged result, even though the static info data (THS / East
Money) is available and conv_value / premium_rate *could* be computed.

Fix
───
• Use `bond_zh_cov` (East Money) as the canonical info source; it covers ALL
  currently listed convertible bonds and directly provides 正股价 (stock price)
  and 债现价 (current bond price), making a separate stock-price lookup and a
  Sina dependency unnecessary for the core calculations.
• Flip the merge to `info.merge(spot, how="left")` so every bond known to East
  Money appears in the result.  Sina's real-time price and change_pct are added
  where available; bonds absent from Sina fall back to East Money's 债现价 for
  the bond price so that both conv_value *and* premium_rate can still be
  computed.

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
    # bond_zh_cov returns "债券代码" (bare 6-digit code)
    "code":        ["债券代码", "转债代码", "代码"],
    # bond name – used as fallback when Sina spot does not include this bond
    "name":        ["债券简称", "债券名称"],
    "issue_size":  ["发行规模", "实际发行量", "发行量（亿元）", "发行量"],
    "stock_code":  ["正股代码"],
    "stock_name":  ["正股名称", "正股简称"],
    "conv_price":  ["转股价", "转股价格"],
    # bond_zh_cov provides the underlying stock price directly
    "stock_price": ["正股价", "最新价", "现价"],
    # bond_zh_cov provides 债现价 – used as fallback bond price for bonds absent from Sina
    "bond_price":  ["债现价"],
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
    """Fetch comprehensive convertible bond info from East Money (bond_zh_cov).

    This covers ALL currently listed convertible bonds — including older ones
    that no longer appear on THS's IPO-centric endpoint — and directly provides
    the underlying stock price (正股价), eliminating the need for a separate
    stock price lookup.

    Returns an empty DataFrame (with expected columns) on any error so that
    fetch_and_merge can still produce results from spot data alone.
    """
    _empty = pd.DataFrame(columns=list(_INFO_COL_MAP.keys()))
    try:
        logger.info("Fetching bond_zh_cov ...")
        df = ak.bond_zh_cov()
        logger.info("bond_zh_cov returned %d rows, columns: %s", len(df), list(df.columns))
    except Exception as exc:
        logger.warning("bond_zh_cov fetch failed (%s). Continuing without info data.", exc)
        return _empty

    info = _extract(df, _INFO_COL_MAP)

    # Normalize bond code
    info["code"] = info["code"].apply(_normalize_code)
    info = info.dropna(subset=["code"])
    info = info[info["code"].str.startswith(("sh", "sz"))]

    # Coerce numeric
    info["issue_size"] = pd.to_numeric(info["issue_size"], errors="coerce")
    info["conv_price"] = pd.to_numeric(info["conv_price"], errors="coerce")
    info["stock_price"] = pd.to_numeric(info["stock_price"], errors="coerce")
    info["bond_price"] = pd.to_numeric(info["bond_price"], errors="coerce")
    return info.drop_duplicates(subset=["code"])


def fetch_and_merge() -> list[dict]:
    """Fetch all APIs, merge, compute derived fields, and return row dicts for DB upsert."""
    spot = fetch_spot()
    info = fetch_info()

    # ── Merge: info (East Money) is the LEFT table ────────────────────────────
    # This is the key fix: using spot as the left table caused bonds absent from
    # Sina's listing (e.g. bonds under forced redemption) to be silently dropped
    # even though East Money had their data.  With info as the primary table
    # every bond known to East Money appears; spot data is added where available.
    merged = info.merge(spot, on="code", how="left", suffixes=("_info", "_spot"))

    # Prefer Sina name; fall back to East Money 债券简称 for bonds absent from Sina.
    _coalesce_suffixed(merged, "name", preferred_suffix="spot", fallback_suffix="info")

    # Prefer info stock_name (more authoritative); fall back to spot.
    _coalesce_suffixed(merged, "stock_name", preferred_suffix="info", fallback_suffix="spot")

    # ── Coalesce bond price ───────────────────────────────────────────────────
    # "price" comes from Sina spot (real-time).  For bonds absent from Sina,
    # fall back to East Money's 债现价 so that premium_rate can still be computed.
    if "price" in merged.columns and "bond_price" in merged.columns:
        merged["price"] = merged["price"].where(
            merged["price"].notna(), merged["bond_price"]
        )
        merged.drop(columns=["bond_price"], inplace=True)
    elif "bond_price" in merged.columns:
        merged.rename(columns={"bond_price": "price"}, inplace=True)

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
