"""交易日日期形态互转与 MongoDB 区间过滤。

`stock_daily_quotes.trade_date` 历史上两种形态并存：写入器
（HistoricalDataService._format_date）产出 ``YYYY-MM-DD``，而早期一次性补数
脚本按 ``YYYYMMDD`` 直插。BSON 字符串比较只看字节序，两种形态混在同一区间里
会整体错位——``"2026-09-01" < "20260901"``（第 5 个字符 '-' < '0'），于是
``{"$gte": "20260901"}`` 会把所有规范形记录排除掉，反之亦然。

统一原则：写入侧只产 ``YYYY-MM-DD``；读取侧同时接受两种，用 ``date_range_filter``
构造 ``$or`` 区间，不让调用方关心库里存的是哪种。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def _digits(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def to_compact(value: Any) -> Optional[str]:
    """任意日期形态 -> ``YYYYMMDD``；不可解析返回 None。"""
    digits = _digits(value)
    return digits


def to_dashed(value: Any) -> Optional[str]:
    """任意日期形态 -> ``YYYY-MM-DD``；不可解析返回 None。"""
    digits = _digits(value)
    if not digits:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def normalize_stored(value: Any) -> Optional[str]:
    """已是 ``YYYY-MM-DD`` 的原样返回，凑位形态转成 ``YYYY-MM-DD``。"""
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-":
        return text
    return to_dashed(text) or text


def date_range_filter(
    start_date: Any = None,
    end_date: Any = None,
    field: str = "trade_date",
) -> dict:
    """构造同时命中 ``YYYYMMDD`` 与 ``YYYY-MM-DD`` 两种存储形态的区间条件。

    两端都为空时返回空 dict（调用方直接 ``**`` 展开即可）。
    """
    clauses = []
    for fmt in (to_compact, to_dashed):
        start = fmt(start_date)
        end = fmt(end_date)
        if not start and not end:
            continue
        bound = {}
        if start:
            bound["$gte"] = start
        if end:
            bound["$lte"] = end
        clauses.append({field: bound})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}
