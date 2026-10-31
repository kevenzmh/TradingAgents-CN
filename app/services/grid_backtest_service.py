"""网格回测服务：把 tradingagents/backtest.py 的库函数接到生产分析引擎上。

run_cell(ticker, date) -> bool：构造 UnifiedAnalysisTask（STOCK_ANALYSIS，
历史 analysis_date），用 UnifiedAnalysisEngine 跑完整分析（与生产同一代码
路径，含 point-in-time 扣留），从结果中提取结论倾向，写进回测专用决策
日志。格子串行执行（本地单模型吞吐限制），进度写入 Mongo 供前端轮询。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RESULTS_DIR = "./results"

# 回测运行状态存 Mongo 的这个集合；一条 run 一个文档
BT_COLLECTION = "backtest_runs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class BacktestRunState:
    """一次回测 run 的可轮询状态（Mongo 文档形状）。"""

    run_id: str
    user_id: str
    tickers: List[str]
    dates: List[str]
    every_n_days: int = 1
    status: str = "pending"  # pending/running/completed/failed
    total_cells: int = 0
    cells_done: int = 0
    cells_run: int = 0
    cells_skipped: int = 0
    current_cell: str = ""
    failures: List[Dict[str, str]] = field(default_factory=list)
    settlement_failures: List[Dict[str, str]] = field(default_factory=list)
    summary_text: str = ""
    summary_data: Optional[Dict[str, Any]] = None
    log_path: str = ""
    created_at: str = ""
    finished_at: str = ""
    error: str = ""


async def _save_state(db, state: BacktestRunState) -> None:
    await db[BT_COLLECTION].update_one(
        {"run_id": state.run_id}, {"$set": state.__dict__}, upsert=True
    )


def _cell_summary_from_result(result: Dict[str, Any]) -> Dict[str, str]:
    """从分析结果提取结论倾向（与结算记录同一套提取逻辑的轻量版）。"""
    decision = (result or {}).get("decision") or {}
    action = str(decision.get("action") or decision.get("analysis_view") or "").strip()
    if not action:
        from app.services.task_analysis_service import TaskAnalysisService

        text = (result or {}).get("final_report") or (result or {}).get("recommendation") or ""
        extracted = TaskAnalysisService()._extract_decision_from_text(str(text))
        action = str(extracted.get("action") or extracted.get("analysis_view") or "").strip()
    return {"action": action}


def _make_run_cell(user_id: str, sync_event_loop, bt_memory_log_path: str):
    """构造 tradingagents.backtest.run_backtest 需要的 run_cell 回调。

    run_cell 是同步函数（被 run_backtest 在后台线程逐格调用），内部把
    异步 engine.execute_task 调度回主事件 loop 并等待。结论倾向写进
    回测专用决策日志（TradingMemoryLog 线程安全，锁内追加）。
    """
    from app.models.analysis import AnalysisTaskType, UnifiedAnalysisTask
    from app.services.unified_analysis_engine import UnifiedAnalysisEngine
    from tradingagents.memory import TradingMemoryLog

    engine = UnifiedAnalysisEngine()
    bt_log = TradingMemoryLog({"decision_log_path": bt_memory_log_path})
    if not bt_log._log_path:  # 路径没解析出来时永远不会落盘——早失败早知道
        raise RuntimeError(f"回测日志路径无效: {bt_memory_log_path}")

    def run_cell(ticker: str, date: str) -> bool:
        task = UnifiedAnalysisTask(
            task_id=f"bt-{uuid.uuid4()}",
            user_id=user_id,
            task_type=AnalysisTaskType.STOCK_ANALYSIS,
            task_params={
                "symbol": ticker,
                "stock_code": ticker,
                "market_type": "A股",
                "analysis_date": f"{date}T00:00:00",
                "research_depth": "标准",
                "selected_analysts": ["market", "fundamentals", "news", "social"],
                "language": "zh-CN",
                "engine": "v2",
            },
        )
        fut = asyncio.run_coroutine_threadsafe(
            engine.execute_task(task), sync_event_loop
        )
        result = fut.result()  # 引擎失败抛异常 → run_backtest 记 failures
        summary = _cell_summary_from_result(result or {})
        action = summary["action"]
        if not action:
            return False  # 产出无结论倾向：不算成功格，也不记失败
        decision_text = str((result or {}).get("recommendation")
                            or (result or {}).get("summary") or action)
        bt_log.store_decision(ticker, date, decision_text, rating=action)
        return True

    return run_cell


async def reap_orphaned_runs(db) -> int:
    """启动时收割僵尸 run：后端重启会使后台线程无声消失，run 文档
    却还标着 pending/running/settling——不清理会永久卡住 409 互斥。

    已完成格子的决策都在日志里，重跑同一网格会自动续跑（run_backtest
    按 (ticker, date) 跳过已完成格子），所以标记失败不损失任何工作。
    """
    result = await db[BT_COLLECTION].update_many(
        {"status": {"$in": ["pending", "running", "settling"]}},
        {
            "$set": {
                "status": "failed",
                "error": "后端进程重启，回测后台线程中断。已完成格子的决策保留在日志中，重新发起同网格回测可续跑。",
                "finished_at": _now().isoformat(),
            }
        },
    )
    if result.modified_count:
        logger.warning(
            "[回测] 启动清理：%d 个僵尸 run 标记为 failed（进程重启遗留）",
            result.modified_count,
        )
    return result.modified_count


async def start_backtest(
    db,
    user_id: str,
    tickers: List[str],
    start_date: str,
    end_date: str,
    every_n_days: int = 1,
    research_depth: str = "标准",
) -> str:
    """创建 run 文档并启动后台线程；立即返回 run_id。"""
    from tradingagents.backtest import eligible_tickers, iter_grid

    codes = eligible_tickers(tickers)
    if not codes:
        raise ValueError("没有可回测的 A 股标的")
    dates = iter_grid(start_date, end_date, every_n_days)
    if not dates:
        raise ValueError("日期网格为空（检查区间与步长）")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    state = BacktestRunState(
        run_id=run_id,
        user_id=user_id,
        tickers=codes,
        dates=dates,
        every_n_days=every_n_days,
        total_cells=len(codes) * len(dates),
        created_at=_now().isoformat(),
        log_path="",
    )
    state.log_path = str((Path(RESULTS_DIR).resolve()) / "backtest" / run_id / "trading_memory.md")
    await _save_state(db, state)

    loop = asyncio.get_running_loop()
    asyncio.get_running_loop().run_in_executor(
        None,
        _run_backtest_sync,
        db,
        state,
        loop,
        research_depth,
    )
    return run_id


def _run_backtest_sync(db, state: BacktestRunState, loop, research_depth: str) -> None:
    """后台线程：网格 → 结算 → 统计，全程把状态刷回 Mongo。"""
    import traceback
    from pathlib import Path as _Path

    from tradingagents.backtest import run_backtest, settle_backtest, summarize

    # run_backtest 自己会在 run_dir 下创建（或复用）trading_memory.md。
    # 用绝对路径——服务进程与诊断脚本的 cwd 可能不同，相对路径会把
    # 日志写到不同位置（第一次真跑就踩了这个坑）。
    log_path = (_Path(RESULTS_DIR).resolve()) / "backtest" / state.run_id / "trading_memory.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def progress(index, total, ticker, date):
        state.cells_done = index - 1
        state.current_cell = f"{ticker} @ {date}"
        asyncio.run_coroutine_threadsafe(
            _save_state(db, state), loop
        ).result(timeout=10)

    try:
        run_cell = _make_run_cell(state.user_id, loop, str(log_path))
        result = run_backtest(
            tickers=state.tickers,
            dates=state.dates,
            run_cell=run_cell,
            results_dir=RESULTS_DIR,
            run_id=state.run_id,
            progress=progress,
        )
        state.cells_run = result.cells_run
        state.cells_skipped = result.skipped
        state.failures = [
            {"ticker": t, "date": d, "error": e} for t, d, e in result.failures
        ]
        state.cells_done = state.total_cells - state.cells_skipped
        state.status = "settling"

        # 结算每只股票的最后一格 + 已到期格子
        from app.services.task_analysis_service import TaskAnalysisService

        svc = TaskAnalysisService()
        try:
            reflector = svc._create_settlement_reflector()
        except Exception as exc:
            logger.warning("[回测] 反思模型不可用，结算退化为仅记录: %s", exc)
            reflector = None
        failures = settle_backtest(result, reflector, svc._settlement_config())
        state.settlement_failures = [
            {"ticker": t, "error": e} for t, e in failures
        ]

        summary = summarize(result)
        state.summary_text = summary.render()
        state.summary_data = {
            "resolved": summary.resolved,
            "pending": summary.pending,
            "unscored": summary.unscored,
            "holding": summary.holding,
            "by_rating": {
                rating: {
                    "count": s.count,
                    "hit_rate": s.hit_rate,
                    "mean_alpha": s.mean_alpha,
                }
                for rating, s in summary.by_rating.items()
            },
        }
        state.log_path = str(result.log_path)
        state.status = "completed"
    except Exception as exc:
        state.status = "failed"
        state.error = f"{exc}\n{traceback.format_exc()[-800:]}"
        logger.error("[回测] run=%s 失败: %s", state.run_id, exc)
    finally:
        state.finished_at = _now().isoformat()
        try:
            asyncio.run_coroutine_threadsafe(_save_state(db, state), loop).result(
                timeout=10
            )
        except Exception:
            pass


async def get_backtest_status(db, run_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[BT_COLLECTION].find_one({"run_id": run_id}, {"_id": 0})
    return doc


async def list_backtest_runs(db, limit: int = 20) -> List[Dict[str, Any]]:
    cursor = (
        db[BT_COLLECTION]
        .find({}, {"_id": 0, "log_path": 0, "dates": 0, "summary_data": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def get_backtest_summary(db, run_id: str) -> Optional[Dict[str, Any]]:
    """对已完成（或中断）的 run 的决策日志重算统计——重结算后刷新。"""
    from tradingagents.backtest import summarize

    doc = await db[BT_COLLECTION].find_one({"run_id": run_id})
    if not doc:
        return None
    log_path = doc.get("log_path")
    if not log_path or not Path(log_path).is_file():
        return {"success": False, "error": "回测决策日志不存在"}
    summary = summarize(Path(log_path))
    return {
        "summary_text": summary.render(),
        "summary_data": {
            "resolved": summary.resolved,
            "pending": summary.pending,
            "unscored": summary.unscored,
            "holding": summary.holding,
            "by_rating": {
                rating: {
                    "count": s.count,
                    "hit_rate": s.hit_rate,
                    "mean_alpha": s.mean_alpha,
                }
                for rating, s in summary.by_rating.items()
            },
        },
    }
