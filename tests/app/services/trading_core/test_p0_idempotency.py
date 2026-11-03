"""P0-1/P0-3 幂等性与未知状态处理的对抗性测试。

覆盖场景（每条都对应一个真实生产故障模式）：
1. 正常路径：预占 → execute → 令牌匹配写 submitted
2. execute 后崩溃：状态已变 → 重试被跳过（不重复下单）
3. 并发重试：第二个进程预占失败 → 跳过
4. 令牌不匹配：外部改动 → 拒绝写入，返回 pending_sync
5. **状态未知**（P0-3）：委托调用异常 → status=unknown（绝不标记 failed）
"""
import pytest

from app.core.config import get_settings
from app.services.trading_core.executor import TradingExecutionEngine


# ---------- 最小 Motor 替身 ----------

class _R:
    def __init__(self, matched=0):
        self.matched_count = matched
        self.modified_count = matched


class FakeCollection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    async def find_one(self, query, *a, **k):
        for d in self._store.setdefault(self._name, []):
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        from bson import ObjectId
        doc.setdefault("_id", ObjectId())
        self._store.setdefault(self._name, []).append(doc)
        return _R(1)

    async def update_one(self, query, update, upsert=False):
        docs = self._store.setdefault(self._name, [])
        for d in docs:
            if _match(d, query):
                _apply(d, update)
                return _R(1)
        if upsert:
            new = {}
            if "_id" in query:
                new["_id"] = query["_id"]
            _apply(new, update)
            docs.append(new)
            return _R(0)
        return _R(0)

    def find(self, query, *a, **k):
        docs = [dict(d) for d in self._store.get(self._name, []) if _match(d, query)]
        return FakeCursor(docs)


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return self._docs

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeDB:
    def __init__(self):
        self.data = {}

    def __getitem__(self, name):
        return FakeCollection(self.data, name)


def _match(doc, query):
    for k, v in query.items():
        if k == "_id":
            if doc.get("_id") != v:
                return False
            continue
        dv = doc.get(k)
        if isinstance(v, dict):
            for op, val in v.items():
                if op == "$in":
                    if dv not in val:
                        return False
                elif op == "$gte":
                    if dv is None or dv < val:
                        return False
                elif op == "$lte":
                    if dv is None or dv > val:
                        return False
        elif dv != v:
            return False
    return True


def _apply(doc, update):
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                _setpath(doc, k, v)
        elif op == "$setOnInsert":
            pass  # upsert 简化：不区分
        elif op == "$inc":
            for k, v in fields.items():
                _setpath(doc, k, (doc.get(k) or 0) + v)


def _setpath(doc, key, value):
    if "." not in key:
        doc[key] = value
        return
    parts = key.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


class FakeAdapter:
    """实盘适配器替身：is_ready 恒 True，execute 由用例注入。"""

    def __init__(self, result):
        self._result = result
        self.calls = 0

        class _Inner:
            @staticmethod
            def is_ready():
                return True

        self._adapter = _Inner()

    async def execute(self, order):
        self.calls += 1
        if callable(self._result):
            return await self._result(order)
        return self._result


@pytest.fixture
def live_mode():
    """切到 live 模式，用例结束后复原。"""
    s = get_settings()
    old = (s.LIVE_TRADING_ENABLED, s.TRADING_MODE)
    s.LIVE_TRADING_ENABLED = True
    s.TRADING_MODE = "live"
    yield s
    s.LIVE_TRADING_ENABLED, s.TRADING_MODE = old


def _seed_order(db, oid, **extra):
    doc = {
        "_id": oid,
        "status": "draft",
        "user_id": "user_1",
        "symbol": "600519",
        "market": "CN",
        "side": "buy",
        "quantity": 100,
        "signal_id": "sig_1",
    }
    doc.update(extra)
    db.data.setdefault("trading_orders", []).append(doc)
    return doc


def _order_doc(db, oid):
    for d in db.data.get("trading_orders", []):
        if d.get("_id") == oid:
            return d
    return None


# ---------- 用例 ----------

@pytest.mark.asyncio
async def test_normal_path_writes_submitted(live_mode, monkeypatch):
    """正常路径：预占 → execute 返回受理号 → 令牌匹配写入 submitted"""
    from app.services.trading_core import executor as ex

    db = FakeDB()
    oid = "oid_normal"
    _seed_order(db, oid)
    adapter = FakeAdapter({"status": "submitted", "broker_order_id": "QMT_1"})
    monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

    engine = TradingExecutionEngine(db)
    result = await engine._submit(_order_doc(db, oid), gate=_StubGate())

    doc = _order_doc(db, oid)
    assert result["status"] == "submitted"
    assert doc["status"] == "submitted"
    assert doc["broker_order_id"] == "QMT_1"
    assert doc.get("retry_token"), "预占令牌未写入"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_retry_after_crash_is_skipped(live_mode, monkeypatch):
    """崩溃后重试：订单已非 draft → 预占失败 → 不重复下单"""
    from app.services.trading_core import executor as ex

    db = FakeDB()
    oid = "oid_crash"
    # 模拟：上次已提交成功（状态已推进），进程崩溃后重试
    _seed_order(db, oid, status="submitted", broker_order_id="QMT_X",
                retry_token="old_token")
    adapter = FakeAdapter({"status": "submitted", "broker_order_id": "QMT_DUP"})
    monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

    engine = TradingExecutionEngine(db)
    result = await engine._submit(_order_doc(db, oid), gate=_StubGate())

    assert result["status"] == "skipped"
    assert adapter.calls == 0, "重复下单！适配器不应被调用"
    assert _order_doc(db, oid)["broker_order_id"] == "QMT_X"


@pytest.mark.asyncio
async def test_concurrent_second_is_skipped(live_mode, monkeypatch):
    """并发：第一个提交后状态变化，第二个预占失败"""
    from app.services.trading_core import executor as ex

    db = FakeDB()
    oid = "oid_conc"
    _seed_order(db, oid)
    adapter = FakeAdapter({"status": "submitted", "broker_order_id": "QMT_C"})
    monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

    engine = TradingExecutionEngine(db)
    r1 = await engine._submit(_order_doc(db, oid), gate=_StubGate())
    r2 = await engine._submit(_order_doc(db, oid), gate=_StubGate())

    assert r1["status"] == "submitted"
    assert r2["status"] == "skipped"
    assert adapter.calls == 1, "并发导致重复下单！"


@pytest.mark.asyncio
async def test_token_mismatch_returns_pending_sync(live_mode, monkeypatch):
    """令牌被外部改动 → 拒绝写入 submitted，返回 pending_sync 交给回报同步"""
    from app.services.trading_core import executor as ex

    db = FakeDB()
    oid = "oid_token"
    _seed_order(db, oid)

    async def tamper(order):
        # 模拟并发进程在 execute 期间改了令牌
        d = _order_doc(db, oid)
        d["retry_token"] = "external"
        return {"status": "submitted", "broker_order_id": "QMT_T"}

    adapter = FakeAdapter(tamper)
    monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

    engine = TradingExecutionEngine(db)
    result = await engine._submit(_order_doc(db, oid), gate=_StubGate())

    assert result["status"] == "pending_sync"
    assert _order_doc(db, oid)["status"] != "submitted", "令牌不匹配却写了 submitted"


@pytest.mark.asyncio
async def test_unknown_status_never_marked_failed(live_mode, monkeypatch):
    """P0-3：委托调用异常 → status=unknown + 告警（绝不标记 failed）"""
    from app.services.trading_core import executor as ex

    db = FakeDB()
    oid = "oid_unknown"
    _seed_order(db, oid)
    adapter = FakeAdapter({
        "status": "unknown",
        "error": "委托调用异常，状态未知需对账核实",
        "requires_reconcile": True,
    })
    monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

    engine = TradingExecutionEngine(db)
    result = await engine._submit(_order_doc(db, oid), gate=_StubGate())

    doc = _order_doc(db, oid)
    assert result["status"] == "unknown"
    assert doc["status"] == "unknown", "未知状态被误标记为 failed —— 会导致重复下单"
    assert doc.get("requires_reconcile") is True
    alerts = db.data.get("trading_alerts", [])
    assert len(alerts) == 1 and alerts[0]["alert_type"] == "order_status_unknown"


@pytest.mark.asyncio
async def test_draft_only_can_be_preclaimed(live_mode, monkeypatch):
    """预占只接受 draft / pending_confirmation，其他状态一律跳过"""
    from app.services.trading_core import executor as ex

    for bad_status in ("filled", "cancelled", "risk_rejected", "unknown"):
        db = FakeDB()
        oid = f"oid_{bad_status}"
        _seed_order(db, oid, status=bad_status)
        adapter = FakeAdapter({"status": "submitted", "broker_order_id": "QMT_B"})
        monkeypatch.setattr(ex, "get_execution_adapter", lambda mode, d: adapter)

        engine = TradingExecutionEngine(db)
        result = await engine._submit(_order_doc(db, oid), gate=_StubGate())
        assert result["status"] == "skipped", f"{bad_status} 状态不应允许提交"
        assert adapter.calls == 0


class _StubGate:
    async def record_execution(self, user_id, notional):
        return None
