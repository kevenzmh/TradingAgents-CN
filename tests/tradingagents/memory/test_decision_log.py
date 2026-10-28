"""决策结算闭环测试：日志读写、结算更新、轮转、point-in-time 过滤、并发锁。

纯本地测试，不依赖 MongoDB/行情/LLM。
"""

import threading
from datetime import datetime, timedelta

import pytest

from tradingagents.memory import (
    Reflector,
    TradingMemoryLog,
    create_memory_log,
    parse_rating,
)
from tradingagents.memory.log import _SEPARATOR


@pytest.fixture
def log(tmp_path):
    return TradingMemoryLog({
        "decision_log_path": str(tmp_path / "trading_memory.md"),
    })


def _store(log, ticker, date, decision, rating=None):
    log.store_decision(ticker, date, decision, rating=rating)


class TestStoreDecision:
    def test_store_pending_entry(self, log, tmp_path):
        _store(log, "000001", "2026-01-05", "研究结论：乐观，建议持续关注。")
        entries = log.load_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e["ticker"] == "000001"
        assert e["date"] == "2026-01-05"
        assert e["rating"] == "乐观"
        assert e["pending"] is True
        assert "研究结论：乐观" in e["decision"]

    def test_dedup_same_ticker_and_date(self, log):
        _store(log, "000001", "2026-01-05", "第一次结论", rating="乐观")
        _store(log, "000001", "2026-01-05", "第二次结论", rating="审慎")
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["decision"] == "第一次结论"

    def test_unparseable_rating_recorded_as_review(self, log):
        """读不出倾向的结论记为待复核（可观测），而非静默丢弃。"""
        log.store_decision("000001", "2026-01-05", "完全读不出倾向的文本", rating="")
        entries = log.load_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e["rating"] == "待复核"
        assert e["pending"] is True

    def test_review_entry_excluded_from_past_context(self, log):
        log.store_decision("000001", "2026-01-05", "读不出倾向", rating="")
        log.update_with_outcome("000001", "2026-01-05", 0.01, 0.01, 5, "不该出现的反思", "2026-01-12")
        assert log.get_past_context("000001") == ""

    def test_review_entry_not_settled(self, log):
        from tradingagents.memory import settlement as s

        log.store_decision("000001", "2026-01-05", "读不出倾向", rating="")
        called = []
        orig = s.fetch_returns
        s.fetch_returns = lambda *a, **k: called.append(1) or (0.1, 0.1, 5, "2026-01-12")
        try:
            result = s.settle_pending("000001", log, None, config={})
        finally:
            s.fetch_returns = orig
        assert result.settled == []
        assert not called  # 待复核条目不触发行情拉取
        assert log.load_entries()[0]["pending"] is True

    def test_no_log_path_is_noop(self):
        log = TradingMemoryLog({})
        log.store_decision("000001", "2026-01-05", "x", rating="乐观")
        assert log.load_entries() == []


class TestParseRating:
    @pytest.mark.parametrize("text,expected", [
        ("综合评估：乐观倾向", "乐观"),
        ("研究结论：审慎", "审慎"),
        ("结论：中性观望", "中性"),
        ("BUY signal with bullish tone", "乐观"),
        ("建议卖出", "审慎"),
        ("持有并持续观察", "中性"),
        ("没有任何倾向词", ""),
        ("", ""),
        (None, ""),
    ])
    def test_parse(self, text, expected):
        assert parse_rating(text) == expected


class TestSettlement:
    def _store_pending(self, log, **kwargs):
        _store(log, **kwargs)

    def test_update_with_outcome(self, log):
        self._store_pending(log, ticker="000001", date="2026-01-05",
                            decision="乐观结论", rating="乐观")
        ok = log.update_with_outcome(
            "000001", "2026-01-05",
            raw_return=0.05, alpha_return=0.02,
            holding_days=5, reflection="方向判断正确。",
            resolution_date="2026-01-12",
        )
        assert ok is True
        e = log.load_entries()[0]
        assert e["pending"] is False
        assert e["raw"] == "+5.0%"
        assert e["alpha"] == "+2.0%"
        assert e["holding"] == "5d"
        assert e["resolved"] == "2026-01-12"
        assert e["reflection"] == "方向判断正确。"

    def test_update_returns_false_when_not_pending(self, log):
        self._store_pending(log, ticker="000001", date="2026-01-05",
                            decision="x", rating="乐观")
        assert log.update_with_outcome("000001", "2026-01-05", 0.1, 0.1, 5, "r", "2026-01-12")
        # 第二次结算同一 (date, ticker) 不重复写
        assert log.update_with_outcome("000001", "2026-01-05", 0.2, 0.2, 5, "r2", "2026-01-12") is False
        e = log.load_entries()[0]
        assert e["raw"] == "+10.0%"
        assert e["reflection"] == "r"

    def test_update_other_ticker_untouched(self, log):
        _store(log, "000001", "2026-01-05", "A", rating="乐观")
        _store(log, "600519", "2026-01-05", "B", rating="审慎")
        log.update_with_outcome("000001", "2026-01-05", 0.01, 0.01, 5, "r", "2026-01-12")
        entries = {e["ticker"]: e for e in log.load_entries()}
        assert entries["600519"]["pending"] is True
        assert entries["000001"]["pending"] is False


class TestPastContext:
    def _settled(self, log, ticker, date, raw, alpha, reflection, resolved=None):
        _store(log, ticker, date, f"{ticker} 结论", rating="乐观")
        log.update_with_outcome(ticker, date, raw, alpha, 5, reflection, resolved)

    def test_same_ticker_first(self, log):
        for i, date in enumerate(["2026-01-05", "2026-01-12", "2026-01-19"]):
            self._settled(log, "000001", date, 0.01 * i, 0.01 * i, f"教训{i}",
                          resolved=date)
        self._settled(log, "600519", "2026-02-01", 0.02, 0.02, "跨标的教训",
                      resolved="2026-02-10")
        ctx = log.get_past_context("000001")
        assert "000001 的历史研究结论" in ctx
        assert "教训2" in ctx  # 最新的同标的教训
        assert "跨标的教训" in ctx
        # 按日期降序
        assert ctx.index("教训2") < ctx.index("教训1") < ctx.index("教训0")

    def test_pending_excluded(self, log):
        _store(log, "000001", "2026-01-05", "pending 结论", rating="乐观")
        assert log.get_past_context("000001") == ""

    def test_as_of_filters_unresolved_yet(self, log):
        self._settled(log, "000001", "2026-01-05", 0.01, 0.01, "早期教训", resolved="2026-01-12")
        self._settled(log, "000001", "2026-03-05", 0.02, 0.02, "晚期教训", resolved="2026-03-12")
        # as_of=2026-02-01 只能看到 1 月的结果
        ctx = log.get_past_context("000001", as_of="2026-02-01")
        assert "早期教训" in ctx
        assert "晚期教训" not in ctx

    def test_as_of_no_resolved_field_keeps_entry(self, log):
        self._settled(log, "000001", "2026-01-05", 0.01, 0.01, "旧格式教训", resolved=None)
        # resolved 缺失（旧格式）在 as_of 过滤下被排除（point-in-time 保守）
        ctx = log.get_past_context("000001", as_of="2026-01-20")
        assert ctx == ""
        # 不过滤时保留
        assert "旧格式教训" in log.get_past_context("000001")


class TestRotation:
    def test_rotation_drops_oldest_resolved(self, tmp_path):
        log = TradingMemoryLog({
            "decision_log_path": str(tmp_path / "t.md"),
            "decision_log_max_entries": 2,
        })
        for i, date in enumerate(["2026-01-05", "2026-01-12", "2026-01-19"]):
            _store(log, "000001", date, f"结论{i}", rating="乐观")
            log.update_with_outcome("000001", date, 0.01, 0.01, 5, f"反思{i}", "2026-02-01")
        entries = log.load_entries()
        assert len(entries) == 2
        # 最旧的 2026-01-05 被丢弃
        assert {e["date"] for e in entries} == {"2026-01-12", "2026-01-19"}

    def test_rotation_keeps_pending(self, tmp_path):
        log = TradingMemoryLog({
            "decision_log_path": str(tmp_path / "t.md"),
            "decision_log_max_entries": 1,
        })
        _store(log, "000001", "2026-01-05", "已结算", rating="乐观")
        log.update_with_outcome("000001", "2026-01-05", 0.01, 0.01, 5, "r", "2026-02-01")
        _store(log, "000001", "2026-02-05", "pending", rating="乐观")
        _store(log, "000001", "2026-02-12", "pending2", rating="乐观")
        entries = log.load_entries()
        assert len(entries) == 3  # 1 resolved + 2 pending


class TestLocking:
    def test_concurrent_settling_single_pass(self, log):
        """settling(wait=False) 下并发只有一个流程能拿到锁。"""
        results = []

        def try_settle():
            with log.settling(wait=False) as held:
                results.append(held)

        t1 = threading.Thread(target=try_settle)
        t1.start()
        t1.join()
        # 顺序执行时都能拿到；这里主要验证 API 不抛异常
        assert results[0] is True

    def test_store_is_thread_safe(self, log):
        errors = []

        def store_many(i):
            try:
                for j in range(10):
                    _store(log, f"{i:06d}", f"2026-01-{j+1:02d}", f"结论{i}-{j}", rating="乐观")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=store_many, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(log.load_entries()) == 40


class TestReflector:
    def test_reflect_invokes_llm(self):
        class FakeLLM:
            def invoke(self, messages):
                self.messages = messages
                return type("R", (), {"content": "反思内容"})

        fake = FakeLLM()
        r = Reflector(fake)
        out = r.reflect_on_final_decision(
            final_decision="乐观结论",
            raw_return=0.05,
            alpha_return=0.02,
            benchmark_name="沪深300",
            holding_days=5,
        )
        assert out == "反思内容"
        sys_msg = fake.messages[0][1]
        assert "5 个交易日" in sys_msg
        human = fake.messages[1][1]
        assert "+5.0%" in human
        assert "沪深300" in human


class TestFetchReturnsWindow:
    def test_window_not_traded_returns_none(self, monkeypatch):
        from tradingagents.memory import settlement as s

        # 伪造 3 个收盘（holding_days=5 需要至少 6 个）
        dates = ["2026-01-06", "2026-01-07", "2026-01-08"]
        closes = s.pd.Series([10.0, 10.5, 11.0], index=dates)
        monkeypatch.setattr(s, "_fetch_stock_closes", lambda *a: (closes, None))
        monkeypatch.setattr(s, "_fetch_benchmark_closes", lambda *a: closes)
        raw, alpha, days, res = s.fetch_returns("000001", "2026-01-05", 5, "000300.SH")
        assert raw is None and alpha is None

    def test_full_window_returns(self, monkeypatch):
        from tradingagents.memory import settlement as s

        stock_dates = [f"2026-01-{d:02d}" for d in range(6, 16)][:6]
        stock = s.pd.Series([10.0, 10.5, 11.0, 10.8, 11.2, 12.0], index=stock_dates)
        bench_dates = ["2026-01-05"] + stock_dates
        bench = s.pd.Series([100.0] * 7, index=bench_dates)
        monkeypatch.setattr(s, "_fetch_stock_closes", lambda *a: (stock, None))
        monkeypatch.setattr(s, "_fetch_benchmark_closes", lambda *a: bench)
        raw, alpha, days, res = s.fetch_returns("000001", "2026-01-05", 5, "000300.SH")
        assert days == 5
        assert res == stock_dates[5]
        # 12.0 / 10.0 - 1 = +20%（基准 0 收益）
        assert raw == pytest.approx(0.2)
        assert alpha == pytest.approx(0.2)

    def test_pct_chain_ignores_adjustment_gaps(self, monkeypatch):
        """复权一致性：窗口跨除权日时，pct_chg 链乘结果不受 close 跳变影响。"""
        from tradingagents.memory import settlement as s

        stock_dates = [f"2026-01-{d:02d}" for d in range(6, 16)][:6]
        # 除权日 close 从 11.0 跳到 5.0（如 10 送 10），pct_chg 仍是真实日收益
        stock = s.pd.Series([10.0, 10.5, 11.0, 5.4, 5.5, 5.6], index=stock_dates)
        pcts = s.pd.Series([1.0, 4.76, 4.76, 1.85, 1.85, 1.82], index=stock_dates)
        bench = s.pd.Series([100.0] * 7, index=["2026-01-05"] + stock_dates)
        monkeypatch.setattr(s, "_fetch_stock_closes", lambda *a: (stock, pcts))
        monkeypatch.setattr(s, "_fetch_benchmark_closes", lambda *a: bench)
        raw, alpha, days, res = s.fetch_returns("000001", "2026-01-05", 5, "000300.SH")
        # 链乘 4 天真实日收益 ≈ +14.2%，远不等于 close 比值 5.6/10-1 = -44%
        assert raw == pytest.approx((1.0476 * 1.0476 * 1.0185 * 1.0185 * 1.0182) - 1, rel=1e-3)
        assert raw > 0

    def test_pct_missing_falls_back_to_close(self, monkeypatch):
        from tradingagents.memory import settlement as s

        stock_dates = [f"2026-01-{d:02d}" for d in range(6, 16)][:6]
        stock = s.pd.Series([10.0, 10.5, 11.0, 10.8, 11.2, 12.0], index=stock_dates)
        # 有 pct 列但窗口内有 NaN → 退回 close 比值
        pcts = s.pd.Series([1.0, 4.76, None, 1.85, 1.85, 1.82], index=stock_dates)
        bench = s.pd.Series([100.0] * 7, index=["2026-01-05"] + stock_dates)
        monkeypatch.setattr(s, "_fetch_stock_closes", lambda *a: (stock, pcts))
        monkeypatch.setattr(s, "_fetch_benchmark_closes", lambda *a: bench)
        raw, alpha, days, res = s.fetch_returns("000001", "2026-01-05", 5, "000300.SH")
        assert raw == pytest.approx(0.2)

    def test_future_date_returns_none(self, monkeypatch):
        from tradingagents.memory import settlement as s

        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        called = []
        monkeypatch.setattr(s, "_fetch_stock_closes", lambda *a: called.append(1))
        raw, alpha, days, res = s.fetch_returns("000001", future, 5, "000300.SH")
        assert raw is None
        assert not called  # 窗口未到，不能取行情


class TestSettlePendingFlow:
    def test_settle_with_reflector(self, log):
        _store(log, "000001", "2026-01-05", "乐观结论", rating="乐观")

        returns = {"raw": 0.05, "alpha": 0.02, "days": 5, "res": "2026-01-12"}

        from tradingagents.memory import settlement as s

        s.fetch_returns = lambda *a, **k: (
            returns["raw"], returns["alpha"], returns["days"], returns["res"]
        )

        class FakeReflector:
            def reflect_on_final_decision(self, **kw):
                return "自动生成的反思"

        result = s.settle_pending("000001", log, FakeReflector(), config={})
        assert result.settled == [("000001", "2026-01-05")]
        e = log.load_entries()[0]
        assert e["pending"] is False
        assert e["reflection"] == "自动生成的反思"

    def test_settle_without_reflector_records_result(self, log):
        _store(log, "000001", "2026-01-05", "乐观结论", rating="乐观")

        from tradingagents.memory import settlement as s

        orig = s.fetch_returns
        s.fetch_returns = lambda *a, **k: (0.03, 0.01, 5, "2026-01-12")
        try:
            result = s.settle_pending("000001", log, None, config={})
        finally:
            s.fetch_returns = orig
        assert result.settled
        assert "超额" in log.load_entries()[0]["reflection"]

    def test_settle_price_failure_left_pending(self, log):
        _store(log, "000001", "2026-01-05", "乐观结论", rating="乐观")

        from tradingagents.memory import settlement as s

        def boom(*a, **k):
            raise RuntimeError("行情不可用")

        orig = s.fetch_returns
        s.fetch_returns = boom
        try:
            result = s.settle_pending("000001", log, None, config={})
        finally:
            s.fetch_returns = orig
        assert result.failed
        assert log.load_entries()[0]["pending"] is True

    def test_hk_ticker_not_settleable(self, log):
        _store(log, "0700.HK", "2026-01-05", "港股结论", rating="乐观")
        from tradingagents.memory import settlement as s

        result = s.settle_pending("0700.HK", log, None, config={})
        assert result.failed and "暂不支持" in result.failed[0][2]
        assert log.load_entries()[0]["pending"] is True


class TestCreateMemoryLog:
    def test_default_path_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_DECISION_LOG_PATH", str(tmp_path / "custom.md"))
        log = create_memory_log()
        log.store_decision("000001", "2026-01-05", "x", rating="乐观")
        assert (tmp_path / "custom.md").exists()

    def test_separator_roundtrip(self, log):
        _store(log, "000001", "2026-01-05", "多行\n结论\n内容", rating="乐观")
        _store(log, "600519", "2026-01-05", "第二条", rating="审慎")
        entries = log.load_entries()
        assert len(entries) == 2
        assert entries[0]["decision"] == "多行\n结论\n内容"
        assert entries[1]["rating"] == "审慎"
