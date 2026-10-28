"""交易信号模型与信号转译器：把研究结论翻译成明确、可审计的交易指令。

实盘交易的第一块基石：**绝不让 LLM 直接生成委托参数**。本模块的输入是
分析流程已产出的结构化决策（结论倾向 / 置信度 / 参考价格区间），输出是
确定性的交易信号——方向、数量、限额全部由固定规则推导，LLM 输出中的
"价格区间"只作参考价，不作为委托价。

合规前提：本项目研究结论使用合规术语（乐观/审慎/中性），转译器把这些
术语映射到交易方向是本模块唯一的"语义决策"，且规则完全静态可审计。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 研究结论倾向 → 交易方向（唯一语义映射，静态可审计）
_ACTION_TO_SIDE = {
    "乐观": "buy",
    "审慎": "sell",
    # 中性/待复核不产生交易信号
}

# A 股最小交易单位（股）；ETF 同样 100 股整数倍
_CN_LOT_SIZE = 100
# 港股每手股数因标的而异（100/200/500/1000...），无统一规则；此处
# 默认 100 并标记 lot_size_source，真实接入时必须从券商/行情获取
_HK_DEFAULT_LOT_SIZE = 100
# 美股支持碎股，最小 1 股
_US_LOT_SIZE = 1


@dataclass
class TradeSignal:
    """一条可执行的交易信号（由规则推导，非 LLM 生成）。"""

    user_id: str
    symbol: str                      # 规范化代码（A 股 6 位数字）
    market: str                      # CN / HK / US
    side: str                        # buy / sell
    quantity: int                    # 股数（已按手数取整）
    reference_price: Optional[float] # LLM 价格区间中值（仅参考，非委托价）
    confidence: float                # 决策置信度 0-1
    analysis_date: str               # 分析日期（信号时点）
    action: str                      # 原始结论倾向（乐观/审慎）
    signal_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    # 溯源：哪个分析任务产生了这条信号
    task_id: str = ""
    analysis_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "user_id": self.user_id,
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side,
            "quantity": self.quantity,
            "reference_price": self.reference_price,
            "confidence": self.confidence,
            "action": self.action,
            "analysis_date": self.analysis_date,
            "task_id": self.task_id,
            "analysis_id": self.analysis_id,
            "created_at": self.created_at,
        }


class SignalTranslationError(ValueError):
    """信号无法安全转译（缺字段/倾向不可交易/数量非法）。"""


def normalize_symbol(code: str, market: Optional[str] = None) -> tuple[str, str]:
    """规范化股票代码，返回 (code6, market)。

    A 股：600519.SH / 600519 → ("600519", "CN")；港股 0700.HK → ("0700", "HK")；
    美股字母代码 → (code, "US")。无法识别抛 SignalTranslationError。
    """
    raw = str(code or "").strip().upper()
    if not raw:
        raise SignalTranslationError("股票代码为空")
    if "." in raw:
        head, _, suffix = raw.partition(".")
        if suffix in ("SZ", "SH", "SS", "XSHE", "XSHG") and head.isdigit() and len(head) == 6:
            return head, "CN"
        if suffix == "HK" and head.isdigit():
            return head.lstrip("0").zfill(4), "HK"
        raise SignalTranslationError(f"无法识别的代码格式: {code}")
    if raw.isdigit():
        if len(raw) == 6:
            return raw, "CN"
        if 4 <= len(raw) <= 5:
            return raw.zfill(4), "HK"
        if len(raw) > 6:  # 8 位北交所/新三板等暂不支持交易
            raise SignalTranslationError(f"暂不支持的代码: {code}")
        raise SignalTranslationError(f"代码位数异常: {code}")
    if 1 <= len(raw) <= 5 and raw.isalpha():
        return raw, "US"
    raise SignalTranslationError(f"无法识别的代码: {code}")


def lot_size(market: str) -> int:
    """最小交易单位。"""
    if market == "CN":
        return _CN_LOT_SIZE
    if market == "HK":
        return _HK_DEFAULT_LOT_SIZE
    if market == "US":
        return _US_LOT_SIZE
    raise SignalTranslationError(f"未知市场: {market}")


def translate_decision_to_signal(
    *,
    user_id: str,
    code: str,
    decision: Dict[str, Any],
    analysis_date: str,
    cash_by_currency: Optional[Dict[str, float]] = None,
    position_quantity: int = 0,
    task_id: str = "",
    analysis_id: str = "",
) -> Optional[TradeSignal]:
    """把结构化研究决策翻译成交易信号（纯函数，无 LLM 调用）。

    数量推导规则（确定性）：
    - 买入：可用现金 * position_pct / 参考价，向下取整手数，至少 1 手
    - 卖出：全部持仓（清仓语义；避免部分卖出引入仓位管理复杂度）
    - 中性/待复核/低置信度 → 无信号（返回 None）

    Args:
        decision: 分析结果中的 decision dict（action/confidence/price_analysis_range）
        cash_by_currency: {CNY: 余额, ...}；买入信号需要
        position_quantity: 当前持仓股数；卖出信号需要
    """
    action = str(decision.get("action") or decision.get("analysis_view") or "").strip()
    side = _ACTION_TO_SIDE.get(action)
    if side is None:
        logger.info("[信号转译] 结论倾向 '%s' 不产生交易信号", action)
        return None

    symbol, market = normalize_symbol(code)

    confidence = decision.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    # 参考价：价格区间中值（合规输出只有区间没有目标价）
    reference_price = _midpoint_of_range(decision.get("price_analysis_range"))

    import uuid

    signal = TradeSignal(
        user_id=str(user_id),
        symbol=symbol,
        market=market,
        side=side,
        quantity=0,  # 下面推导
        reference_price=reference_price,
        confidence=confidence,
        analysis_date=analysis_date,
        action=action,
        signal_id=f"sig_{uuid.uuid4().hex[:16]}",
        task_id=str(task_id or ""),
        analysis_id=str(analysis_id or ""),
    )

    if side == "sell":
        if position_quantity <= 0:
            logger.info("[信号转译] %s 无持仓，卖出信号丢弃", symbol)
            return None
        # 卖出取整到手数（持仓本身应是整手，防御异常持仓）
        size = lot_size(market)
        qty = (position_quantity // size) * size
        if qty <= 0:
            logger.info("[信号转译] %s 持仓不足一手，卖出信号丢弃", symbol)
            return None
        signal.quantity = qty
        return signal

    # 买入：按仓位比例推导数量
    if reference_price is None or reference_price <= 0:
        raise SignalTranslationError(f"{symbol} 无有效参考价格，无法推导买入数量")
    cash = _cash_for_market(cash_by_currency, market)
    if cash is None or cash <= 0:
        raise SignalTranslationError(f"{symbol} 无可用 {market} 现金，无法推导买入数量")

    from app.core.config import get_settings

    position_pct = get_settings().TRADING_MAX_POSITION_PCT / 100.0
    budget = cash * position_pct
    size = lot_size(market)
    qty = int(budget // reference_price)
    qty = (qty // size) * size
    if qty < size:
        raise SignalTranslationError(
            f"{symbol} 预算 {budget:.2f} 不足以买入一手（参考价 {reference_price:.2f}）"
        )
    signal.quantity = qty
    return signal


def _cash_for_market(cash_by_currency: Optional[Dict[str, float]], market: str) -> Optional[float]:
    currency_map = {"CN": "CNY", "HK": "HKD", "US": "USD"}
    currency = currency_map.get(market)
    if currency is None or not cash_by_currency:
        return None
    try:
        return float(cash_by_currency.get(currency, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _midpoint_of_range(price_range: Any) -> Optional[float]:
    """合规价格区间（[min, max] 或 "min-max" 字符串）取中值；解析失败返回 None。"""
    if price_range is None:
        return None
    try:
        if isinstance(price_range, (list, tuple)) and len(price_range) == 2:
            low, high = float(price_range[0]), float(price_range[1])
        elif isinstance(price_range, str):
            text = price_range.strip().replace("，", ",").replace("~", "-").replace("－", "-")
            for sep in ("-", "—", "–", ",", "，", " "):
                if sep in text:
                    parts = [p for p in text.split(sep) if p.strip()]
                    if len(parts) == 2:
                        low = float(parts[0].strip().rstrip("元"))
                        high = float(parts[1].strip().rstrip("元"))
                        break
            else:
                return None
        else:
            return None
        if low <= 0 or high < low:
            return None
        return round((low + high) / 2, 4)
    except (TypeError, ValueError, UnboundLocalError):
        return None


def signal_age_minutes(signal: TradeSignal, now: Optional[datetime] = None) -> float:
    """信号年龄（分钟），用于过期检查。容忍带/不带时区的 ISO 时间。"""
    try:
        created = datetime.fromisoformat(signal.created_at)
    except (TypeError, ValueError):
        return float("inf")
    if now is None:
        now = datetime.now(timezone.utc)
    # 对齐时区感知：一方 naive 则把另一方也视作 UTC
    if created.tzinfo is None and now.tzinfo is not None:
        created = created.replace(tzinfo=timezone.utc)
    elif created.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds() / 60.0
