"""交易核心测试：信号转译、风控闸门、执行引擎、安全不变量。

纯本地测试：Mongo 用 fakes 替代（Motor 接口仅用到 find_one/find/
insert_one/update_one 的 awaitable 语义）。核心断言是安全不变量：
默认配置下绝无撮合成交路径触达 live 适配器。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from app.core.config import get_settings
from app.services.trading_core.signal import (
    SignalTranslationError,
    TradeSignal,
    normalize_symbol,
    signal_age_minutes,
    translate_decision_to_signal,
)
from app.services.trading_core.risk_gate import RiskGate
from app.services.trading_core.executor import (
    DryRunAdapter,
    LiveAdapter,
    TradingExecutionEngine,
    get_execution_adapter,
)


# ---------- 测试替身 ----------

class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, length=None):
        return self._docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, name: str, store: "FakeDB"):
        self._name = name
        self._store = store

    def find(self, query: Dict, *a, **k) -> FakeCursor:
        docs = [d for d in self._store.data.get(self._name, []) if _match(d, query)]
        return FakeCursor(list(docs))

    async def find_one(self, query: Dict, *a, **k):
        for d in self._store.data.get(self._name, []):
            if _match(d, query):
                return _copy_id(d)
        return None

    async def insert_one(self, doc: Dict):
        from bson import ObjectId

        oid = ObjectId()
        # Motor 语义：insert_one 原地写入并给调用方的 dict 加 _id
        doc["_id"] = oid
        self._store.data.setdefault(self._name, []).append(doc)
        return type("R", (), {"inserted_id": oid})

    async def update_one(self, query: Dict, update: Dict, upsert: bool = False):
        docs = self._store.data.setdefault(self._name, [])
        for d in docs:
            if _match(d, query):
                _apply_update(d, update)
                return type("R", (), {"matched_count": 1})
        if upsert:
            doc = {k: v for k, v in query.items() if k not in ("_id",)}
            if "_id" in query:
                doc["_id"] = query["_id"]
            _apply_update(doc, update)
            docs.append(doc)
        return type("R", (), {"matched_count": 0})

    async def delete_one(self, query: Dict):
        docs = self._store.data.get(self._name, [])
        for i, d in enumerate(docs):
            if _match(d, query):
                del docs[i]
                return type("R", (), {"deleted_count": 1})
        return type("R", (), {"deleted_count": 0})


def _copy_id(d):
    return dict(d)


def _match(doc: Dict, query: Dict) -> bool:
    for k, v in query.items():
        if k not in doc:
            return False
        dv = doc[k]
        if isinstance(v, dict) and "$gt" in v:
            if not (dv > v["$gt"]):
                return False
        elif isinstance(v, dict):
            for op, val in v.items():
                if op == "$gte" and not dv >= val:
                    return False
        elif dv != v:
            return False
    return True


def _apply_update(doc: Dict, update: Dict):
    for op, fields in update.items():
        if op == "$set":
            for k, v in fields.items():
                # MongoDB 点路径语义：cash.CNY → doc["cash"]["CNY"]
                if "." in k:
                    parts = k.split(".")
                    target = doc
                    for p in parts[:-1]:
                        target = target.setdefault(p, {})
                    target[parts[-1]] = v
                else:
                    doc[k] = v
        elif op == "$inc":
            for k, v in fields.items():
                if "." in k:
                    parts = k.split(".")
                    target = doc
                    for p in parts[:-1]:
                        target = target.setdefault(p, {})
                    target[parts[-1]] = target.get(parts[-1], 0) + v
                else:
                    doc[k] = doc.get(k, 0) + v


class FakeDB:
    """Motor 数据库替身。"""

    def __init__(self):
        self.data: Dict[str, List[Dict[str, Any]]] = {}
        self.call_log: List[str] = []

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(name, self)


def _make_signal(**overrides) -> TradeSignal:
    base = dict(
        user_id="user_1",
        symbol="600519",
        market="CN",
        side="buy",
        quantity=100,
        reference_price=1500.0,
        confidence=0.85,
        analysis_date="2026-01-05",
        action="乐观",
        signal_id="sig_test",
        created_at=datetime.utcnow().isoformat(),
    )
    base.update(overrides)
    return TradeSignal(**base)


@pytest.fixture
def settings_off():
    """默认安全配置：总开关关、signal_only。"""
    s = get_settings()
    old = (s.LIVE_TRADING_ENABLED, s.TRADING_MODE, s.TRADING_ALLOWED_USER_IDS,
           s.TRADING_MIN_CONFIDENCE, s.TRADING_MAX_ORDER_NOTIONAL,
           s.TRADING_MAX_DAILY_NOTIONAL, s.TRADING_MAX_DAILY_ORDERS)
    s.LIVE_TRADING_ENABLED = False
    s.TRADING_MODE = "signal_only"
    s.TRADING_ALLOWED_USER_IDS = "user_1"
    s.TRADING_MIN_CONFIDENCE = 0.7
    s.TRADING_MAX_ORDER_NOTIONAL = 2_000_000.0
    s.TRADING_MAX_DAILY_NOTIONAL = 5_000_000.0
    s.TRADING_MAX_DAILY_ORDERS = 50
    yield s
    (s.LIVE_TRADING_ENABLED, s.TRADING_MODE, s.TRADING_ALLOWED_USER_IDS,
     s.TRADING_MIN_CONFIDENCE, s.TRADING_MAX_ORDER_NOTIONAL,
     s.TRADING_MAX_DAILY_NOTIONAL, s.TRADING_MAX_DAILY_ORDERS) = old


# ---------- 信号转译 ----------

class TestNormalizeSymbol:
    @pytest.mark.parametrize("raw,expected", [
        ("600519", ("600519", "CN")),
        ("600519.SH", ("600519", "CN")),
        ("000001.SZ", ("000001", "CN")),
        ("0700.HK", ("0700", "HK")),
        ("AAPL", ("AAPL", "US")),
    ])
    def test_normal(self, raw, expected):
        assert normalize_symbol(raw) == expected

    @pytest.mark.parametrize("bad", ["", "123456789", "60.519", "600519.XX", "abcdefg"])
    def test_rejects(self, bad):
        with pytest.raises(SignalTranslationError):
            normalize_symbol(bad)


class TestTranslateDecision:
    def test_buy_with_cash_and_range(self, settings_off):
        decision = {
            "action": "乐观",
            "confidence": 0.9,
            "price_analysis_range": [1400.0, 1600.0],
        }
        sig = translate_decision_to_signal(
            user_id="user_1", code="600519", decision=decision,
            analysis_date="2026-01-05",
            cash_by_currency={"CNY": 50_000_000.0},
        )
        assert sig is not None
        assert sig.side == "buy"
        assert sig.reference_price == 1500.0
        # 5000万 * 10% / 1500 = 3333 股 → 33 手 → 3300 股
        assert sig.quantity == 3300

    def test_neutral_no_signal(self, settings_off):
        sig = translate_decision_to_signal(
            user_id="u", code="600519", decision={"action": "中性", "confidence": 0.9},
            analysis_date="2026-01-05", cash_by_currency={"CNY": 100000},
        )
        assert sig is None

    def test_review_no_signal(self, settings_off):
        sig = translate_decision_to_signal(
            user_id="u", code="600519", decision={"action": "待复核", "confidence": 0.9},
            analysis_date="2026-01-05", cash_by_currency={"CNY": 100000},
        )
        assert sig is None

    def test_sell_without_position_dropped(self, settings_off):
        sig = translate_decision_to_signal(
            user_id="u", code="600519", decision={"action": "审慎", "confidence": 0.9},
            analysis_date="2026-01-05", position_quantity=0,
        )
        assert sig is None

    def test_sell_full_position(self, settings_off):
        sig = translate_decision_to_signal(
            user_id="u", code="600519", decision={"action": "审慎", "confidence": 0.9},
            analysis_date="2026-01-05", position_quantity=6600,
        )
        assert sig.side == "sell"
        assert sig.quantity == 6600

    def test_buy_no_price_raises(self, settings_off):
        with pytest.raises(SignalTranslationError):
            translate_decision_to_signal(
                user_id="u", code="600519", decision={"action": "乐观", "confidence": 0.9},
                analysis_date="2026-01-05", cash_by_currency={"CNY": 100000},
            )

    def test_buy_insufficient_budget_raises(self, settings_off):
        with pytest.raises(SignalTranslationError):
            translate_decision_to_signal(
                user_id="u", code="600519",
                decision={"action": "乐观", "confidence": 0.9,
                          "price_analysis_range": [1400, 1600]},
                analysis_date="2026-01-05", cash_by_currency={"CNY": 5000},
            )

    def test_price_range_string_parsed(self, settings_off):
        decision = {"action": "乐观", "confidence": 0.9,
                    "price_analysis_range": "10.00 - 12.00"}
        sig = translate_decision_to_signal(
            user_id="u", code="600519", decision=decision,
            analysis_date="2026-01-05", cash_by_currency={"CNY": 100_000.0},
        )
        assert sig is not None
        assert sig.reference_price == 11.0


class TestSignalAge:
    def test_fresh(self):
        sig = _make_signal()
        assert signal_age_minutes(sig) < 1

    def test_stale(self):
        sig = _make_signal(
            created_at=(datetime.utcnow() - timedelta(minutes=90)).isoformat()
        )
        assert signal_age_minutes(sig) == pytest.approx(90, abs=1)


# ---------- 风控闸门 ----------

class TestRiskGate:
    @pytest.mark.asyncio
    async def test_all_gates_pass(self, settings_off):
        settings_off.LIVE_TRADING_ENABLED = True  # 限额闸门本身的开门测试
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(confidence=0.85)
        result = await gate.check_signal(
            sig, notional=990_000.0,
            total_asset_by_currency={"CNY": 10_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert result.approved is True
        assert result.rejections == []

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_everything(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        await gate.set_kill_switch("测试熔断")
        sig = _make_signal()
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert result.approved is False
        assert any("熔断" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_kill_switch_off_restores(self, settings_off):
        settings_off.LIVE_TRADING_ENABLED = True
        db = FakeDB()
        gate = RiskGate(db)
        await gate.set_kill_switch("触发")
        await gate.set_kill_switch("解除", off=True)
        sig = _make_signal(confidence=0.85)
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_live_disabled_rejected_for_live_mode(self, settings_off):
        """LIVE off + live 模式 → 拒绝；dry_run/signal_only 不受该闸门限制。"""
        settings_off.TRADING_MODE = "live"
        try:
            db = FakeDB()
            gate = RiskGate(db)
            sig = _make_signal()
            result = await gate.check_signal(
                sig, notional=1000.0,
                total_asset_by_currency={"CNY": 1_000_000.0},
                current_position_value=0.0, price=1500.0,
            )
            assert any("LIVE_TRADING_ENABLED=false" in r for r in result.rejections)
        finally:
            settings_off.TRADING_MODE = "signal_only"

    @pytest.mark.asyncio
    async def test_user_not_in_whitelist(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(user_id="intruder", confidence=0.85)
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert any("白名单" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_low_confidence_rejected(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(confidence=0.5)
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert any("置信度" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_stale_signal_rejected(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(
            created_at=(datetime.utcnow() - timedelta(hours=3)).isoformat()
        )
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert any("过期" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_order_notional_limit(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(confidence=0.85)
        result = await gate.check_signal(
            sig, notional=2_500_000.0,
            total_asset_by_currency={"CNY": 100_000_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert any("单笔金额" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_position_pct_limit(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        sig = _make_signal(confidence=0.85)
        # 已持有 9% + 本单 9.9% → 18.9% > 10%
        result = await gate.check_signal(
            sig, notional=99_000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=90_000.0, price=1500.0,
        )
        assert any("仓位" in r for r in result.rejections)
        assert result.details["projected_position_pct"] == pytest.approx(18.9, abs=0.1)

    @pytest.mark.asyncio
    async def test_daily_counters_and_limit(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        # 预填当日计数
        await db["trading_risk_state"].update_one(
            {"_id": "daily_user_1"},
            {"$set": {"date": datetime.utcnow().strftime("%Y-%m-%d"),
                      "orders": 50, "notional": 0.0}},
            upsert=True,
        )
        sig = _make_signal(confidence=0.85)
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        assert any("每日" in r or "笔" in r for r in result.rejections)

    @pytest.mark.asyncio
    async def test_daily_counters_reset_next_day(self, settings_off):
        db = FakeDB()
        gate = RiskGate(db)
        await db["trading_risk_state"].update_one(
            {"_id": "daily_user_1"},
            {"$set": {"date": "2000-01-01", "orders": 99, "notional": 99_999_999.0}},
            upsert=True,
        )
        sig = _make_signal(confidence=0.85)
        result = await gate.check_signal(
            sig, notional=1000.0,
            total_asset_by_currency={"CNY": 1_000_000.0},
            current_position_value=0.0, price=1500.0,
        )
        # 旧日期计数不计入
        assert result.details["daily_orders"] == 0


# ---------- 执行引擎 ----------

class TestExecutionEngine:
    @pytest.mark.asyncio
    async def test_signal_only_mode_records_without_fill(self, settings_off):
        """安全不变量 #1：signal_only 模式风控通过后只落 recorded，不撮合。"""
        db = FakeDB()
        engine = TradingExecutionEngine(db)
        sig = _make_signal(confidence=0.85)
        result = await engine.execute_signal(sig, require_confirmation=True)
        assert result["status"] == "recorded"
        orders = db.data.get("trading_orders", [])
        assert len(orders) == 1
        assert orders[0]["status"] == "recorded"
        # 绝无交易流水产生
        assert db.data.get("paper_trades") in (None, [])

    @pytest.mark.asyncio
    async def test_dry_run_requires_confirmation(self, settings_off):
        """dry_run 模式默认等待人工确认，不直接成交。"""
        s = settings_off
        s.TRADING_MODE = "dry_run"
        try:
            db = FakeDB()
            # 预置账户与现金
            await db["paper_accounts"].update_one(
                {"user_id": "user_1"},
                {"$set": {"cash": {"CNY": 2_000_000.0}}},
                upsert=True,
            )
            engine = TradingExecutionEngine(db)
            sig = _make_signal(confidence=0.85)
            result = await engine.execute_signal(sig, require_confirmation=True)
            assert result["status"] == "pending_confirmation"
            assert db.data.get("paper_trades") in (None, [])
        finally:
            s.TRADING_MODE = "signal_only"

    @pytest.mark.asyncio
    async def test_dry_run_confirm_fills(self, settings_off):
        """确认后（重跑风控）DryRun 撮合成交并记账。"""
        s = settings_off
        s.TRADING_MODE = "dry_run"
        try:
            db = FakeDB()
            await db["paper_accounts"].update_one(
                {"user_id": "user_1"},
                {"$set": {"cash": {"CNY": 2_000_000.0}}},
                upsert=True,
            )
            engine = TradingExecutionEngine(db)
            sig = _make_signal(confidence=0.85)
            result = await engine.execute_signal(sig, require_confirmation=True)
            order_id = result["order_id"]

            outcome = await engine.confirm_order(order_id, "user_1")
            assert outcome.get("status") == "filled"
            trades = db.data.get("paper_trades", [])
            assert len(trades) == 1
            assert trades[0]["source"] == "trading_engine"
            positions = db.data.get("paper_positions", [])
            assert positions and positions[0]["quantity"] == 100
            # A股 T+1：新买入不可用
            assert positions[0]["available_qty"] == 0
            # 现金扣减
            acc = db.data["paper_accounts"][0]
            assert acc["cash"]["CNY"] < 2_000_000.0
        finally:
            s.TRADING_MODE = "signal_only"

    @pytest.mark.asyncio
    async def test_confirm_reruns_risk(self, settings_off):
        """确认时 kill switch 已触发 → 复审拒绝，不成交。"""
        s = settings_off
        s.TRADING_MODE = "dry_run"
        try:
            db = FakeDB()
            await db["paper_accounts"].update_one(
                {"user_id": "user_1"}, {"$set": {"cash": {"CNY": 2_000_000.0}}}, upsert=True,
            )
            engine = TradingExecutionEngine(db)
            sig = _make_signal(confidence=0.85)
            result = await engine.execute_signal(sig, require_confirmation=True)
            order_id = result["order_id"]

            from app.services.trading_core import RiskGate
            await RiskGate(db).set_kill_switch("确认前熔断")

            outcome = await engine.confirm_order(order_id, "user_1")
            assert outcome.get("success") is False
            assert db.data.get("paper_trades") in (None, [])
        finally:
            s.TRADING_MODE = "signal_only"

    @pytest.mark.asyncio
    async def test_risk_rejected_order_persisted(self, settings_off):
        """风控拒绝也留档（审计要求）。"""
        db = FakeDB()
        engine = TradingExecutionEngine(db)
        sig = _make_signal(confidence=0.3)  # 低置信度必拒
        result = await engine.execute_signal(sig, require_confirmation=True)
        assert result["status"] == "risk_rejected"
        orders = db.data.get("trading_orders", [])
        assert len(orders) == 1
        assert orders[0]["status"] == "risk_rejected"
        assert orders[0]["risk_check"]["rejections"]

    @pytest.mark.asyncio
    async def test_wrong_user_cannot_confirm(self, settings_off):
        db = FakeDB()
        engine = TradingExecutionEngine(db)
        sig = _make_signal(confidence=0.85)
        result = await engine.execute_signal(sig, require_confirmation=True)
        outcome = await engine.confirm_order(result["order_id"], "other_user")
        assert outcome["success"] is False


class TestAdapters:
    @pytest.mark.asyncio
    async def test_live_adapter_unready_never_submits(self, settings_off):
        """安全不变量 #2：QMT 通道未就绪（无账号/无 xtquant）时 execute 必然
        返回 failed，不可能发出真实委托。"""
        settings_off.LIVE_TRADING_ENABLED = True  # 即使总开关打开
        adapter = LiveAdapter()
        result = await adapter.execute({"symbol": "600519", "market": "CN",
                                       "side": "buy", "quantity": 100,
                                       "user_id": "u", "signal_id": "sig_x"})
        assert result["status"] == "failed"
        assert "未就绪" in result["error"] or "QMT" in result["error"]

    def test_get_adapter_by_mode(self):
        db = FakeDB()
        assert isinstance(get_execution_adapter("dry_run", db), DryRunAdapter)
        assert isinstance(get_execution_adapter("live", db), LiveAdapter)
        assert isinstance(get_execution_adapter("signal_only", db), DryRunAdapter)


class TestDryRunAdapter:
    @pytest.mark.asyncio
    async def test_insufficient_cash_fails_cleanly(self):
        db = FakeDB()
        await db["paper_accounts"].update_one(
            {"user_id": "u1"}, {"$set": {"cash": {"CNY": 100.0}}}, upsert=True,
        )
        adapter = DryRunAdapter(db)
        result = await adapter.execute({
            "user_id": "u1", "symbol": "600519", "market": "CN",
            "side": "buy", "quantity": 100, "limit_price": 1500.0,
        })
        assert result["status"] == "failed"
        assert "不足" in result["error"]
        assert db.data.get("paper_trades") in (None, [])

    @pytest.mark.asyncio
    async def test_sell_reduces_position(self):
        db = FakeDB()
        await db["paper_positions"].insert_one({
            "user_id": "u1", "code": "600519", "market": "CN", "currency": "CNY",
            "quantity": 200, "available_qty": 200, "avg_cost": 100.0,
        })
        await db["paper_accounts"].update_one(
            {"user_id": "u1"}, {"$set": {"cash": {"CNY": 0.0}}}, upsert=True,
        )
        adapter = DryRunAdapter(db)
        result = await adapter.execute({
            "user_id": "u1", "symbol": "600519", "market": "CN",
            "side": "sell", "quantity": 200, "limit_price": 110.0,
        })
        assert result["status"] == "filled"
        assert db.data.get("paper_positions") == []  # 清仓删除持仓
        assert db.data["paper_trades"][0]["side"] == "sell"
