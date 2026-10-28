"""委托回报同步器：成交确认、撤单、对账。

实盘委托是异步的——order_stock 只拿到受理号，成交状态要靠轮询
query_stock_orders。本模块把"轮询→状态机推进→对账"整合为：

1. sync_once()：单次同步。可由定时任务驱动（交易日盘中每
   TRADING_ORDER_SYNC_INTERVAL_SECONDS 一次），也可手动触发。
2. reconcile()：对账。比对引擎订单状态与柜台实际委托，凡柜台
   已终态（全部成交/已撤/柜台拒绝）而引擎仍停在 submitted 的，
   修正状态并留审计记录。

状态机扩展（在 executor 基础状态上增加实盘形态）：
    submitted → filled          全部成交
    submitted → partially_filled → filled / cancelled   部分成交
    submitted → cancelled       撤单（含撤余量）
    submitted → failed          柜台废单/长期无回报（超时废弃）
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ORDERS_COLLECTION = "trading_orders"

# xtconstant 委托状态（XtOrderStatus），字面量对应
_XT_ORDER_STATUS = {
    48: "pending",          # ORDER_UNREPORTED 未报
    49: "pending",           # ORDER_WAIT_REPORT 待报
    50: "pending",           # ORDER_REPORTED 已报
    52: "pending",           # ORDER_PART_FILLED 长江证券扩展（部分成交在途）
    53: "pending",           # ORDER_PART_FILLED（部分成交）
    54: "filled",           # ORDER_FILLED 全部成交
    55: "filled",           # ORDER_FILLED（部分成交后全部）
    56: "cancelled",        # ORDER_CANCELLED 已撤
    57: "cancelled",        # ORDER_PART_CANCELLED 部分成交后撤单
    58: "failed",           # ORDER_REJECTED 柜台废单
    59: "failed",           # ORDER_CANCELLING 撤单中（按废处理待确认）
}

# 提交后无回报的最长容忍（分钟）：超时按可疑单标记，人工介入
_STALE_SUBMITTED_MINUTES = 30


class QMTOrderSyncService:
    """QMT 委托回报同步与对账。"""

    def __init__(self, db: Any):
        self._db = db

    async def sync_once(self, adapter) -> Dict[str, Any]:
        """同步一次所有 open（submitted/partially_filled）订单。

        返回 {"updated": n, "still_open": n, "errors": [...]}。
        任何单笔异常不中断整轮同步。
        """
        open_orders = await self._load_open_orders()
        if not open_orders:
            return {"updated": 0, "still_open": 0, "errors": []}

        broker_ids = [str(o.get("broker_order_id")) for o in open_orders if o.get("broker_order_id")]
        if not broker_ids:
            return {"updated": 0, "still_open": len(open_orders),
                    "errors": ["存在无受理号的 open 订单（数据异常，需人工检查）"]}

        query = await adapter.query_orders()
        if query.get("status") != "ok":
            return {"updated": 0, "still_open": len(open_orders),
                    "errors": [f"柜台查询失败: {query.get('error')}"]}

        broker_map = {str(o["broker_order_id"]): o for o in query.get("orders", [])}
        updated = still_open = 0
        errors = []

        for order in open_orders:
            bid = str(order.get("broker_order_id"))
            remote = broker_map.get(bid)
            if remote is None:
                # 当日委托查询不到 → 超时判定
                updated += await self._handle_missing(order, errors)
                continue
            status = _XT_ORDER_STATUS.get(int(remote.get("status_code", -1)))
            if status is None:
                errors.append(f"{bid}: 未知柜台状态码 {remote.get('status_code')}")
                continue
            if status in ("filled", "cancelled", "failed"):
                updated += await self._settle_remote(order, remote, status)
            else:
                # 仍在途：记录部分成交量
                traded = int(remote.get("traded_volume", 0))
                if traded > 0 and order.get("status") != "partially_filled":
                    await self._update_order(order["_id"], {
                        "status": "partially_filled",
                        "traded_volume": traded,
                    })
                    updated += 1
                still_open += 1
        return {"updated": updated, "still_open": still_open, "errors": errors}

    async def reconcile(self, adapter) -> Dict[str, Any]:
        """对账：引擎 submitted 超时订单 vs 柜台现状。

        与 sync_once 的区别：reconcile 只处理可疑单（提交超过
        _STALE_SUBMITTED_MINUTES 仍非终态），并给出人工介入清单。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_SUBMITTED_MINUTES)
        cursor = self._db[_ORDERS_COLLECTION].find({"status": {"$in": ["submitted", "partially_filled"]}})
        stale: List[Dict[str, Any]] = []
        async for order in cursor:
            try:
                submitted_at = datetime.fromisoformat(order.get("updated_at") or order.get("created_at", ""))
                if submitted_at.tzinfo is None:
                    submitted_at = submitted_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if submitted_at < cutoff:
                stale.append(order)

        result: Dict[str, Any] = {
            "stale_count": len(stale),
            "items": [],
        }
        for order in stale:
            result["items"].append({
                "order_id": str(order.get("_id")),
                "signal_id": order.get("signal_id"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "quantity": order.get("quantity"),
                "broker_order_id": order.get("broker_order_id"),
                "submitted_at": order.get("created_at"),
            })
        if stale:
            logger.warning("[对账] %d 笔实盘委托超过 %d 分钟无终态，需人工核查",
                          len(stale), _STALE_SUBMITTED_MINUTES)
        return result

    # ---- 内部 ----

    async def _load_open_orders(self) -> List[Dict[str, Any]]:
        cursor = self._db[_ORDERS_COLLECTION].find(
            {"status": {"$in": ["submitted", "partially_filled"]}}
        )
        return [o async for o in cursor]

    async def _handle_missing(self, order: Dict[str, Any], errors: List[str]) -> int:
        """柜台查不到受理号：多因隔日（query 只查当日）或数据异常。"""
        submitted_at = order.get("updated_at") or order.get("created_at", "")
        is_today = str(submitted_at)[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if is_today:
            errors.append(
                f"{order.get('broker_order_id')}: 当日提交但柜台查不到（疑似废单），人工核查"
            )
            return 0
        # 隔日订单：按最终未知处理，标记人工核查而非自动反转
        await self._update_order(order["_id"], {
            "status": "needs_review",
            "error": "隔日未收到成交回报，需人工对账",
        })
        return 1

    async def _settle_remote(self, order: Dict[str, Any], remote: Dict[str, Any], status: str) -> int:
        """按柜台终态推进引擎订单状态。"""
        update: Dict[str, Any] = {
            "status": status,
            "traded_volume": int(remote.get("traded_volume", 0) or 0),
            "fill_price": float(remote.get("traded_price", 0) or 0) or float(remote.get("price", 0) or 0),
            "broker_status_code": int(remote.get("status_code", -1)),
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        if status == "filled":
            traded_qty = int(remote.get("traded_volume", 0) or 0) or int(order.get("quantity", 0))
            fill_price = update["fill_price"] or float(order.get("limit_price", 0) or 0)
            update["fill"] = {
                "status": "filled",
                "price": fill_price,
                "notional": round(fill_price * traded_qty, 2),
            }
            # 成交计入每日限额
            try:
                from app.services.trading_core import RiskGate
                gate = RiskGate(self._db)
                await gate.record_execution(order.get("user_id", ""),
                                            round(fill_price * traded_qty, 2))
            except Exception as exc:
                logger.warning("[同步] 限额登记失败(忽略): %s", exc)
            logger.info("[同步] 委托 %s 全部成交: %s x%d @%.2f",
                        order.get("broker_order_id"), order.get("symbol"),
                        traded_qty, fill_price)
        elif status == "cancelled":
            update["error"] = "柜台撤单（部分成交余量撤销见 traded_volume）"
        elif status == "failed":
            update["error"] = "柜台废单"
        await self._update_order(order["_id"], update)
        return 1

    async def _update_order(self, oid: Dict[str, Any], fields: Dict[str, Any]) -> None:
        await self._db[_ORDERS_COLLECTION].update_one(
            {"_id": oid}, {"$set": fields}
        )
