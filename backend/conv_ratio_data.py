#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
可转债累计转股比例数据接口

从上交所（SSE）和深交所（SZSE）官方网站获取各可转债的累计转股比例。

数据来源：
  - 上交所：https://www.sse.com.cn/market/bonddata/convertible/
    API 地址：https://query.sse.com.cn/commonSoaQuery.do
    sqlId：COMMON_BOND_ZQ_ZHZGZH_LIST
  - 深交所：https://www.szse.cn/market/bond/convertible/index.html
    API 地址：https://www.szse.cn/api/report/ShowReport/data
    CATALOGID：1028，TABKEY：tab2

返回结果：{bond_code (6位数字, 不含交易所前缀) -> 累计转股比例 (%, float)}
"""

from __future__ import annotations

import logging
from typing import Dict

import requests

logger = logging.getLogger(__name__)

_SSE_CONV_URL = "https://query.sse.com.cn/commonSoaQuery.do"
_SSE_HEADERS = {
    "Referer": "https://www.sse.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

_SZSE_CONV_URL = "https://www.szse.cn/api/report/ShowReport/data"
_SZSE_HEADERS = {
    "Referer": "https://www.szse.cn/market/bond/convertible/index.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Possible field names for bond code and cumulative conversion ratio in SSE response
_SSE_CODE_FIELDS = ["SECU_CODE", "BOND_ID", "BOND_CODE", "SECURITY_CODE", "secu_code", "bond_id"]
_SSE_RATIO_FIELDS = [
    "ACCUM_CONV_RATIO", "ACCUM_TRANSFER_RATIO", "CUM_CONV_RATIO",
    "CONVERT_RATE", "CONVERT_RATIO", "TRANSFER_RATIO",
    "accum_conv_ratio", "convert_rate", "convert_ratio",
]

# Possible field names in SZSE response
_SZSE_CODE_FIELDS = ["zqdm", "ZQDM", "jymc", "aqzqmc", "bond_id", "BOND_ID"]
_SZSE_RATIO_FIELDS = [
    "ljzgbl", "LJZGBL", "zsgbl", "ZSGBL",
    "ACCUM_CONV_RATIO", "CONVERT_RATIO", "ljzgb",
]


def _first_existing(d: dict, candidates: list[str]) -> str | None:
    for k in candidates:
        if k in d:
            return k
    return None


def _fetch_sse_conv_ratios() -> Dict[str, float]:
    """从上交所 API 获取所有可转债的累计转股比例。

    :return: {6位债券代码 -> 累计转股比例 (%)}
    """
    result: Dict[str, float] = {}
    page_no = 1
    page_size = 200
    try:
        while True:
            params = {
                "sqlId": "COMMON_BOND_ZQ_ZHZGZH_LIST",
                "isPagination": "true",
                "pageHelp.pageSize": str(page_size),
                "pageHelp.pageNo": str(page_no),
                "pageHelp.beginPage": str(page_no),
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": str(page_no + 5),
            }
            r = requests.get(_SSE_CONV_URL, headers=_SSE_HEADERS, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()

            # Handle different response shapes
            rows = None
            if isinstance(data, dict):
                page_help = data.get("pageHelp", {})
                if isinstance(page_help, dict):
                    rows = page_help.get("data", [])
                if not rows:
                    rows = data.get("result", data.get("data", []))
            elif isinstance(data, list):
                rows = data

            if not rows:
                logger.debug("SSE conv ratio API returned empty rows on page %d", page_no)
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                code_field = _first_existing(row, _SSE_CODE_FIELDS)
                ratio_field = _first_existing(row, _SSE_RATIO_FIELDS)
                if not code_field or not ratio_field:
                    continue
                code = str(row[code_field]).strip().lstrip("0").zfill(6)
                try:
                    ratio = float(row[ratio_field])
                    if ratio > 0:
                        result[code] = ratio
                except (ValueError, TypeError):
                    pass

            # Pagination: check if there's a next page
            if isinstance(data, dict):
                page_help = data.get("pageHelp", {})
                total = page_help.get("total", 0) if isinstance(page_help, dict) else 0
                if total and page_no * page_size >= total:
                    break
                if not rows or len(rows) < page_size:
                    break
            else:
                if not rows or len(rows) < page_size:
                    break
            page_no += 1

    except Exception as exc:
        logger.warning("SSE cumulative conversion ratio fetch failed: %s", exc)

    logger.info("SSE: fetched cumulative conv ratios for %d bonds", len(result))
    return result


def _fetch_szse_conv_ratios() -> Dict[str, float]:
    """从深交所 API 获取所有可转债的累计转股比例。

    :return: {6位债券代码 -> 累计转股比例 (%)}
    """
    result: Dict[str, float] = {}
    page_no = 1
    page_size = 200
    try:
        while True:
            params = {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1028",
                "TABKEY": "tab2",
                "tab2PAGENO": str(page_no),
                "tab2PAGESIZE": str(page_size),
                "random": "0.1234567890",
            }
            r = requests.get(_SZSE_CONV_URL, headers=_SZSE_HEADERS, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()

            rows = None
            # SZSE ShowReport/data often returns a list of tab data
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    rows = first.get("data", first.get("rows", []))
            elif isinstance(data, dict):
                rows = data.get("data", data.get("rows", []))

            if not rows:
                logger.debug("SZSE conv ratio API returned empty rows on page %d", page_no)
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                code_field = _first_existing(row, _SZSE_CODE_FIELDS)
                ratio_field = _first_existing(row, _SZSE_RATIO_FIELDS)
                if not code_field or not ratio_field:
                    continue
                code = str(row[code_field]).strip().lstrip("0").zfill(6)
                try:
                    ratio_raw = str(row[ratio_field]).replace("%", "").strip()
                    ratio = float(ratio_raw)
                    if ratio > 0:
                        result[code] = ratio
                except (ValueError, TypeError):
                    pass

            if not rows or len(rows) < page_size:
                break
            page_no += 1

    except Exception as exc:
        logger.warning("SZSE cumulative conversion ratio fetch failed: %s", exc)

    logger.info("SZSE: fetched cumulative conv ratios for %d bonds", len(result))
    return result


def fetch_cumulative_conv_ratios() -> Dict[str, float]:
    """从上交所和深交所获取所有可转债的累计转股比例。

    :return: {6位债券代码 -> 累计转股比例 (%，0~100之间)}
    """
    sse = _fetch_sse_conv_ratios()
    szse = _fetch_szse_conv_ratios()
    merged = {**sse, **szse}
    logger.info("Combined cumulative conv ratios: %d bonds", len(merged))
    return merged


if __name__ == "__main__":
    import json
    ratios = fetch_cumulative_conv_ratios()
    print(f"Total bonds with conv ratio: {len(ratios)}")
    sample = dict(list(ratios.items())[:10])
    print(json.dumps(sample, ensure_ascii=False, indent=2))
