"""Pipeline 回测：在股票×日期网格上运行分析流程，并从决策日志读取评分。

参考上游 TauricResearch/TradingAgents v0.7.1 的 backtest.py。决策日志就是
结果表——每个格子已经记录结论倾向，持有窗口走出后由结算闭环写入实际
收益与 alpha，因此回测本身不需要任何额外的记录结构。

范围：这是决策质量评估工具，不是组合模拟器。不会虚构下单数量、成交价
与资金台账；每个格子相互独立。回测写入自己的决策日志（独立于真实
分析的日志），避免淹没真实运行读取的教训上下文。
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from tradingagents.memory import TradingMemoryLog
from tradingagents.memory.log import RATING_REVIEW
from tradingagents.utils.stock_utils import StockMarket, StockUtils

logger = logging.getLogger(__name__)

# 各倾向声称的方向，outcome 与之比对计算命中率；中性不声明方向
_DIRECTION = {"乐观": 1, "审慎": -1, "中性": 0}


def iter_grid(start_date: str, end_date: str, every_n_days: int = 1) -> list[str]:
    """从 ``start_date`` 起生成分析日期，绝不晚于今天。

    未来日期没有可结算的 outcome，网格在当下截断。
    """
    start, end = _canonical(start_date), _canonical(end_date)
    if every_n_days < 1:
        raise ValueError("every_n_days 必须至少为 1")
    if end < start:
        raise ValueError(f"网格终点早于起点: {end_date} 早于 {start_date}")

    last = min(end, datetime.strptime(datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d"))
    dates, cursor = [], start
    while cursor <= last:
        dates.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=every_n_days)
    return dates


def _canonical(date: str) -> datetime:
    """解析网格边界，格式要求与运行日期一致。"""
    try:
        parsed = datetime.strptime(str(date), "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"网格日期必须为 YYYY-MM-DD 格式，收到 {date!r}") from exc
    if parsed.strftime("%Y-%m-%d") != str(date):
        raise ValueError(f"网格日期必须为 YYYY-MM-DD 格式，收到 {date!r}")
    return parsed


def safe_path_component(name: str) -> str:
    """路径段校验：拒绝绝对路径与 `..` 逃逸。"""
    cleaned = str(name).strip()
    if not cleaned or cleaned.startswith((".", "/", "\\")) or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"非法路径段: {name!r}")
    return cleaned


def _alpha(entry: dict) -> Optional[float]:
    """已结算条目的 alpha（百分数）。日志按 0.1% 精度存储。"""
    text = (entry.get("alpha") or "").strip().rstrip("%")
    try:
        return float(text) / 100
    except ValueError:
        return None


@dataclass
class BacktestResult:
    run_id: str
    log_path: Path
    cells_run: int = 0
    skipped: int = 0
    failures: list[tuple[str, str, str]] = field(default_factory=list)
    settlement_failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RatingScore:
    count: int
    hit_rate: Optional[float]
    mean_alpha: float


@dataclass
class BacktestSummary:
    resolved: int
    pending: int
    by_rating: dict[str, RatingScore]
    unscored: int = 0
    holding: str = ""

    def render(self) -> str:
        lines = [f"已结算格子: {self.resolved} · 待结算: {self.pending}"
                 + (f" · 待复核: {self.unscored}" if self.unscored else "")]
        for rating, score in self.by_rating.items():
            called = (f"方向命中率 {score.hit_rate:.0%}" if score.hit_rate is not None else "无方向声明")
            lines.append(
                f"- {rating}: n={score.count}, {called}, "
                f"平均 alpha {score.mean_alpha:+.2%}（相对基准）"
            )
        lines.append("")
        if self.pending:
            lines.append("待结算格子未计入上述评分；重跑本回测可结算已到期的格子。")
        lines.append(
            f"alpha 按每个分析日期后第一个收盘起算的 {self.holding or '配置窗口'} 度量。"
            "每格单次模型采样，文本数据源未存档，结果供方向性参考而非可复现基准。"
        )
        return "\n".join(lines)


def _new_run(runs_dir: Path) -> str:
    """带时间戳的 run_id，原子性占用目录避免并发覆盖。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    runs_dir.mkdir(parents=True, exist_ok=True)
    for n in itertools.count(1):
        run_id = stamp if n == 1 else f"{stamp}_{n}"
        try:
            (runs_dir / run_id).mkdir()
            return run_id
        except FileExistsError:
            continue


def run_backtest(
    tickers: list[str],
    dates: list[str],
    run_cell: Callable[[str, str], bool],
    results_dir: str = "./results",
    run_id: Optional[str] = None,
    backtest_log_path: Optional[str] = None,
    progress: Optional[Callable[[int, int, str, str], None]] = None,
) -> BacktestResult:
    """在网格上逐格运行分析。

    与上游不同，这里不直接构建 graph——A 股的运行入口是项目自己的
    工作流引擎。``run_cell(ticker, date) -> bool`` 由调用方注入（True 表示
    该格成功产出结论），因此本函数只负责网格、日志与跳过逻辑，分析
    执行方式与主程序保持同一套代码路径。

    回测写入自己的决策日志（``backtest_log_path``），与真实分析的日志
    隔离；格子已存在于本 run 的日志则跳过，中断后重跑即续跑。
    """
    runs_dir = Path(results_dir) / "backtest"
    if run_id:
        run_id = safe_path_component(run_id)
        (runs_dir / run_id).mkdir(parents=True, exist_ok=True)
    else:
        run_id = _new_run(runs_dir)
    run_dir = runs_dir / run_id
    log_path = Path(backtest_log_path) if backtest_log_path else run_dir / "trading_memory.md"

    memory_log = TradingMemoryLog({"decision_log_path": str(log_path)})
    result = BacktestResult(run_id=run_id, log_path=log_path)
    done = {(e["ticker"], e["date"]) for e in memory_log.load_entries()}

    # 重复的 ticker 或日期只算一个格子，运行并结算一次
    tickers = list(dict.fromkeys(tickers))
    dates = list(dict.fromkeys(dates))
    cells = [(t, d) for t in tickers for d in dates]
    todo = [cell for cell in cells if cell not in done]
    result.skipped = len(cells) - len(todo)

    logger.info("🎯 [回测] run=%s: %d 个格子（跳过 %d 个已完成）", run_id, len(todo), result.skipped)
    for index, (ticker, date) in enumerate(todo, 1):
        if progress:
            progress(index, len(todo), ticker, date)
        try:
            ok = run_cell(ticker, date)
            if ok:
                result.cells_run += 1
        except Exception as exc:  # 单格失败不终止整个扫描
            logger.warning("回测格子 %s %s 失败: %s", ticker, date, exc)
            result.failures.append((ticker, date, str(exc)))
    return result


def settle_backtest(result: BacktestResult, reflector=None, config: dict = None) -> list[tuple[str, str]]:
    """回测收尾：结算每个 ticker 的最后一格。

    结算通常在下一次分析该股票时触发，回测中每只股票的最后一格没有
    "下一次"，需要显式结算一遍。
    """
    from tradingagents.memory import settle_pending

    memory_log = TradingMemoryLog({"decision_log_path": str(result.log_path)})
    failures: list[tuple[str, str]] = []
    for ticker in dict.fromkeys(e["ticker"] for e in memory_log.get_pending_entries()):
        try:
            settlement = settle_pending(ticker, memory_log, reflector, config=config or {})
            failures.extend((t, f"{d}: {reason}") for t, d, reason in settlement.failed)
        except Exception as exc:
            logger.warning("结算 %s 失败: %s", ticker, exc)
            failures.append((ticker, str(exc)))
    result.settlement_failures.extend(failures)
    return failures


def summarize(source: "BacktestResult | str | Path") -> BacktestSummary:
    """按结论倾向统计已结算格子的命中率与平均 alpha。"""
    if isinstance(source, BacktestResult):
        path = source.log_path
    else:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"决策日志不存在: {source}")
    # 回测日志通常含待复核条目；统计时排除
    entries = TradingMemoryLog({"decision_log_path": str(path)}).load_entries()

    resolved = [(e, _alpha(e)) for e in entries
                if not e["pending"] and e.get("rating") != RATING_REVIEW]
    resolved = [(e, a) for e, a in resolved if a is not None]

    by_rating: dict[str, RatingScore] = {}
    for rating in dict.fromkeys(e["rating"] for e, _ in resolved):
        alphas = [a for e, a in resolved if e["rating"] == rating]
        direction = _DIRECTION.get(rating, 0)
        by_rating[rating] = RatingScore(
            count=len(alphas),
            hit_rate=(sum(a * direction > 0 for a in alphas) / len(alphas)) if direction else None,
            mean_alpha=sum(alphas) / len(alphas),
        )
    unscored = sum(1 for e in entries if e.get("rating") == RATING_REVIEW)

    windows = {f"{e['holding'][:-1]} 个交易日" for e, _ in resolved
               if (e.get("holding") or "").endswith("d")}
    return BacktestSummary(
        resolved=len(resolved),
        pending=len(entries) - len(resolved) - unscored,
        by_rating=by_rating,
        unscored=unscored,
        holding="、".join(sorted(windows)) or "配置窗口",
    )


def eligible_tickers(tickers: list[str]) -> list[str]:
    """过滤出可结算市场的 ticker（当前为 A 股个股与 ETF）。"""
    eligible = []
    for t in tickers:
        if StockUtils.identify_stock_market(t) == StockMarket.CHINA_A:
            eligible.append(str(t).split(".")[0].zfill(6))
        else:
            logger.info("⏭️ [回测] 跳过非 A 股标的 %s（结算通道未覆盖）", t)
    return eligible
