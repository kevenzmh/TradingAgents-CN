"""交易日日期区间过滤的行为约束。

背景：stock_daily_quotes.trade_date 两种形态并存（YYYY-MM-DD 与 YYYYMMDD），
BSON 字符串比较下两者互相排斥，只按一种形态做 $gte/$lte 会让另一批数据整体
不可见。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tradingagents.utils.trade_date import (  # noqa: E402
    date_range_filter,
    normalize_stored,
    to_compact,
    to_dashed,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-01", "20260901"),
        ("20260901", "20260901"),
        ("2026/09/01", "20260901"),
        ("2026-09-01 00:00:00", "20260901"),
        ("20160704 00:00:00", "20160704"),
    ],
)
def test_to_compact_accepts_all_stored_shapes(value, expected):
    assert to_compact(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("20260901", "2026-09-01"),
        ("2026-09-01", "2026-09-01"),
        ("2026/09/01", "2026-09-01"),
    ],
)
def test_to_dashed_normalizes(value, expected):
    assert to_dashed(value) == expected


def test_normalize_stored_only_rewrites_compact_form():
    assert normalize_stored("20260901") == "2026-09-01"
    assert normalize_stored("2026-09-01") == "2026-09-01"


def test_date_range_filter_covers_both_stored_forms():
    result = date_range_filter("2026-09-01", "2026-11-05", field="trade_date")

    assert result == {
        "$or": [
            {"trade_date": {"$gte": "20260901", "$lte": "20261105"}},
            {"trade_date": {"$gte": "2026-09-01", "$lte": "2026-11-05"}},
        ]
    }


def test_date_range_filter_handles_open_ended_bounds():
    assert date_range_filter("2026-09-01", None) == {
        "$or": [
            {"trade_date": {"$gte": "20260901"}},
            {"trade_date": {"$gte": "2026-09-01"}},
        ]
    }
    assert date_range_filter(None, "2026-11-05") == {
        "$or": [
            {"trade_date": {"$lte": "20261105"}},
            {"trade_date": {"$lte": "2026-11-05"}},
        ]
    }


def test_date_range_filter_is_empty_without_bounds():
    assert date_range_filter(None, None) == {}