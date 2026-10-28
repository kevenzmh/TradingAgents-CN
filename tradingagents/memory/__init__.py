"""决策结算闭环：结论记录 → 到期结算（收益与 alpha）→ 反思 → 注入后续分析。

``log`` 维护条目，``settlement`` 在持有窗口走出后度量收益，``reflection``
把结果转成同类分析的教训。参考上游 TauricResearch/TradingAgents v0.7.1。
"""

import os
from typing import Optional

from tradingagents.memory.log import TradingMemoryLog, parse_rating
from tradingagents.memory.reflection import Reflector
from tradingagents.memory.settlement import (
    Settlement,
    fetch_returns,
    resolve_benchmark,
    settle_all_pending,
    settle_pending,
)

__all__ = [
    "TradingMemoryLog",
    "parse_rating",
    "Reflector",
    "Settlement",
    "fetch_returns",
    "resolve_benchmark",
    "settle_pending",
    "settle_all_pending",
]


def get_decision_log_path() -> str:
    """默认决策日志路径（可用 TRADINGAGENTS_DECISION_LOG_PATH 覆盖）。"""
    override = os.getenv("TRADINGAGENTS_DECISION_LOG_PATH")
    if override:
        return override
    return os.path.join("data", "decision_logs", "trading_memory.md")


def create_memory_log(
    log_path: Optional[str] = None,
    max_entries: Optional[int] = None,
) -> TradingMemoryLog:
    """按默认配置构建日志实例。"""
    cfg = {
        "decision_log_path": log_path or get_decision_log_path(),
    }
    if max_entries is not None:
        cfg["decision_log_max_entries"] = max_entries
    return TradingMemoryLog(cfg)
