#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
可转债数据接口（从 akshare 提取，独立运行，不依赖 akshare 其他模块）

提取来源：
  - akshare/bond/bond_zh_cov.py  → bond_zh_hs_cov_spot
  - akshare/bond/bond_cb_ths.py  → bond_zh_cov_info_ths

外部依赖：requests, pandas, demjson3
    pip install requests pandas demjson3
"""

from __future__ import annotations

import re

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 常量（原 akshare/bond/cons.py）
# ---------------------------------------------------------------------------
_BOND_HS_COV_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeDataSimple"
)
_BOND_HS_COV_COUNT_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeStockCountSimple"
)
_BOND_HS_COV_PAYLOAD = {
    "page": "1",
    "num": "80",
    "sort": "symbol",
    "asc": "1",
    "node": "hskzz_z",
    "_s_r_a": "page",
}


# ---------------------------------------------------------------------------
# 工具：解码新浪返回的非严格 JSON
# ---------------------------------------------------------------------------
def _decode_sina_json(text: str):
    """优先使用 demjson3，其次 json 兜底。"""
    try:
        import demjson3  # type: ignore
        return demjson3.decode(text)
    except Exception:
        pass
    import json
    fixed = re.sub(r"([{,]\s*)([A-Za-z_]\w*)(\s*:)", r'\1"\2"\3', text.strip())
    fixed = fixed.replace("'", '"')
    return json.loads(fixed)


# ---------------------------------------------------------------------------
# bond_zh_hs_cov_spot — 新浪财经沪深可转债实时行情
# ---------------------------------------------------------------------------
def _get_zh_bond_hs_cov_page_count() -> int:
    """返回沪深可转债行情的总页数（每页 80 条）。"""
    r = requests.get(_BOND_HS_COV_COUNT_URL, params={"node": "hskzz_z"}, timeout=15)
    r.raise_for_status()
    total = int(re.findall(r"\d+", r.text)[0])
    pages = total / 80
    return int(pages) if pages == int(pages) else int(pages) + 1


def bond_zh_hs_cov_spot() -> pd.DataFrame:
    """
    新浪财经-债券-沪深可转债实时行情数据。

    :return: 所有沪深可转债在当前时刻的实时行情（含 symbol, name, trade, changepercent 等字段）
    :rtype: pandas.DataFrame
    """
    page_count = _get_zh_bond_hs_cov_page_count()
    payload = _BOND_HS_COV_PAYLOAD.copy()
    frames: list[pd.DataFrame] = []
    for page in range(1, page_count + 1):
        payload["page"] = str(page)
        res = requests.get(_BOND_HS_COV_URL, params=payload, timeout=20)
        res.raise_for_status()
        data = _decode_sina_json(res.text)
        if data:
            frames.append(pd.DataFrame(data))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# bond_zh_cov_info_ths — 同花顺可转债静态信息
# ---------------------------------------------------------------------------
def bond_zh_cov_info_ths() -> pd.DataFrame:
    """
    同花顺-数据中心-可转债静态信息。
    https://data.10jqka.com.cn/ipo/kzz/

    :return: 可转债基本信息（含债券代码、正股代码、转股价格、发行量等）
    :rtype: pandas.DataFrame
    """
    url = "https://data.10jqka.com.cn/ipo/kzz/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"
        ),
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    data_json = r.json()
    temp_df = pd.DataFrame(data_json["list"])
    temp_df.rename(
        columns={
            "sub_date": "申购日期",
            "bond_code": "债券代码",
            "bond_name": "债券简称",
            "code": "正股代码",
            "name": "正股简称",
            "sub_code": "申购代码",
            "share_code": "原股东配售码",
            "sign_date": "中签公布日",
            "plan_total": "计划发行量",
            "issue_total": "实际发行量",
            "issue_price": "-",
            "success_rate": "中签率",
            "listing_date": "上市日期",
            "expire_date": "到期时间",
            "price": "转股价格",
            "quota": "每股获配额",
            "number": "中签号",
            "market_id": "-",
            "stock_market_id": "-",
        },
        inplace=True,
    )
    temp_df = temp_df[
        [
            "债券代码",
            "债券简称",
            "申购日期",
            "申购代码",
            "原股东配售码",
            "每股获配额",
            "计划发行量",
            "实际发行量",
            "中签公布日",
            "中签号",
            "上市日期",
            "正股代码",
            "正股简称",
            "转股价格",
            "到期时间",
            "中签率",
        ]
    ]
    temp_df["申购日期"] = pd.to_datetime(temp_df["申购日期"], format="%Y-%m-%d", errors="coerce").dt.date
    temp_df["中签公布日"] = pd.to_datetime(temp_df["中签公布日"], format="%Y-%m-%d", errors="coerce").dt.date
    temp_df["上市日期"] = pd.to_datetime(temp_df["上市日期"], format="%Y-%m-%d", errors="coerce").dt.date
    temp_df["到期时间"] = pd.to_datetime(temp_df["到期时间"], format="%Y-%m-%d", errors="coerce").dt.date
    temp_df["每股获配额"] = pd.to_numeric(temp_df["每股获配额"], errors="coerce")
    temp_df["计划发行量"] = pd.to_numeric(temp_df["计划发行量"], errors="coerce")
    temp_df["实际发行量"] = pd.to_numeric(temp_df["实际发行量"], errors="coerce")
    temp_df["转股价格"] = pd.to_numeric(temp_df["转股价格"], errors="coerce")
    return temp_df


# ---------------------------------------------------------------------------
# 使用示例
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== bond_zh_hs_cov_spot ===")
    spot_df = bond_zh_hs_cov_spot()
    print(f"行数: {len(spot_df)}, 字段: {spot_df.columns.tolist()}")
    print(spot_df.head(5))

    print("\n=== bond_zh_cov_info_ths ===")
    info_df = bond_zh_cov_info_ths()
    print(f"行数: {len(info_df)}, 字段: {info_df.columns.tolist()}")
    print(info_df.head(5))
