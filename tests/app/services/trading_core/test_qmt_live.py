"""QMT 实盘适配器与回报同步测试（无 QMT 环境，纯 mock）。

xtquant 只在装有 QMT 终端的机器上存在，测试全部通过注入假
xttrader/假配置验证逻辑分支，绝不触达真实终端。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.core.config import get_settings
from app.services.trading_core.sync import (
    _STALE_SUBMITTED_MINUTES,
    QMTOrderSyncService,
)


class FakeBroker:
    """柜台替身：按受理号返回预设委托状态。"""

    def __init__(self, orders_by_id: Dict[str, Dict[str, Any]]):
        self.orders_by_id = orders_by_id

    async def query_orders(self, order_ids=None):
        wanted = {str(x) for x in order_ids} if order_ids else None
        out = []
        for bid, o in self.orders_by_id.items():
            if wanted is None or bid in wanted:
                out.append({"broker_order_id": bid, **o})
        return {"status": "ok", "orders": out}


def _open_order(bid: str = "1001", submitted_minutes_ago: int = 5, **extra) -> Dict[str, Any]:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=submitted_minutes_ago))
    doc = {
        "_id": f"oid_{bid}",
        "user_id": "user_1",
        "signal_id": f"sig_{bid}",
        "symbol": "600519",
        "market": "CN",
        "side": "buy",
        "quantity": 100,
        "limit_price": 1500.0,
        "notional": 150000.0,
        "status": "submitted",
        "broker_order_id": bid,
        "created_at": ts.isoformat(),
        "updated_at": ts.isoformat(),
    }
    doc.update(extra)
    return doc


class FakeSyncDB:
    """只实现 sync.py 用到的 Mongo 语义。"""

    def __init__(self, orders: List[Dict[str, Any]]):
        self.orders = orders

    def __getitem__(self, name):
        if name != "trading_orders":
            raise KeyError(name)
        return self

    def find(self, query=None, *a, **k):
        wanted = (query or {}).get("status", {"$in": []})
        if isinstance(wanted, dict) and "$in" in wanted:
            docs = [o for o in self.orders if o["status"] in wanted["$in"]]
        else:
            docs = [o for o in self.orders if o["status"] == wanted]

        class C:
            def __init__(self, items):
                self.items = items

            async def __aiter__(self):
                for it in self.items:
                    yield dict(it)

        return C(docs)

    async def update_one(self, query, update, upsert=False):
        for o in self.orders:
            if o.get("_id") == query.get("_id"):
                for k, v in update.get("$set", {}).items():
                    o[k] = v
                return
        raise AssertionError(f"update_one 未找到订单: {query}")


class TestSyncOnce:
    @pytest.mark.asyncio
    async def test_fill_confirms_order(self):
        db = FakeSyncDB([_open_order("1001")])
        broker = FakeBroker({"1001": {"status_code": 54, "traded_volume": 100,
                                     "traded_price": 1501.0, "price": 1500.0}})
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(broker)
        assert result["updated"] == 1
        assert result["still_open"] == 0
        order = db.orders[0]
        assert order["status"] == "filled"
        assert order["fill"]["price"] == 1501.0
        assert order["traded_volume"] == 100

    @pytest.mark.asyncio
    async def test_cancel_marks_cancelled(self):
        db = FakeSyncDB([_open_order("1002")])
        broker = FakeBroker({"1002": {"status_code": 56, "traded_volume": 0,
                                      "traded_price": 0, "price": 1500.0}})
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(broker)
        assert result["updated"] == 1
        assert db.orders[0]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_reject_marks_failed(self):
        db = FakeSyncDB([_open_order("1003")])
        broker = FakeBroker({"1003": {"status_code": 58, "traded_volume": 0,
                                      "traded_price": 0, "price": 1500.0}})
        svc = QMTOrderSyncService(db)
        await svc.sync_once(broker)
        assert db.orders[0]["status"] == "failed"
        assert "废单" in db.orders[0].get("error", "")

    @pytest.mark.asyncio
    async def test_partial_fill_updates_traded_volume(self):
        db = FakeSyncDB([_open_order("1004")])
        broker = FakeBroker({"1004": {"status_code": 53, "traded_volume": 40,
                                      "traded_price": 1499.0, "price": 1500.0}})
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(broker)
        assert result["still_open"] == 1
        assert db.orders[0]["status"] == "partially_filled"
        assert db.orders[0]["traded_volume"] == 40

    @pytest.mark.asyncio
    async def test_broker_query_failure_no_state_change(self):
        db = FakeSyncDB([_open_order("1005")])

        class BrokenBroker:
            async def query_orders(self, order_ids=None):
                return {"status": "failed", "error": "终端断开"}

        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(BrokenBroker())
        assert result["updated"] == 0
        assert result["still_open"] == 1
        assert db.orders[0]["status"] == "submitted"  # 状态未被破坏

    @pytest.mark.asyncio
    async def test_missing_today_flagged_not_auto_flipped(self):
        db = FakeSyncDB([_open_order("9999", submitted_minutes_ago=2)])
        broker = FakeBroker({})  # 柜台查无此单
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(broker)
        # 当日单查不到 → 报错并保持原状态（不自动反转）
        assert result["errors"]
        assert db.orders[0]["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_missing_next_day_needs_review(self):
        db = FakeSyncDB([_open_order("8888", submitted_minutes_ago=1500)])  # 隔日
        broker = FakeBroker({})
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(broker)
        assert db.orders[0]["status"] == "needs_review"

    @pytest.mark.asyncio
    async def test_no_open_orders_noop(self):
        db = FakeSyncDB([])
        svc = QMTOrderSyncService(db)
        result = await svc.sync_once(FakeBroker({}))
        assert result == {"updated": 0, "still_open": 0, "errors": []}


class TestReconcile:
    @pytest.mark.asyncio
    async def test_stale_order_reported(self):
        stale_minutes = _STALE_SUBMITTED_MINUTES + 10
        db = FakeSyncDB([_open_order("7001", submitted_minutes_ago=stale_minutes)])
        svc = QMTOrderSyncService(db)
        result = await svc.reconcile(FakeBroker({}))
        assert result["stale_count"] == 1
        item = result["items"][0]
        assert item["signal_id"] == "sig_7001"

    @pytest.mark.asyncio
    async def test_fresh_order_not_reported(self):
        db = FakeSyncDB([_open_order("7002", submitted_minutes_ago=2)])
        svc = QMTOrderSyncService(db)
        result = await svc.reconcile(FakeBroker({}))
        assert result["stale_count"] == 0


class TestQMTLiveReadiness:
    def test_unconfigured_account_not_ready(self):
        from app.services.trading_core.qmt_live import validate_qmt_live_readiness

        s = get_settings()
        old = (s.LIVE_TRADING_ENABLED, s.TRADING_MODE, s.QMT_ACCOUNT_ID)
        s.LIVE_TRADING_ENABLED = False
        s.TRADING_MODE = "signal_only"
        s.QMT_ACCOUNT_ID = ""
        try:
            result = validate_qmt_live_readiness()
            assert result["ready"] is False
            assert any("QMT_ACCOUNT_ID" in p for p in result["problems"])
        finally:
            (s.LIVE_TRADING_ENABLED, s.TRADING_MODE, s.QMT_ACCOUNT_ID) = old

    def test_adapter_not_ready_without_account(self):
        from app.services.trading_core.qmt_live import QMTLiveAdapter

        s = get_settings()
        old = s.QMT_ACCOUNT_ID
        s.QMT_ACCOUNT_ID = ""
        try:
            adapter = QMTLiveAdapter()
            assert adapter.is_ready() is False
        finally:
            s.QMT_ACCOUNT_ID = old
