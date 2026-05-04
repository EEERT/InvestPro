#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
沪深A股实时行情接口（仅 SH + SZ，不含 BJ），数据来源：新浪财经

特点：
1) 分别抓取 sh_a 和 sz_a，避免 hs_a 节点不稳定导致数据不全
2) 自动分页抓取（每页 80 条）
3) 网络请求带重试和超时
4) 过滤 bj 开头代码（双保险）
5) 自动将常见数值字段转为数值类型

外部依赖：requests, pandas, demjson3
    pip install requests pandas demjson3
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 新浪行情接口地址
# ---------------------------------------------------------------------------
SINA_HQ_NODE_DATA_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeDataSimple"
)
SINA_HQ_NODE_COUNT_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeStockCountSimple"
)

BASE_PAYLOAD = {
    "page": "1",
    "num": "80",
    "sort": "symbol",
    "asc": "1",
    "_s_r_a": "page",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _decode_sina_text(text: str):
    """
    解码新浪返回内容（可能是非严格 JSON）。
    优先 demjson3，其次 demjson，最后 json 兜底。
    """
    try:
        import demjson3  # type: ignore
        return demjson3.decode(text)
    except Exception:
        pass

    try:
        import demjson  # type: ignore
        return demjson.decode(text)
    except Exception:
        pass

    import json
    fixed = text.strip()
    fixed = re.sub(r"([{,]\s*)([A-Za-z_]\w*)(\s*:)", r'\1"\2"\3', fixed)
    fixed = fixed.replace("'", '"')
    return json.loads(fixed)


def _request_with_retry(
    session: requests.Session,
    url: str,
    params: Dict,
    timeout: int = 20,
    retry: int = 3,
    retry_sleep: float = 0.8,
) -> requests.Response:
    """带重试的 GET 请求。"""
    last_err: Optional[Exception] = None
    for i in range(retry):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            if i < retry - 1:
                time.sleep(retry_sleep * (i + 1))
    raise RuntimeError(f"请求失败: {url}, params={params}, error={last_err}")


def _extract_total_count(text: str) -> int:
    """从 count 接口返回文本中提取总数量。"""
    nums = re.findall(r"\d+", text)
    if not nums:
        raise ValueError(f"未能从返回中提取总数，response={text}")
    return int(nums[0])


def _get_page_count(
    session: requests.Session,
    node: str,
    page_size: int = 80,
    retry: int = 3,
) -> int:
    resp = _request_with_retry(
        session=session,
        url=SINA_HQ_NODE_COUNT_URL,
        params={"node": node},
        timeout=15,
        retry=retry,
    )
    total = _extract_total_count(resp.text)
    return (total + page_size - 1) // page_size


def _fetch_node_data(
    session: requests.Session,
    node: str,
    page_size: int = 80,
    delay: float = 0.05,
    retry: int = 3,
    verbose: bool = True,
) -> pd.DataFrame:
    """抓取单个节点（sh_a 或 sz_a）的全部分页数据。"""
    page_count = _get_page_count(session=session, node=node, page_size=page_size, retry=retry)
    if verbose:
        print(f"[{node}] total pages: {page_count}")

    if page_count <= 0:
        return pd.DataFrame()

    payload = BASE_PAYLOAD.copy()
    payload["node"] = node
    payload["num"] = str(page_size)

    frames: List[pd.DataFrame] = []
    for page in range(1, page_count + 1):
        payload["page"] = str(page)
        resp = _request_with_retry(
            session=session,
            url=SINA_HQ_NODE_DATA_URL,
            params=payload,
            timeout=20,
            retry=retry,
        )

        data = _decode_sina_text(resp.text)
        df_page = pd.DataFrame(data) if data else pd.DataFrame()

        if not df_page.empty:
            frames.append(df_page)

        if verbose and (page == 1 or page % 20 == 0 or page == page_count):
            print(f"[{node}] page {page}/{page_count}, rows={len(df_page)}")

        if delay > 0:
            time.sleep(delay)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗：
    1) 过滤 symbol，仅保留 sh/sz，剔除 bj
    2) 去重
    3) 常见数值列转 numeric
    """
    if df.empty:
        return df

    df = df.copy()

    if "symbol" in df.columns:
        symbol_str = df["symbol"].astype(str).str.lower()
        df = df[symbol_str.str.startswith(("sh", "sz"))]
        df = df[~symbol_str.str.startswith("bj")]

    if "symbol" in df.columns:
        df = df.drop_duplicates(subset=["symbol"], keep="first")

    numeric_candidates = [
        "trade", "pricechange", "changepercent", "buy", "sell",
        "settlement", "open", "high", "low", "volume", "amount",
        "per", "pb", "mktcap", "nmc", "turnoverratio",
    ]
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 对外主函数
# ---------------------------------------------------------------------------
def stock_zh_a_sh_sz_spot_sina(
    delay: float = 0.05,
    retry: int = 3,
    timeout_adapter: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    新浪财经-沪深A股实时行情（仅 SH + SZ，不含 BJ）。

    返回 DataFrame 字段（新浪原始英文字段）：
      symbol, name, trade, pricechange, changepercent, buy, sell,
      settlement, open, high, low, volume, amount, ...

    :param delay: 分页请求间隔（秒），建议 0.03~0.2
    :param retry: 每次请求失败重试次数
    :param timeout_adapter: 是否开启 HTTPAdapter 连接池
    :param verbose: 是否打印进度
    :return: pandas.DataFrame
    """
    with requests.Session() as session:
        if timeout_adapter:
            adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        df_sh = _fetch_node_data(
            session=session, node="sh_a", delay=delay, retry=retry, verbose=verbose
        )
        df_sz = _fetch_node_data(
            session=session, node="sz_a", delay=delay, retry=retry, verbose=verbose
        )

    df = pd.concat([df_sh, df_sz], ignore_index=True)
    df = _clean_dataframe(df)
    return df


# ---------------------------------------------------------------------------
# 脚本入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    df_all = stock_zh_a_sh_sz_spot_sina(delay=0.05, retry=3, verbose=True)

    print("\n=== 抓取完成 ===")
    print("总条数:", len(df_all))

    if "symbol" in df_all.columns:
        s = df_all["symbol"].astype(str).str.lower()
        print("sh 条数:", int(s.str.startswith("sh").sum()))
        print("sz 条数:", int(s.str.startswith("sz").sum()))
        print("bj 条数:", int(s.str.startswith("bj").sum()))

    print("字段列表:", df_all.columns.tolist())
    print(df_all.head(10))

    out_file = "sina_stock_sh_sz_spot.csv"
    df_all.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"\n已保存到: {out_file}")
