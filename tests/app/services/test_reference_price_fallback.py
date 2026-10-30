"""阻断级修复的回归测试：参考价兜底与 point-in-time 财报读取。

- 信号转译无价格区间时按分析日收盘价兜底（不让可推导参数卡死链路）
- 历史分析日的基本面工具输出财报比率（有真实报告期 vintage，非未来信息）
- 风险经理 schema 乐观/审慎必须输出价格区间
"""

import types
import sys

import pytest

try:  # 本机可能未装 motor
    import motor  # noqa: F401
except ModuleNotFoundError:
    _motor_mod = types.ModuleType("motor")
    _motor_asyncio = types.ModuleType("motor.motor_asyncio")
    for _cls_name in ("AsyncIOMotorClient", "AsyncIOMotorCollection", "AsyncIOMotorDatabase"):
        setattr(_motor_asyncio, _cls_name, type(_cls_name, (), {}))
    _motor_mod.motor_asyncio = _motor_asyncio
    sys.modules.setdefault("motor", _motor_mod)
    sys.modules.setdefault("motor.motor_asyncio", _motor_asyncio)

from app.services.trading_core.signal import (
    SignalTranslationError,
    translate_decision_to_signal,
)


# ── 参考价兜底：转译层行为（无区间 → 抛错，由调用方兜底） ──────────────────


def test_buy_without_range_raises_translatable_error():
    with pytest.raises(SignalTranslationError) as exc_info:
        translate_decision_to_signal(
            user_id="u1",
            code="600519",
            decision={"action": "乐观", "confidence": 0.8},
            analysis_date="2026-10-28",
            cash_by_currency={"CNY": 1_000_000},
        )
    assert "参考价格" in str(exc_info.value)


def test_buy_with_fallback_range_translates():
    """调用方用收盘价 ±2% 兜底后应能正常转译。

    现金按 TRADING_MAX_POSITION_PCT 折算预算：1 亿现金 × 5% = 500 万
    参考价 1350 一手 13.5 万，远小于预算。
    """
    signal = translate_decision_to_signal(
        user_id="u1",
        code="600519",
        decision={
            "action": "乐观",
            "confidence": 0.8,
            "price_analysis_range": [1323.0, 1377.0],  # 收盘 1350 ± 2%
        },
        analysis_date="2026-10-28",
        cash_by_currency={"CNY": 100_000_000},
    )
    assert signal is not None
    assert signal.side == "buy"
    assert signal.reference_price == pytest.approx(1350.0)
    assert signal.quantity >= 100  # 至少一手


def test_never_still_produces_no_signal():
    assert (
        translate_decision_to_signal(
            user_id="u1",
            code="600519",
            decision={"action": "中性", "confidence": 0.5},
            analysis_date="2026-10-28",
            cash_by_currency={"CNY": 1_000_000},
        )
        is None
    )


# ── _close_asof：收盘价兜底查询 ────────────────────────────────────────────


class _FakeColl:
    """Motor find_one 是协程；同步 pymongo 语义用函数包装。"""

    def __init__(self, docs):
        self._docs = docs
        self.query = None

    def find_one(self, query, *a, **k):
        self.query = query

        async def _coro():
            return self._docs[0] if self._docs else None

        return _coro()


@pytest.mark.asyncio
async def test_close_asof_takes_latest_on_or_before():
    from app.services.task_analysis_service import TaskAnalysisService

    coll = _FakeColl([{"close": 1350.0}])
    db = {"stock_daily_quotes": coll}

    price = await TaskAnalysisService._close_asof(db, "600519", "2026-10-28")
    assert price == 1350.0
    assert coll.query["symbol"] == "600519"
    assert coll.query["trade_date"] == {"$lte": "2026-10-28"}


@pytest.mark.asyncio
async def test_close_asof_missing_returns_none():
    from app.services.task_analysis_service import TaskAnalysisService

    db = {"stock_daily_quotes": _FakeColl([])}
    assert await TaskAnalysisService._close_asof(db, "600519", "2026-10-28") is None


@pytest.mark.asyncio
async def test_close_asof_invalid_close_returns_none():
    from app.services.task_analysis_service import TaskAnalysisService

    db = {"stock_daily_quotes": _FakeColl([{"close": None}])}
    assert await TaskAnalysisService._close_asof(db, "600519", "2026-10-28") is None


# ── 风险经理 schema：乐观/审慎必须输出价格区间 ──────────────────────────────


def test_risk_schema_requires_range_for_directional_actions():
    from core.agents.adapters.risk_manager_v2 import RISK_ASSESSMENT_SCHEMA_PROMPT

    # "可选" 字样不应再出现在 price_analysis_range 的定义行
    import re

    line = next(
        (l for l in RISK_ASSESSMENT_SCHEMA_PROMPT.splitlines() if "price_analysis_range" in l),
        "",
    )
    assert "可选" not in line
    assert "必须给出" in line or "必须输出" in line


def test_risk_schema_keeps_neutral_escape_hatch():
    from core.agents.adapters.risk_manager_v2 import RISK_ASSESSMENT_SCHEMA_PROMPT

    # 中性结论仍允许 null（无方向性判断时不应强迫编造区间）
    assert "中性" in RISK_ASSESSMENT_SCHEMA_PROMPT
