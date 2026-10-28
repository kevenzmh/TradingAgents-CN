"""交易执行引擎：信号 → 风控 → （HITL 确认）→ 撮合 → 记账。

实盘交易的第三块基石。状态机（全部状态落 MongoDB trading_orders）：

    draft → risk_rejected          （风控闸门拒绝，终态）
    draft → pending_confirmation   （等待人工确认，HITL）
    draft → submitted              （无 HITL 模式直接提交）
    pending_confirmation → confirmed / expired / cancelled
    submitted → filled / failed

撮合适配器（ExecutionAdapter）抽象两条路径：
- DryRunAdapter：模拟撮合（复用模拟交易账户/持仓/订单集合的语义，
  与 app/routers/paper.py 的成交规则一致：市价按最新价即成）
- LiveAdapter：真实委托 —— 当前**故意未实现**：raise NotImplementedError。
  接实盘券商前必须实现并补充成交回报/撤单/对账逻辑，且默认不开启。

安全不变量（测试逐一验证）：
1. LIVE_TRADING_ENABLED=false 时，execute_signal 最多到 DryRun，绝不触达 live 适配器
2. 任何异常路径都不会绕过风控闸门
3. 订单全部带 signal_id / analysis_id 溯源，风控拒绝也留档
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ORDERS_COLLECTION = "trading_orders"


class ExecutionAdapter:
    """撮合适配器接口。"""

    name = "base"

    async def execute(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """执行订单，返回 {"status": "filled"/"failed", "price": float, ...}"""
        raise NotImplementedError


class DryRunAdapter(ExecutionAdapter):
    """模拟撮合适配器：按参考价/最新价即成，记账到模拟交易账户。

    与 paper.py 的即时成交语义一致，但走独立的 trading_orders 集合，
    与用户手动模拟交易区分开（source="trading_engine"）。
    """

    name = "dry_run"

    def __init__(self, db: Any):
        self._db = db

    async def execute(self, order: Dict[str, Any]) -> Dict[str, Any]:
        price = float(order.get("limit_price") or 0)
        if price <= 0:
            price = await self._latest_price(order["symbol"], order["market"])
        if price is None or price <= 0:
            return {"status": "failed", "error": "无法获取成交价"}

        qty = int(order["quantity"])
        notional = round(price * qty, 2)
        currency_map = {"CN": "CNY", "HK": "HKD", "US": "USD"}
        currency = currency_map.get(order["market"], "CNY")

        # 记账到模拟账户（与 paper.py 相同的集合，来源标记区分）
        acc = await self._db["paper_accounts"].find_one({"user_id": order["user_id"]})
        if not acc:
            return {"status": "failed", "error": "交易账户不存在，请先初始化模拟账户"}
        cash = acc.get("cash") or {}
        available_cash = float(cash.get(currency, 0.0)) if isinstance(cash, dict) else 0.0

        if order["side"] == "buy":
            commission = notional * 0.0003  # 与 paper.py 默认佣金率一致（有最低5元，此处简化）
            commission = max(commission, 5.0)
            total_cost = notional + commission
            if available_cash < total_cost:
                return {
                    "status": "failed",
                    "error": f"可用{currency}不足：需要 {total_cost:.2f}，可用 {available_cash:.2f}",
                }
            await self._db["paper_accounts"].update_one(
                {"user_id": order["user_id"]},
                {"$set": {f"cash.{currency}": round(available_cash - total_cost, 2)}},
            )
            await self._apply_position(order["user_id"], order["symbol"], order["market"],
                                       currency, qty, price, side="buy")
        else:
            commission = max(notional * 0.0003, 0.0)  # 卖出佣金（无印花税简化）
            await self._db["paper_accounts"].update_one(
                {"user_id": order["user_id"]},
                {"$set": {f"cash.{currency}": round(available_cash + notional - commission, 2)}},
            )
            await self._apply_position(order["user_id"], order["symbol"], order["market"],
                                       currency, qty, price, side="sell")

        await self._db["paper_trades"].insert_one({
            "user_id": order["user_id"],
            "code": order["symbol"],
            "market": order["market"],
            "currency": currency,
            "side": order["side"],
            "quantity": qty,
            "price": price,
            "amount": notional,
            "commission": commission,
            "pnl": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "trading_engine",
            "signal_id": order.get("signal_id"),
            "analysis_id": order.get("analysis_id"),
        })
        return {"status": "filled", "price": price, "notional": notional,
                "commission": commission}

    async def _apply_position(self, user_id: str, symbol: str, market: str,
                              currency: str, qty: int, price: float, side: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        pos = await self._db["paper_positions"].find_one(
            {"user_id": user_id, "code": symbol}
        )
        if side == "buy":
            if not pos:
                await self._db["paper_positions"].insert_one({
                    "user_id": user_id, "code": symbol, "market": market,
                    "currency": currency, "quantity": qty,
                    "available_qty": 0 if market == "CN" else qty,  # A股 T+1
                    "avg_cost": price, "updated_at": now_iso,
                    "source": "trading_engine",
                })
            else:
                old_qty = int(pos.get("quantity", 0))
                old_cost = float(pos.get("avg_cost", 0.0))
                new_qty = old_qty + qty
                new_avg = round((old_cost * old_qty + price * qty) / new_qty, 4)
                new_available = pos.get("available_qty", old_qty) if market == "CN" else new_qty
                await self._db["paper_positions"].update_one(
                    {"_id": pos["_id"]},
                    {"$set": {"quantity": new_qty, "available_qty": new_available,
                              "avg_cost": new_avg, "updated_at": now_iso}},
                )
        else:
            if pos:
                new_qty = int(pos.get("quantity", 0)) - qty
                if new_qty <= 0:
                    await self._db["paper_positions"].delete_one({"_id": pos["_id"]})
                else:
                    new_available = max(0, int(pos.get("available_qty", new_qty)) - qty)
                    await self._db["paper_positions"].update_one(
                        {"_id": pos["_id"]},
                        {"$set": {"quantity": new_qty, "available_qty": new_available,
                                  "updated_at": now_iso}},
                    )

    async def _latest_price(self, symbol: str, market: str) -> Optional[float]:
        """取最新价：优先实时行情集合，退回日线最后收盘。"""
        try:
            doc = await self._db["realtime_quotes"].find_one(
                {"code": symbol}, sort=[("timestamp", -1)]
            )
            if doc:
                price = float(doc.get("price") or doc.get("last") or 0)
                if price > 0:
                    return price
        except Exception:
            pass
        try:
            code6 = symbol.zfill(6)
            cursor = self._db["stock_daily_quotes"].find(
                {"symbol": code6}, {"close": 1, "trade_date": 1}
            ).sort("trade_date", -1).limit(1)
            docs = await cursor.to_list(length=1)
            if docs:
                price = float(docs[0].get("close") or 0)
                if price > 0:
                    return price
        except Exception:
            pass
        return None


class LiveAdapter(ExecutionAdapter):
    """真实委托适配器：委托受理 + 委托状态查询 + 撤单。

    具体通道为 QMT（qmt_live.QMTLiveAdapter）。与 DryRun 的关键差异：
    execute() 只保证"柜台已受理"（status=submitted + broker_order_id），
    成交确认由 sync.QMTOrderSyncService 轮询驱动，绝不在受理时就当成交。

    通道未就绪（终端未运行/账号未配置/xtquant 不可用）时显式 failed，
    不重试——低频交易节奏下由用户处理环境问题更安全。
    """

    name = "live"

    def __init__(self, broker_config: Optional[Dict[str, Any]] = None):
        from app.services.trading_core.qmt_live import QMTLiveAdapter

        self._adapter = QMTLiveAdapter()

    async def execute(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return await self._adapter.execute(order)


def get_execution_adapter(mode: str, db: Any) -> ExecutionAdapter:
    """按模式取撮合适配器。live 模式返回未实现的适配器（必然失败）。"""
    if mode == "live":
        return LiveAdapter()
    return DryRunAdapter(db)


class TradingExecutionEngine:
    """交易执行引擎：状态机 + 风控 + 撮合。"""

    def __init__(self, db: Any):
        self._db = db

    async def execute_signal(
        self,
        signal,
        *,
        require_confirmation: bool = True,
        price_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """执行一条信号：建单 → 风控 → HITL（可选）→ 撮合 → 落账。

        返回:
            {"order_id", "status", "risk": {...}, "fill": {...}}
            status ∈ risk_rejected / pending_confirmation / filled / failed

        安全不变量：
        - TRADING_MODE=signal_only：风控通过后只落 draft 订单（status=recorded），
          不做任何撮合 —— 与"只记录信号"的语义一致
        - require_confirmation=True（默认）：即使风控全过，也停在人机确认，
          确认动作由 confirm_order() 显式触发
        """
        from app.core.config import get_settings
        from app.services.trading_core.risk_gate import RiskGate
        from app.services.trading_core.signal import normalize_symbol  # noqa: F401 复核规范化

        settings = get_settings()
        gate = RiskGate(self._db)

        # 1. 建单（draft）
        price = float(price_override or signal.reference_price or 0)
        notional = round(price * signal.quantity, 2)
        order = {
            "user_id": signal.user_id,
            "signal_id": signal.signal_id,
            "analysis_id": signal.analysis_id,
            "task_id": signal.task_id,
            "symbol": signal.symbol,
            "market": signal.market,
            "side": signal.side,
            "quantity": signal.quantity,
            "limit_price": price,
            "notional": notional,
            "status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # 2. 风控闸门
        total_asset = await self._total_asset_by_currency(signal)
        position_value = await self._position_value(signal)
        risk = await gate.check_signal(
            signal,
            notional=notional,
            total_asset_by_currency=total_asset,
            current_position_value=position_value,
            price=price,
        )
        order["risk_check"] = {
            "approved": risk.approved,
            "rejections": risk.rejections,
            "details": risk.details,
        }

        if not risk.approved:
            order["status"] = "risk_rejected"
            await self._save_order(order)
            return {
                "order_id": str(order.get("_id", "")),
                "status": "risk_rejected",
                "risk": order["risk_check"],
                "fill": None,
            }

        # 3. 只记录模式：风控通过即落单，不撮合
        if settings.TRADING_MODE == "signal_only":
            order["status"] = "recorded"
            await self._save_order(order)
            return {"order_id": str(order.get("_id", "")), "status": "recorded",
                    "risk": order["risk_check"], "fill": None}

        # 4. HITL：默认等待人工确认
        if require_confirmation:
            order["status"] = "pending_confirmation"
            await self._save_order(order)
            return {"order_id": str(order.get("_id", "")),
                    "status": "pending_confirmation",
                    "risk": order["risk_check"], "fill": None}

        # 5. 直接提交（仅显式 require_confirmation=False 时）
        return await self._submit(order, gate)

    async def confirm_order(self, order_id: str, user_id: str) -> Dict[str, Any]:
        """人工确认后提交执行（HITL 的确认动作）。"""
        from app.services.trading_core.risk_gate import RiskGate

        order = await self._db[_ORDERS_COLLECTION].find_one({"_id": _to_objectid(order_id)})
        if not order:
            return {"success": False, "error": "订单不存在"}
        if order.get("user_id") != user_id:
            return {"success": False, "error": "无权操作此订单"}
        if order.get("status") != "pending_confirmation":
            return {"success": False, "error": f"订单状态异常（{order.get('status')}），无法确认"}

        # 确认时重跑风控（kill switch 可能刚刚被触发）
        gate = RiskGate(self._db)
        signal = _rebuild_signal(order)
        notional = float(order.get("notional", 0))
        total_asset = await self._total_asset_by_currency(signal)
        position_value = await self._position_value(signal)
        risk = await gate.check_signal(
            signal, notional=notional,
            total_asset_by_currency=total_asset,
            current_position_value=position_value,
            price=float(order.get("limit_price", 0)),
        )
        if not risk.approved:
            await self._db[_ORDERS_COLLECTION].update_one(
                {"_id": order["_id"]},
                {"$set": {"status": "risk_rejected",
                          "risk_check.recheck": risk.rejections}},
            )
            return {"success": False, "error": "确认时风控复审拒绝",
                    "rejections": risk.rejections}

        return await self._submit(order, gate, order_doc=order)

    async def cancel_order(self, order_id: str, user_id: str) -> Dict[str, Any]:
        """取消待确认订单。"""
        order = await self._db[_ORDERS_COLLECTION].find_one({"_id": _to_objectid(order_id)})
        if not order:
            return {"success": False, "error": "订单不存在"}
        if order.get("user_id") != user_id:
            return {"success": False, "error": "无权操作此订单"}
        if order.get("status") != "pending_confirmation":
            return {"success": False, "error": f"订单状态 {order.get('status')} 不可取消"}
        await self._db[_ORDERS_COLLECTION].update_one(
            {"_id": order["_id"]}, {"$set": {"status": "cancelled"}}
        )
        return {"success": True, "status": "cancelled"}

    # ---- 内部 ----

    async def _submit(self, order: Dict[str, Any], gate, order_doc: Optional[Dict] = None) -> Dict[str, Any]:
        """提交撮合适配器执行并落终态。

        适配器返回三种形态：
        - filled（DryRun 即时成交）：直接落成交态
        - submitted（实盘柜台受理）：落 submitted + broker_order_id，
          成交确认由 sync.QMTOrderSyncService 轮询驱动
        - failed：落失败态
        """
        from app.core.config import get_settings

        settings = get_settings()
        adapter = get_execution_adapter(settings.TRADING_MODE, self._db)

        if settings.TRADING_MODE == "live":
            # 实盘通道就绪检查前移：未就绪直接 failed，不给柜台添废单
            if not getattr(adapter, "_adapter", None) or not adapter._adapter.is_ready():
                error = "QMT 实盘通道未就绪（终端未运行/账号未配置）"
                await self._set_status(order, "failed", error)
                return {"order_id": str(order.get("_id", "")), "status": "failed",
                        "risk": order.get("risk_check"), "fill": {"error": error}}

        try:
            fill = await adapter.execute(order)
        except NotImplementedError as exc:
            await self._set_status(order, "failed", str(exc))
            return {"order_id": str(order.get("_id", "")), "status": "failed",
                    "risk": order.get("risk_check"), "fill": {"error": str(exc)}}

        if fill.get("status") == "filled":
            await self._set_status(order, "filled", None, fill)
            # 成交才计每日限额（与风控闸门的计数语义一致）
            await gate.record_execution(order["user_id"], float(fill.get("notional", order.get("notional", 0))))
            logger.info(
                "[执行] 信号 %s 成交: %s %s x%d @%.2f",
                order.get("signal_id"), order["side"], order["symbol"],
                order["quantity"], fill.get("price", 0),
            )
            return {"order_id": str(order.get("_id", "")), "status": "filled",
                    "risk": order.get("risk_check"), "fill": fill}

        if fill.get("status") == "submitted":
            # 实盘：柜台已受理，等待成交回报
            await self._set_status(order, "submitted", None, fill)
            order["status"] = "submitted"
            order["broker_order_id"] = fill.get("broker_order_id", "")
            logger.info(
                "[执行] 信号 %s 已提交柜台: %s %s x%d，受理号 %s（成交由回报同步确认）",
                order.get("signal_id"), order["side"], order["symbol"],
                order["quantity"], fill.get("broker_order_id"),
            )
            return {"order_id": str(order.get("_id", "")), "status": "submitted",
                    "risk": order.get("risk_check"), "fill": fill}

        error = fill.get("error", "未知撮合失败")
        await self._set_status(order, "failed", error)
        logger.warning("[执行] 信号 %s 撮合失败: %s", order.get("signal_id"), error)
        return {"order_id": str(order.get("_id", "")), "status": "failed",
                "risk": order.get("risk_check"), "fill": fill}

    async def _save_order(self, order: Dict[str, Any]) -> None:
        await self._db[_ORDERS_COLLECTION].insert_one(order)

    async def _set_status(self, order: Dict[str, Any], status: str,
                          error: Optional[str], fill: Optional[Dict] = None) -> None:
        oid = order.get("_id")
        if oid is None:
            return
        update: Dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            update["error"] = error
        if fill:
            update["fill"] = fill
            # 实盘受理号提为顶层字段，供回报同步按它轮询
            broker_id = fill.get("broker_order_id")
            if broker_id:
                update["broker_order_id"] = str(broker_id)
        await self._db[_ORDERS_COLLECTION].update_one({"_id": oid}, {"$set": update})

    async def _total_asset_by_currency(self, signal) -> Dict[str, float]:
        """该用户各币种总资产（现金 + 持仓市值近似）。"""
        currency_map = {"CN": "CNY", "HK": "HKD", "US": "USD"}
        currency = currency_map.get(signal.market, "CNY")
        acc = await self._db["paper_accounts"].find_one({"user_id": signal.user_id})
        cash = {}
        if acc:
            raw = acc.get("cash")
            if isinstance(raw, dict):
                cash = {k: float(v) for k, v in raw.items()}
        positions_value = 0.0
        cursor = self._db["paper_positions"].find({"user_id": signal.user_id, "currency": currency})
        async for pos in cursor:
            qty = int(pos.get("quantity", 0))
            avg = float(pos.get("avg_cost", 0.0))
            positions_value += qty * avg
        return {currency: cash.get(currency, 0.0) + positions_value}

    async def _position_value(self, signal) -> float:
        pos = await self._db["paper_positions"].find_one(
            {"user_id": signal.user_id, "code": signal.symbol}
        )
        if not pos:
            return 0.0
        return int(pos.get("quantity", 0)) * float(pos.get("avg_cost", 0.0))


def _rebuild_signal(order: Dict[str, Any]):
    """从订单文档重建 TradeSignal（确认路径复用风控检查）。"""
    from app.services.trading_core.signal import TradeSignal

    return TradeSignal(
        user_id=order["user_id"],
        symbol=order["symbol"],
        market=order["market"],
        side=order["side"],
        quantity=int(order["quantity"]),
        reference_price=float(order.get("limit_price") or 0),
        confidence=float(order.get("confidence", 1.0)),
        analysis_date=order.get("analysis_date", ""),
        action=order.get("action", ""),
        signal_id=order.get("signal_id", ""),
        created_at=order.get("created_at") or datetime.now(timezone.utc).isoformat(),
        task_id=order.get("task_id", ""),
        analysis_id=order.get("analysis_id", ""),
    )


def _to_objectid(order_id: str):
    from bson import ObjectId

    return ObjectId(order_id)
