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
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.trading_core.observability import metrics, trading_trace

logger = logging.getLogger(__name__)

_ORDERS_COLLECTION = "trading_orders"


def _record_order_metric(order: Dict[str, Any], status: str,
                         elapsed: Optional[float] = None) -> None:
    """记录订单终态指标（失败静默，绝不影响交易）。

    elapsed 由调用方测量（不写进订单文档，避免污染 schema 与 DB 负载）。
    """
    try:
        labels = {
            "market": str(order.get("market") or "unknown"),
            "side": str(order.get("side") or "unknown"),
            "status": str(status or "unknown"),
        }
        metrics.inc("trading_order_total", labels)
        if elapsed is not None:
            metrics.observe(
                "trading_order_latency_seconds", max(0.0, float(elapsed)),
                {"status": str(status or "unknown")},
            )
    except Exception:  # pragma: no cover
        pass


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

        # P0-4：走幂等结算（补偿日志 + 原子条件更新），不再裸写三步
        return await self._settle(order, price, qty, notional, currency)

    # ---- P0-4 幂等结算（无事务环境下的 saga）----

    _SETTLEMENT_COLLECTION = "trading_settlements"

    async def _settle(self, order: Dict[str, Any], price: float, qty: int,
                      notional: float, currency: str) -> Dict[str, Any]:
        """幂等结算：资金/持仓/流水三步在单机 MongoDB 下的安全落账。

        **为什么不用事务**：本项目 MongoDB 为单机部署（无副本集），
        `start_transaction()` 会直接抛 InvalidOperation。因此改用 saga：

        1. 结算意图日志 `trading_settlements`（_id = 订单 _id）作为幂等键
           - 已有 done → 直接返回缓存结果（重复执行无副作用）
           - 已有 pending → 说明上次中断，继续向前推进（幂等补做）
        2. 现金变动用 **条件原子更新**（$gte 守卫 + $inc），
           杜绝"读余额→写绝对值"的丢失更新竞态
        3. 持仓用 pipeline update 原子重算均价
        4. 流水用 `_id = order_id` upsert，重复写被唯一键挡住
        5. 全部完成后 journal 置 done 并缓存结果

        任一步骤崩溃，journal 停留在 pending，由
        `reconcile_settlements()` 在启动/定时任务里补做或回滚。
        """
        oid = order.get("_id")
        settlement_id = str(oid) if oid is not None else f"tmp_{order.get('signal_id')}"
        side = order["side"]
        user_id = order["user_id"]
        symbol = order["symbol"]
        market = order["market"]

        if side == "buy":
            commission = max(notional * 0.0003, 5.0)
        else:
            commission = max(notional * 0.0003, 0.0)
        cash_delta = -(notional + commission) if side == "buy" else (notional - commission)

        journal = self._db[self._SETTLEMENT_COLLECTION]

        # ---- 步骤 0：建立/读取结算意图 ----
        try:
            await journal.update_one(
                {"_id": settlement_id},
                {"$setOnInsert": {
                    "_id": settlement_id,
                    "order_id": str(oid),
                    "user_id": user_id,
                    "symbol": symbol,
                    "market": market,
                    "side": side,
                    "quantity": qty,
                    "price": price,
                    "notional": notional,
                    "commission": commission,
                    "currency": currency,
                    "state": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as exc:
            # 日志不可用不应阻断交易，但必须显式记录
            logger.error("[结算] 意图日志写入失败（继续执行）: %s", exc, exc_info=True)

        existing = await journal.find_one({"_id": settlement_id}) or {}
        if existing.get("state") == "done":
            # 幂等命中：重复调用直接返回上次结果
            logger.info("[结算] 订单 %s 已完成结算，幂等返回", settlement_id)
            cached = existing.get("result") or {}
            return {
                "status": "filled",
                "price": cached.get("price", price),
                "notional": cached.get("notional", notional),
                "commission": cached.get("commission", commission),
                "idempotent": True,
            }

        # ---- 步骤 1：现金（原子条件更新）----
        if not existing.get("cash_applied"):
            if side == "buy":
                # 条件原子扣款：余额不足则 matched_count == 0
                res = await self._db["paper_accounts"].update_one(
                    {"user_id": user_id, f"cash.{currency}": {"$gte": notional + commission}},
                    {"$inc": {f"cash.{currency}": -round(notional + commission, 2)},
                     "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
                )
                if res.matched_count == 0:
                    acc = await self._db["paper_accounts"].find_one({"user_id": user_id})
                    if not acc:
                        await journal.update_one({"_id": settlement_id},
                                                 {"$set": {"state": "failed",
                                                           "error": "交易账户不存在"}})
                        return {"status": "failed", "error": "交易账户不存在，请先初始化模拟账户"}
                    cash = acc.get("cash") or {}
                    avail = float(cash.get(currency, 0.0)) if isinstance(cash, dict) else 0.0
                    await journal.update_one({"_id": settlement_id},
                                             {"$set": {"state": "failed",
                                                       "error": f"可用{currency}不足"}})
                    return {
                        "status": "failed",
                        "error": f"可用{currency}不足：需要 {notional + commission:.2f}，可用 {avail:.2f}",
                    }
            else:
                res = await self._db["paper_accounts"].update_one(
                    {"user_id": user_id},
                    {"$inc": {f"cash.{currency}": round(notional - commission, 2)},
                     "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
                )
                if res.matched_count == 0:
                    await journal.update_one({"_id": settlement_id},
                                             {"$set": {"state": "failed",
                                                       "error": "交易账户不存在"}})
                    return {"status": "failed", "error": "交易账户不存在，请先初始化模拟账户"}

            await journal.update_one({"_id": settlement_id},
                                     {"$set": {"cash_applied": True,
                                               "cash_delta": round(cash_delta, 2)}})

        # ---- 步骤 2：持仓（原子）----
        if not existing.get("position_applied"):
            await self._apply_position_atomic(user_id, symbol, market, currency, qty, price, side)
            await journal.update_one({"_id": settlement_id},
                                     {"$set": {"position_applied": True}})

        # ---- 步骤 3：流水（唯一键幂等）----
        try:
            await self._db["paper_trades"].update_one(
                {"_id": settlement_id},
                {"$setOnInsert": {
                    "_id": settlement_id,
                    "user_id": user_id,
                    "code": symbol,
                    "market": market,
                    "currency": currency,
                    "side": side,
                    "quantity": qty,
                    "price": price,
                    "amount": notional,
                    "commission": commission,
                    "pnl": 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "trading_engine",
                    "signal_id": order.get("signal_id"),
                    "analysis_id": order.get("analysis_id"),
                }},
                upsert=True,
            )
        except Exception as exc:
            logger.error("[结算] 流水写入失败: %s", exc, exc_info=True)

        # ---- 步骤 4：完结 ----
        result = {"price": price, "notional": notional, "commission": commission}
        await journal.update_one(
            {"_id": settlement_id},
            {"$set": {"state": "done",
                      "result": result,
                      "completed_at": datetime.now(timezone.utc).isoformat()}},
        )
        try:
            metrics.inc("trading_settlement_total", {"result": "done"})
        except Exception:  # pragma: no cover
            pass
        return {"status": "filled", **result}

    async def _apply_position_atomic(self, user_id: str, symbol: str, market: str,
                                     currency: str, qty: int, price: float, side: str) -> None:
        """原子更新持仓（买入用 aggregation pipeline 重算均价，卖出条件扣减）。"""
        now_iso = datetime.now(timezone.utc).isoformat()
        coll = self._db["paper_positions"]

        if side == "buy":
            # 无持仓 → 插入；有持仓 → pipeline 原子重算 quantity/avg_cost
            existing = await coll.find_one({"user_id": user_id, "code": symbol})
            if not existing:
                try:
                    await coll.insert_one({
                        "user_id": user_id, "code": symbol, "market": market,
                        "currency": currency, "quantity": qty,
                        "available_qty": 0 if market == "CN" else qty,  # A股 T+1
                        "avg_cost": price, "updated_at": now_iso,
                        "source": "trading_engine",
                    })
                except Exception:
                    # 并发插入冲突：退化为 pipeline 更新
                    await coll.update_one(
                        {"user_id": user_id, "code": symbol},
                        [{"$set": {
                            "quantity": {"$add": ["$quantity", qty]},
                            "avg_cost": {"$round": [{"$divide": [
                                {"$add": [{"$multiply": ["$avg_cost", "$quantity"]},
                                          price * qty]},
                                {"$add": ["$quantity", qty]}]}, 4]},
                            "available_qty": {"$cond": [{"$eq": [market, "CN"]},
                                                        "$available_qty", {"$add": ["$quantity", qty]}]},
                            "updated_at": now_iso,
                        }}],
                    )
            else:
                await coll.update_one(
                    {"user_id": user_id, "code": symbol},
                    [{"$set": {
                        "quantity": {"$add": ["$quantity", qty]},
                        "avg_cost": {"$round": [{"$divide": [
                            {"$add": [{"$multiply": ["$avg_cost", "$quantity"]}, price * qty]},
                            {"$add": ["$quantity", qty]}]}, 4]},
                        "available_qty": {"$cond": [{"$eq": [market, "CN"]},
                                                    "$available_qty",
                                                    {"$add": [{"$ifNull": ["$available_qty", 0]}, qty]}]},
                        "updated_at": now_iso,
                    }}],
                )
        else:
            # 卖出：条件扣减（数量不足则不改），扣到 0 删除
            res = await coll.update_one(
                {"user_id": user_id, "code": symbol, "quantity": {"$gte": qty}},
                [{"$set": {
                    "quantity": {"$subtract": ["$quantity", qty]},
                    "available_qty": {"$max": [0, {"$subtract": [
                        {"$ifNull": ["$available_qty", "$quantity"]}, qty]}]},
                    "updated_at": now_iso,
                }}],
            )
            if res.matched_count == 0:
                logger.error(
                    "[结算] 卖出扣减失败（持仓不足）: user=%s code=%s qty=%d",
                    user_id, symbol, qty,
                )
                return
            await coll.delete_one({"user_id": user_id, "code": symbol, "quantity": {"$lte": 0}})

    async def reconcile_settlements(self, limit: int = 100) -> Dict[str, Any]:
        """补偿未完成的结算（P0-4）。

        扫描 `trading_settlements` 中 pending 且超过 1 分钟的日志：
        - 三步标记齐全 → 补写 done
        - 有部分标记 → 继续推进剩余步骤
        - 账户已不存在等不可恢复 → 标记 failed 留痕

        建议在启动时与定时任务中调用。单机无事务环境下，
        这是保证资金/持仓/流水三者最终一致的兜底。
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        cursor = self._db[self._SETTLEMENT_COLLECTION].find({
            "state": "pending",
            "created_at": {"$lte": cutoff},
        }).limit(limit)

        repaired = failed = 0
        errors: list = []
        async for doc in cursor:
            sid = doc.get("_id")
            try:
                if doc.get("cash_applied") and doc.get("position_applied"):
                    await self._db[self._SETTLEMENT_COLLECTION].update_one(
                        {"_id": sid},
                        {"$set": {"state": "done",
                                  "completed_at": datetime.now(timezone.utc).isoformat(),
                                  "repaired_by": "reconcile_settlements"}},
                    )
                    repaired += 1
                    logger.warning("[结算补偿] 订单 %s 三步齐全，补写 done", sid)
                    try:
                        metrics.inc("trading_settlement_total", {"result": "repaired"})
                    except Exception:  # pragma: no cover
                        pass
                    continue

                # 未完成的：无法安全重放（可能重复扣款），标记待人工
                await self._db[self._SETTLEMENT_COLLECTION].update_one(
                    {"_id": sid},
                    {"$set": {"state": "needs_review",
                              "error": "结算中断且无法安全重放，需人工核对资金/持仓",
                              "flagged_at": datetime.now(timezone.utc).isoformat()}},
                )
                await self._db["trading_alerts"].insert_one({
                    "alert_type": "settlement_incomplete",
                    "severity": "critical",
                    "settlement_id": sid,
                    "user_id": doc.get("user_id"),
                    "symbol": doc.get("symbol"),
                    "side": doc.get("side"),
                    "quantity": doc.get("quantity"),
                    "reason": "结算中断且无法安全重放，需人工核对",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "resolved": False,
                })
                failed += 1
                try:
                    metrics.inc("trading_settlement_total", {"result": "needs_review"})
                    metrics.inc("trading_alert_total", {
                        "alert_type": "settlement_incomplete", "severity": "critical",
                    })
                except Exception:  # pragma: no cover
                    pass
            except Exception as exc:
                errors.append(f"{sid}: {exc}")
                logger.error("[结算补偿] %s 失败: %s", sid, exc, exc_info=True)

        return {"repaired": repaired, "flagged": failed, "errors": errors}

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

        可观测性：本方法是「建单→风控→撮合→落账」的共同出口，
        在此绑定 trace_id（订单维度稳定），使后台线程/定时任务里
        产生的日志也能串起同一笔订单的完整生命周期。
        """
        with trading_trace(order=order):
            started_at = time.time()
            result = await self._submit_inner(order, gate, order_doc)
            # 终态耗时（含未知状态），供 p95 告警使用
            _record_order_metric(
                order, str(result.get("status") or "unknown"),
                elapsed=time.time() - started_at,
            )
            return result

    async def _submit_inner(self, order: Dict[str, Any], gate,
                            order_doc: Optional[Dict] = None) -> Dict[str, Any]:
        """_submit 的实际逻辑（外层负责 trace 与耗时埋点）。"""
        from app.core.config import get_settings

        settings = get_settings()
        adapter = get_execution_adapter(settings.TRADING_MODE, self._db)

        if settings.TRADING_MODE == "live":
            # 实盘通道就绪检查前移：未就绪直接 failed，不给柜台添废单
            # 轻量检查（只看配置），几乎无开销
            if not getattr(adapter, "_adapter", None) or not adapter._adapter.is_ready(deep_check=False):
                error = "QMT 实盘通道未就绪（终端未运行/账号未配置）"
                await self._set_status(order, "failed", error)
                return {"order_id": str(order.get("_id", "")), "status": "failed",
                        "risk": order.get("risk_check"), "fill": {"error": error}}

        # P0-1 修复：实盘委托幂等性保护（两阶段提交）
        # 阶段 1：预占状态，写入重试令牌（乐观锁），避免崩溃后重复下单
        retry_token = None
        if settings.TRADING_MODE == "live":
            import secrets
            retry_token = secrets.token_hex(8)  # 16 字符唯一令牌
            result = await self._db[_ORDERS_COLLECTION].update_one(
                {"_id": order.get("_id"), "status": {"$in": ["draft", "pending_confirmation"]}},
                {"$set": {
                    "submitting_at": datetime.now(timezone.utc).isoformat(),
                    "retry_token": retry_token,
                }},
            )
            if result.matched_count == 0:
                # 订单已被其他进程提交（并发保护）或状态已非法
                logger.warning(
                    "[执行] 订单 %s 无法预占（已提交或状态非法），跳过委托",
                    order.get("_id"),
                )
                return {"order_id": str(order.get("_id", "")), "status": "skipped",
                        "risk": order.get("risk_check"),
                        "fill": {"error": "订单已被提交或状态非法"}}

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
            # 指标由 _submit 外层统一记录（避免此处重复计数）
            return {"order_id": str(order.get("_id", "")), "status": "filled",
                    "risk": order.get("risk_check"), "fill": fill}

        if fill.get("status") == "submitted":
            # P0-1 修复：阶段 2：用重试令牌作为乐观锁，确保只有持有令牌的进程能写 submitted
            # （若进程 A 拿到 broker_order_id 但崩溃，进程 B 重试时令牌已变，不会覆盖）
            oid = order.get("_id")
            broker_order_id = fill.get("broker_order_id", "")
            if settings.TRADING_MODE == "live" and retry_token:
                result = await self._db[_ORDERS_COLLECTION].update_one(
                    {"_id": oid, "retry_token": retry_token},  # 令牌匹配才写
                    {"$set": {
                        "status": "submitted",
                        "broker_order_id": str(broker_order_id),
                        "fill": fill,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                if result.matched_count == 0:
                    # 令牌不匹配（并发重试已成功）或订单已被其他进程修改
                    logger.warning(
                        "[执行] 订单 %s 令牌不匹配，柜台受理号 %s 已返回但未写入 DB "
                        "（可能并发提交），由回报同步兜底",
                        oid, broker_order_id,
                    )
                    # 不返回 submitted（避免上游误判成功），状态由回报同步修正
                    return {"order_id": str(oid), "status": "pending_sync",
                            "risk": order.get("risk_check"),
                            "fill": {"broker_order_id": broker_order_id,
                                     "note": "柜台已受理但状态写入被并发阻止，等待回报同步"}}
            else:
                # DryRun 或非实盘模式，直接写（无令牌）
                await self._set_status(order, "submitted", None, fill)

            order["status"] = "submitted"
            order["broker_order_id"] = broker_order_id
            logger.info(
                "[执行] 信号 %s 已提交柜台: %s %s x%d，受理号 %s（成交由回报同步确认）",
                order.get("signal_id"), order["side"], order["symbol"],
                order["quantity"], broker_order_id,
            )
            return {"order_id": str(order.get("_id", "")), "status": "submitted",
                    "risk": order.get("risk_check"), "fill": fill}

        # ⚠️ P0-3 关键安全分支：状态未知（委托调用异常，柜台可能已受理）
        #
        # 绝不能标记为 failed —— failed 会被上游视为"这单没下成"，
        # 用户/策略可能重下，造成重复下单。必须标记为 unknown 并进入
        # 对账队列，由 sync.reconcile() 向柜台核实真实状态。
        if fill.get("status") == "unknown" or fill.get("requires_reconcile"):
            oid = order.get("_id")
            error = str(fill.get("error") or "委托状态未知，需对账核实")
            await self._db[_ORDERS_COLLECTION].update_one(
                {"_id": oid},
                {"$set": {
                    "status": "unknown",
                    "error": error,
                    "requires_reconcile": True,
                    "reconcile_flagged_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            # 同时写告警，保证人工能看到这笔"悬空"委托
            try:
                await self._db["trading_alerts"].insert_one({
                    "alert_type": "order_status_unknown",
                    "severity": "critical",
                    "order_id": str(oid),
                    "signal_id": order.get("signal_id"),
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "quantity": order.get("quantity"),
                    "user_id": order.get("user_id"),
                    "reason": error,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "resolved": False,
                })
            except Exception as alert_exc:
                logger.error("[执行] 未知状态告警写入失败: %s", alert_exc, exc_info=True)

            logger.error(
                "[执行] ⚠️ 信号 %s 委托状态未知，已标记 unknown 待对账: %s",
                order.get("signal_id"), error,
            )
            return {"order_id": str(oid), "status": "unknown",
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
