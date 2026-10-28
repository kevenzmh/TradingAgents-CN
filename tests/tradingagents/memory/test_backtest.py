"""回测三件套测试：iter_grid、run_backtest 网格/跳过逻辑、summarize 统计。

纯本地测试：run_cell 由注入的假函数充当，决策日志落在 tmp_path。
"""

from pathlib import Path

import pytest

from tradingagents.backtest import (
    BacktestResult,
    eligible_tickers,
    iter_grid,
    run_backtest,
    safe_path_component,
    summarize,
)
from tradingagents.memory import TradingMemoryLog


class TestIterGrid:
    def test_basic_grid(self):
        dates = iter_grid("2026-01-01", "2026-01-15", every_n_days=7)
        assert dates == ["2026-01-01", "2026-01-08", "2026-01-15"]

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError):
            iter_grid("2026-02-01", "2026-01-01")

    def test_invalid_every_raises(self):
        with pytest.raises(ValueError):
            iter_grid("2026-01-01", "2026-01-15", every_n_days=0)

    def test_bad_format_raises(self):
        with pytest.raises(ValueError):
            iter_grid("2026-1-1", "2026-01-15")

    def test_unpadded_date_rejected(self):
        with pytest.raises(ValueError):
            iter_grid("2026-01-1", "2026-01-15")

    def test_never_past_today(self):
        future = "2099-01-01"
        dates = iter_grid("2026-01-01", future, every_n_days=7)
        assert all(d <= "2026-12-31" for d in dates)  # 远不到 2099


class TestSafePathComponent:
    @pytest.mark.parametrize("bad", ["../escape", "/abs", "", "a/b", "a\\b", ".."])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            safe_path_component(bad)

    def test_accepts_normal(self):
        assert safe_path_component("20260101_120000") == "20260101_120000"


class TestRunBacktest:
    def test_runs_cells_and_resumes(self, tmp_path):
        results_dir = str(tmp_path / "results")
        calls = []

        def run_cell(ticker, date):
            calls.append((ticker, date))
            return True

        r1 = run_backtest(
            ["600519", "000001"], ["2026-01-05", "2026-01-12"],
            run_cell, results_dir=results_dir,
        )
        assert r1.cells_run == 4
        assert r1.skipped == 0
        assert len(calls) == 4

        # 手动给两个格子写入决策（模拟已完成）
        log = TradingMemoryLog({
            "decision_log_path": str(r1.log_path),
        })
        log.store_decision("600519", "2026-01-05", "乐观", rating="乐观")
        log.store_decision("000001", "2026-01-12", "审慎", rating="审慎")

        calls.clear()
        r2 = run_backtest(
            ["600519", "000001"], ["2026-01-05", "2026-01-12"],
            run_cell, results_dir=results_dir, run_id=r1.run_id,
        )
        # 4 格中 2 格已写入决策 → 跳过 2 格、重跑其余 2 格
        assert r2.cells_run == 2
        assert r2.skipped == 2
        assert set(calls) == {("600519", "2026-01-12"), ("000001", "2026-01-05")}

    def test_cell_failure_recorded_not_fatal(self, tmp_path):
        def run_cell(ticker, date):
            if ticker == "000001":
                raise RuntimeError("模拟失败")
            return True

        r = run_backtest(
            ["600519", "000001"], ["2026-01-05"],
            run_cell, results_dir=str(tmp_path),
        )
        assert r.cells_run == 1
        assert r.failures == [("000001", "2026-01-05", "模拟失败")]

    def test_dedup_tickers_and_dates(self, tmp_path):
        calls = []
        r = run_backtest(
            ["600519", "600519"], ["2026-01-05", "2026-01-05"],
            lambda t, d: calls.append((t, d)) or True,
            results_dir=str(tmp_path),
        )
        assert r.cells_run == 1

    def test_custom_log_path(self, tmp_path):
        custom = tmp_path / "my_log.md"
        r = run_backtest(
            ["600519"], ["2026-01-05"],
            lambda t, d: True,
            results_dir=str(tmp_path),
            backtest_log_path=str(custom),
        )
        assert r.log_path == custom


class TestSummarize:
    def _log_with(self, tmp_path, entries):
        log = TradingMemoryLog({"decision_log_path": str(tmp_path / "bt.md")})
        for ticker, date, rating, raw, alpha in entries:
            log.store_decision(ticker, date, f"{rating}结论", rating=rating)
            log.update_with_outcome(ticker, date, raw, alpha, 5, "反思", "2026-01-20")
        return log

    def test_summary_by_rating(self, tmp_path):
        log = self._log_with(tmp_path, [
            ("600519", "2026-01-05", "乐观", 0.05, 0.03),
            ("600519", "2026-01-12", "乐观", -0.02, -0.04),
            ("000001", "2026-01-05", "审慎", -0.01, -0.05),
        ])
        s = summarize(tmp_path / "bt.md")
        assert s.resolved == 3
        assert s.pending == 0
        opt = s.by_rating["乐观"]
        assert opt.count == 2
        assert opt.hit_rate == 0.5  # +3% 命中、-4% 未命中
        assert opt.mean_alpha == pytest.approx(-0.005)
        cautious = s.by_rating["审慎"]
        assert cautious.hit_rate == 1.0  # 审慎 + alpha<0 = 命中
        assert "方向命中率 50%" in s.render() or "方向命中率" in s.render()

    def test_neutral_has_no_hit_rate(self, tmp_path):
        log = self._log_with(tmp_path, [
            ("600519", "2026-01-05", "中性", 0.01, 0.01),
        ])
        s = summarize(tmp_path / "bt.md")
        assert s.by_rating["中性"].hit_rate is None

    def test_review_entries_unscored(self, tmp_path):
        log = TradingMemoryLog({"decision_log_path": str(tmp_path / "bt.md")})
        log.store_decision("600519", "2026-01-05", "读不出", rating="")
        log.store_decision("000001", "2026-01-05", "乐观", rating="乐观")
        log.update_with_outcome("000001", "2026-01-05", 0.02, 0.01, 5, "r", "2026-01-12")
        s = summarize(tmp_path / "bt.md")
        assert s.unscored == 1
        assert s.resolved == 1

    def test_pending_counted(self, tmp_path):
        log = TradingMemoryLog({"decision_log_path": str(tmp_path / "bt.md")})
        log.store_decision("600519", "2026-01-05", "乐观", rating="乐观")
        s = summarize(tmp_path / "bt.md")
        assert s.pending == 1
        assert s.resolved == 0

    def test_summary_from_result_object(self, tmp_path):
        r = run_backtest(
            ["600519"], ["2026-01-05"], lambda t, d: True,
            results_dir=str(tmp_path),
        )
        log = TradingMemoryLog({"decision_log_path": str(r.log_path)})
        log.store_decision("600519", "2026-01-05", "乐观", rating="乐观")
        log.update_with_outcome("600519", "2026-01-05", 0.03, 0.02, 5, "r", "2026-01-12")
        s = summarize(r)
        assert s.resolved == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            summarize(tmp_path / "nope.md")


class TestEligibleTickers:
    def test_filters_non_cn(self):
        assert eligible_tickers(["600519", "0700.HK", "AAPL", "510300"]) == ["600519", "510300"]

    def test_normalizes_suffix(self):
        assert eligible_tickers(["600519.SH"]) == ["600519"]
