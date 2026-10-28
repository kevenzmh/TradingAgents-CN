"""风控闸门：信号执行前的确定性检查链。

实盘交易的第二块基石：**每条信号过五道闸门，任何一道拒绝即不下单**。
闸门全部是确定性规则（无 LLM），且带有全局 kill switch——一个开关
立即熔断所有交易。

设计原则：
- 单票仓位上限 / 单笔金额上限 / 每日笔数与金额上限 / 最低置信度 /
  信号时效 / 用户白名单，全部可配置（app.core.config TRADING_*）
- 风控状态持久化在 MongoDB，重启后限额计数仍在
- check 返回结构化结果（拒绝原因），绝不静默放行
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RISK_COLLECTION = "trading_risk_state"
_KILL_SWITCH_DOC_ID = "kill_switch"


@dataclass
class RiskCheckResult:
    """风控检查结果：通过 or 带原因的拒绝列表。"""

    approved: bool
    rejections: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.approved = False
        self.rejections.append(reason)


class RiskGate:
    """交易风控闸门（状态持久化在 MongoDB trading_risk_state 集合）。"""

    def __init__(self, db: Any):
        self._db = db

    # ---- 全局熔断 ----

    async def set_kill_switch(self, reason: str, *, off: bool = False) -> Dict[str, Any]:
        """设置/解除全局 kill switch。开启后所有执行请求立即拒绝。"""
        doc = {
            "enabled": not off,
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._db[_RISK_COLLECTION].update_one(
            {"_id": _KILL_SWITCH_DOC_ID}, {"$set": doc}, upsert=True
        )
        logger.warning("[风控] kill switch %s: %s", "解除" if off else "触发", reason)
        return doc

    async def kill_switch_active(self) -> Optional[Dict[str, Any]]:
        """读取 kill switch 状态；未设置或已解除返回 None。"""
        doc = await self._db[_RISK_COLLECTION].find_one({"_id": _KILL_SWITCH_DOC_ID})
        if doc and doc.get("enabled"):
            return doc
        return None

    # ---- 每日限额 ----

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def _daily_counters(self, user_id: str) -> Dict[str, Any]:
        doc = await self._db[_RISK_COLLECTION].find_one({"_id": f"daily_{user_id}"})
        if not doc or doc.get("date") != self._today_key():
            return {"orders": 0, "notional": 0.0}
        return {"orders": int(doc.get("orders", 0)), "notional": float(doc.get("notional", 0.0))}

    async def record_execution(self, user_id: str, notional: float) -> None:
        """成交后累计当日限额（幂等由调用方保证：只对首次成交记录）。"""
        key = f"daily_{user_id}"
        doc = await self._db[_RISK_COLLECTION].find_one({"_id": key})
        if not doc or doc.get("date") != self._today_key():
            await self._db[_RISK_COLLECTION].update_one(
                {"_id": key},
                {"$set": {"date": self._today_key(), "orders": 1, "notional": float(notional)}},
                upsert=True,
            )
        else:
            await self._db[_RISK_COLLECTION].update_one(
                {"_id": key},
                {"$inc": {"orders": 1, "notional": float(notional)}},
            )

    # ---- 主检查 ----

    async def check_signal(
        self,
        signal,
        *,
        notional: float,
        total_asset_by_currency: Dict[str, float],
        current_position_value: float,
        price: float,
    ) -> RiskCheckResult:
        """对一条信号执行全部闸门检查。

        Args:
            signal: TradeSignal
            notional: 本单金额（price * quantity）
            total_asset_by_currency: 该币种总资产（现金+持仓市值）{CNY: ...}
            current_position_value: 该票当前持仓市值
            price: 拟成交价（DryRun 用参考价，实盘用限价）
        """
        from app.core.config import get_settings
        from app.services.trading_core.signal import signal_age_minutes

        settings = get_settings()
        result = RiskCheckResult(approved=True)

        # 闸门 0：全局 kill switch
        ks = await self.kill_switch_active()
        if ks:
            result.reject(f"全局熔断已触发: {ks.get('reason', '未知原因')}")
            return result

        # 闸门 0b：总开关只把守真实委托——live 模式必须在 LIVE_TRADING_ENABLED
        # 开启时才放行；signal_only（仅记录）与 dry_run（模拟撮合，记模拟
        # 账户）不涉及真实资金，默认即可使用
        if settings.TRADING_MODE == "live" and not settings.LIVE_TRADING_ENABLED:
            result.reject("LIVE_TRADING_ENABLED=false：实盘执行未开启")

        # 闸门 1：用户白名单
        allowed = [
            uid.strip()
            for uid in (settings.TRADING_ALLOWED_USER_IDS or "").split(",")
            if uid.strip()
        ]
        if not allowed or signal.user_id not in allowed:
            result.reject(f"用户 {signal.user_id} 不在交易白名单")

        # 闸门 2：最低置信度
        if signal.confidence < settings.TRADING_MIN_CONFIDENCE:
            result.reject(
                f"置信度 {signal.confidence:.2f} 低于下限 {settings.TRADING_MIN_CONFIDENCE}"
            )

        # 闸门 3：信号时效
        age = signal_age_minutes(signal)
        result.details["signal_age_minutes"] = round(age, 1)
        if age > settings.TRADING_SIGNAL_MAX_AGE_MINUTES:
            result.reject(f"信号已过期 {age:.0f} 分钟（上限 {settings.TRADING_SIGNAL_MAX_AGE_MINUTES}）")

        # 闸门 4：单笔金额
        if notional > settings.TRADING_MAX_ORDER_NOTIONAL:
            result.reject(f"单笔金额 {notional:.2f} 超上限 {settings.TRADING_MAX_ORDER_NOTIONAL}")

        # 闸门 5：单票仓位（买入后该票市值 / 该币种总资产）
        currency_map = {"CN": "CNY", "HK": "HKD", "US": "USD"}
        currency = currency_map.get(signal.market, "CNY")
        total_asset = float(total_asset_by_currency.get(currency, 0.0))
        if signal.side == "buy" and total_asset > 0:
            projected_pct = (current_position_value + notional) / total_asset * 100
            result.details["projected_position_pct"] = round(projected_pct, 2)
            if projected_pct > settings.TRADING_MAX_POSITION_PCT:
                result.reject(
                    f"买入后单票仓位 {projected_pct:.1f}% 超上限 "
                    f"{settings.TRADING_MAX_POSITION_PCT}%"
                )

        # 闸门 6：每日限额
        counters = await self._daily_counters(signal.user_id)
        result.details["daily_orders"] = counters["orders"]
        result.details["daily_notional"] = counters["notional"]
        if counters["orders"] + 1 > settings.TRADING_MAX_DAILY_ORDERS:
            result.reject(
                f"今日委托 {counters['orders']} 笔已达上限 {settings.TRADING_MAX_DAILY_ORDERS}"
            )
        if counters["notional"] + notional > settings.TRADING_MAX_DAILY_NOTIONAL:
            result.reject(
                f"今日成交 {counters['notional']:.2f} + 本单 {notional:.2f} "
                f"超每日上限 {settings.TRADING_MAX_DAILY_NOTIONAL}"
            )

        # 闸门 7：价格健全性（防参考价异常推导出天文数量）
        if price <= 0:
            result.reject(f"成交价非法: {price}")

        if result.approved:
            logger.info(
                "[风控] 信号 %s 通过全部闸门: %s %s x%d 金额 %.2f",
                signal.signal_id, signal.side, signal.symbol, signal.quantity, notional,
            )
        else:
            logger.warning(
                "[风控] 信号 %s 被拒绝: %s", signal.signal_id, "; ".join(result.rejections)
            )
        return result

    async def status(self, user_id: str) -> Dict[str, Any]:
        """风控状态总览（API 展示用）。"""
        from app.core.config import get_settings

        settings = get_settings()
        ks = await self.kill_switch_active()
        counters = await self._daily_counters(user_id)
        return {
            "live_trading_enabled": settings.LIVE_TRADING_ENABLED,
            "trading_mode": settings.TRADING_MODE,
            "kill_switch": ks,
            "limits": {
                "max_position_pct": settings.TRADING_MAX_POSITION_PCT,
                "max_order_notional": settings.TRADING_MAX_ORDER_NOTIONAL,
                "max_daily_orders": settings.TRADING_MAX_DAILY_ORDERS,
                "max_daily_notional": settings.TRADING_MAX_DAILY_NOTIONAL,
                "min_confidence": settings.TRADING_MIN_CONFIDENCE,
                "signal_max_age_minutes": settings.TRADING_SIGNAL_MAX_AGE_MINUTES,
            },
            "today": counters,
        }
