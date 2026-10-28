"""trading_core：研究结论到交易执行的确定性通道。

signal      把结构化研究决策翻译成可审计的交易信号（无 LLM）
risk_gate   五道风控闸门 + 全局 kill switch（拒绝即不下单）
executor    状态机执行引擎：draft → 风控 → HITL 确认 → 撮合 → 落账

安全不变量：LIVE_TRADING_ENABLED 默认关闭；live 适配器故意未实现，
当前所有路径最多到 DryRun（模拟撮合）。
"""

from app.services.trading_core.signal import (
    SignalTranslationError,
    TradeSignal,
    normalize_symbol,
    translate_decision_to_signal,
)
from app.services.trading_core.risk_gate import RiskCheckResult, RiskGate
from app.services.trading_core.executor import (
    DryRunAdapter,
    LiveAdapter,
    TradingExecutionEngine,
    get_execution_adapter,
)

__all__ = [
    "SignalTranslationError",
    "TradeSignal",
    "normalize_symbol",
    "translate_decision_to_signal",
    "RiskCheckResult",
    "RiskGate",
    "DryRunAdapter",
    "LiveAdapter",
    "TradingExecutionEngine",
    "get_execution_adapter",
]
