"""宏观仪表盘指标解析与"最新已发布期"选取的行为约束。

回归背景：akshare 宏观接口排序不统一（CPI/PMI/GDP 最新在前，PPI/LPR/社融
最旧在前），且最后一个报告期常常只有"前值"、没有"今值"。按首行/末行取，
会把 1995 年的 PPI 或上一期的 CPI 当成当期。
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.tools.implementations.fundamentals.macro_tools import (  # noqa: E402
    _latest_row,
    _parse_china_month,
    _parse_china_quarter,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026年09月份", "2026-09"),
        ("2026年10月份", "2026-10"),
        ("202601", "2026-01"),
        ("2026-07", "2026-07"),
    ],
)
def test_parse_china_month(value, expected):
    assert _parse_china_month(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026年第1-3季度", "2026-09"),
        ("2026年第2季度", "2026-06"),
        ("2026年第4季度", "2026-12"),
    ],
)
def test_parse_china_quarter(value, expected):
    assert _parse_china_quarter(value) == expected


def _period_from_month(row):
    return _parse_china_month(row.get("月份")) or ""


def _period_from_trade_date(row):
    return str(row.get("TRADE_DATE") or "")[:10]


def test_latest_row_picks_newest_when_frame_is_oldest_first():
    frame = pd.DataFrame(
        [
            {"TRADE_DATE": "1991-04-21", "LPR1Y": None, "LPR5Y": None},
            {"TRADE_DATE": "2026-09-20", "LPR1Y": 3.0, "LPR5Y": 3.5},
            {"TRADE_DATE": "2026-10-20", "LPR1Y": 3.1, "LPR5Y": 3.6},
        ]
    )

    row = _latest_row(frame, _period_from_trade_date, ("LPR1Y", "LPR5Y"))

    assert row["TRADE_DATE"] == "2026-10-20"


def test_latest_row_skips_unpublished_period_without_value():
    frame = pd.DataFrame(
        [
            {"月份": "2026年08月份", "全国-同比增长": 0.6},
            {"月份": "2026年09月份", "全国-同比增长": 0.9},
            {"月份": "2026年10月份", "全国-同比增长": None},
        ]
    )

    row = _latest_row(frame, _period_from_month, ("全国-同比增长",))

    assert row["月份"] == "2026年09月份"


def test_latest_row_returns_none_for_empty_frame():
    assert _latest_row(pd.DataFrame(), _period_from_month, ("全国-同比增长",)) is None
    assert _latest_row(None, _period_from_month, ("全国-同比增长",)) is None
