"""date_window point-in-time 规则测试（对照上游 #1220/#1476/#1595/#1126/#1300）。

纯本地：日期钳制、窗口校验、无日期条目策略、覆盖缺口声明、实时快照扣留。
"""

from datetime import datetime, timedelta

import pytest

from tradingagents.dataflows import date_window as dw


TODAY = dw.get_current_date()
PAST = "2026-01-05"


class TestCanonicalDate:
    def test_padded(self):
        assert dw.canonical_date("2026-01-05") == "2026-01-05"

    def test_unpadded(self):
        # 上游 #1488：未补零日期文本比较会错序
        assert dw.canonical_date("2026-1-5") == "2026-01-05"

    def test_digits_only(self):
        assert dw.canonical_date("20260105") == "2026-01-05"

    def test_with_time(self):
        assert dw.canonical_date("2026-01-05 15:30:00") == "2026-01-05"

    def test_unparseable_none(self):
        assert dw.canonical_date("not a date") is None
        assert dw.canonical_date("") is None
        assert dw.canonical_date(None) is None


class TestAsOf:
    def test_same_or_earlier_passes(self):
        assert dw.as_of("2026-01-03", PAST) == "2026-01-03"
        assert dw.as_of("2026-01-05", PAST) == "2026-01-05"

    def test_later_clamped(self):
        # 上游 #1220：模型传"今天"不能越过分析日
        assert dw.as_of("2026-01-10", PAST) == PAST

    def test_unparsed_request_clamped(self):
        # 不可读的请求退回分析日，绝不猜
        assert dw.as_of("2026-7-32", PAST) == PAST

    def test_none_request_clamped(self):
        assert dw.as_of(None, PAST) == PAST

    def test_outside_run_passes_through(self):
        # graph 外直接调用（无分析日）：不钳制
        assert dw.as_of("2026-01-10", "") == "2026-01-10"
        assert dw.as_of("2026-1-9", "") == "2026-01-09"


class TestAsOfWindow:
    def test_end_clamped_to_analysis_date(self):
        s, e = dw.as_of_window("2026-01-01", "2026-01-10", PAST)
        assert (s, e) == ("2026-01-01", PAST)

    def test_window_after_analysis_moves_back(self):
        # 整窗晚于分析日：保持窗长回移（上游语义）
        s, e = dw.as_of_window("2026-02-01", "2026-02-08", PAST)
        assert e == PAST
        assert (dw.datetime.strptime(e, "%Y-%m-%d") - dw.datetime.strptime(s, "%Y-%m-%d")).days == 7

    def test_inverted_window_raises(self):
        # 上游 #1595：起止倒置送回模型，不静默换窗
        with pytest.raises(dw.DateWindowError):
            dw.as_of_window("2026-01-10", "2026-01-05", PAST)

    def test_unreadable_start_raises(self):
        # 上游 #1476：起点不可读无法猜窗口
        with pytest.raises(dw.DateWindowError):
            dw.as_of_window("gibberish", PAST, PAST)

    def test_unreadable_end_reads_as_analysis_date(self):
        s, e = dw.as_of_window("2026-01-01", "gibberish", PAST)
        assert e == PAST


class TestInWindow:
    def test_dated_included(self):
        s, e = dw.window_bounds("2026-01-01", PAST)
        assert dw.in_window(dw.datetime(2026, 1, 3, 12, 0), s, e) is True
        assert dw.in_window(dw.datetime(2026, 1, 5, 23, 59), s, e) is True

    def test_dated_excluded(self):
        s, e = dw.window_bounds("2026-01-01", PAST)
        assert dw.in_window(dw.datetime(2025, 12, 31, 12, 0), s, e) is False
        assert dw.in_window(dw.datetime(2026, 1, 6, 0, 0), s, e) is False

    def test_boundary_inclusive(self):
        s, e = dw.window_bounds("2026-01-01", PAST)
        assert dw.in_window(s, s, e) is True
        assert dw.in_window(e, s, e) is True

    def test_undated_dropped_in_historical_window(self):
        # 上游 #1126：历史回放无法证明无日期条目不是未来的
        s, e = dw.window_bounds("2025-12-30", PAST)
        assert dw.in_window(None, s, e) is False

    def test_undated_kept_in_current_window(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        s, e = dw.window_bounds(yesterday, today)
        assert dw.in_window(None, s, e) is True

    def test_tz_aware_normalized(self):
        # aware datetime 去时区后比较（A 股本地时间语义）
        from datetime import timezone as tz

        s, e = dw.window_bounds("2026-01-01", PAST)
        aware = datetime(2026, 1, 3, 12, 0, tzinfo=tz.utc)
        assert dw.in_window(aware, s, e) is True


class TestCoverageGap:
    def test_empty_historical_window_is_gap(self):
        note = dw.coverage_gap([], "2025-12-30", PAST, "新闻源", "新闻")
        assert note is not None
        assert "不是" in note and "看不到" in note

    def test_empty_current_window_is_absence(self):
        # 当下窗口的空结果是真实缺席，不得夸大
        today = datetime.now().strftime("%Y-%m-%d")
        assert dw.coverage_gap([], today, today, "新闻源", "新闻") is None

    def test_items_newer_than_start_is_gap(self):
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        note = dw.coverage_gap([today], week_ago, today, "新闻源", "新闻")
        assert note is not None and "最近条目" in note

    def test_full_coverage_none(self):
        start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        assert dw.coverage_gap([start + " 08:00"], start, today, "新闻源", "新闻") is None

    def test_future_window_is_gap(self):
        future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        note = dw.coverage_gap([], future, future, "新闻源", "新闻")
        assert note is not None and "未来" in note


class TestIsHistorical:
    def test_past(self):
        assert dw.is_historical(PAST) is True

    def test_today(self):
        assert dw.is_historical(TODAY) is False

    def test_empty(self):
        assert dw.is_historical("") is False
        assert dw.is_historical(None) is False


class TestWithholdLiveSnapshot:
    def test_historical_withheld(self):
        # 上游 #1300：实时快照没有历史版本，历史运行读到即泄漏未来
        note = dw.withhold_live_snapshot(PAST, "某股估值快照")
        assert note is not None
        assert PAST in note and "实时" in note

    def test_today_served(self):
        assert dw.withhold_live_snapshot(TODAY, "某股估值快照") is None

    def test_empty_served(self):
        assert dw.withhold_live_snapshot(None, "x") is None
        assert dw.withhold_live_snapshot("", "x") is None


class TestToolClampingIntegration:
    """工具入口的钳制行为（不触 DB，只验证日期流）。"""

    def test_market_tool_clamp_via_contextvar(self, monkeypatch):
        # 模拟：分析日 2026-01-05，模型请求终点 2026-01-10
        from core.tools.context import (
            get_current_analysis_date,
            reset_current_analysis_date,
            set_current_analysis_date,
        )

        token = set_current_analysis_date(PAST)
        try:
            assert get_current_analysis_date() == PAST
            # 市场工具的逻辑路径
            run_date = get_current_analysis_date()
            s, e = dw.as_of_window("2026-01-01", "2026-01-10", run_date)
            assert e == PAST
        finally:
            reset_current_analysis_date(token)
        assert get_current_analysis_date() is None  # token 复位

    def test_no_analysis_date_no_clamp(self):
        from core.tools.context import get_current_analysis_date

        assert get_current_analysis_date() is None
        s, e = dw.as_of_window("2026-01-01", "2026-01-10", "")
        # graph 外调用不钳制（上游语义）
        assert e == "2026-01-10"

    def test_contextvar_isolation_between_contexts(self):
        # contextvars 在并发分析间天然隔离：不同 context 读不同值
        import contextvars

        from core.tools.context import set_current_analysis_date

        var_set = contextvars.copy_context()
        var_set.run(set_current_analysis_date, "2026-01-05")
        # 主 context 不受影响
        from core.tools.context import get_current_analysis_date

        assert get_current_analysis_date() is None
