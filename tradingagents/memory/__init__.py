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
    "create_memory_log",
    "get_decision_log_path",
    "get_holding_period_days",
    "get_decision_log_max_entries",
]


def get_decision_log_path() -> str:
    """默认决策日志路径（可用 TRADINGAGENTS_DECISION_LOG_PATH 覆盖）。"""
    override = os.getenv("TRADINGAGENTS_DECISION_LOG_PATH")
    if override:
        return override
    return os.path.join("data", "decision_logs", "trading_memory.md")


def get_holding_period_days() -> int:
    """结算持有期（交易日）：优先 app Settings（DECISION_HOLDING_PERIOD_DAYS），
    退回环境变量，再退回默认 5。"""
    env_val = os.getenv("DECISION_HOLDING_PERIOD_DAYS")
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    try:
        from app.core.config import get_settings

        return max(1, int(get_settings().DECISION_HOLDING_PERIOD_DAYS))
    except Exception:
        return 5


def get_decision_log_max_entries() -> Optional[int]:
    """已结算条目轮转上限；0/负值表示不轮转（返回 None）。"""
    env_val = os.getenv("DECISION_LOG_MAX_ENTRIES")
    if env_val:
        try:
            val = int(env_val)
            return val if val > 0 else None
        except ValueError:
            pass
    try:
        from app.core.config import get_settings

        val = int(get_settings().DECISION_LOG_MAX_ENTRIES)
        return val if val > 0 else None
    except Exception:
        return None


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
    else:
        rotation = get_decision_log_max_entries()
        if rotation is not None:
            cfg["decision_log_max_entries"] = rotation
    return TradingMemoryLog(cfg)
