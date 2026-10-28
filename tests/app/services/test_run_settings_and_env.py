"""#2/#3/#5 剩余清单的单元测试。

- 温度环境覆盖 `_env_temperature_override`：未设置/0.0/非法/超范围/合法覆盖各分支
- 报告 `run_settings` 快照 `_build_run_settings`：字段来源、None 过滤、VERSION 读取
- 快照扣留接线 `get_stock_fundamentals_unified`：历史分析日扣留实时估值通道、
  当日/无上下文分析不扣留、模型传"今天"被钳制回分析日
"""

import os
import sys
import types
from types import SimpleNamespace

import pytest

try:
    import motor  # noqa: F401
except ModuleNotFoundError:
    # 本机未装 motor 时打桩，让 app.core.database 的顶层 import 可通过
    _motor_mod = types.ModuleType("motor")
    _motor_asyncio = types.ModuleType("motor.motor_asyncio")
    for _cls_name in ("AsyncIOMotorClient", "AsyncIOMotorCollection", "AsyncIOMotorDatabase"):
        setattr(_motor_asyncio, _cls_name, type(_cls_name, (), {}))
    _motor_mod.motor_asyncio = _motor_asyncio
    sys.modules.setdefault("motor", _motor_mod)
    sys.modules.setdefault("motor.motor_asyncio", _motor_asyncio)

from core.workflow.builder import _env_temperature_override


# ── _env_temperature_override ────────────────────────────────────────────


def test_env_temperature_unset_keeps_configured():
    assert _env_temperature_override(0.3) == 0.3


def test_env_temperature_zero_applied(monkeypatch):
    # 0.0 是合法覆盖值，不能被 or-链吞掉
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "0")
    assert _env_temperature_override(0.3) == 0.0


def test_env_temperature_valid_value(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "0.7")
    assert _env_temperature_override(0.1) == 0.7


def test_env_temperature_blank_ignored(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "   ")
    assert _env_temperature_override(0.2) == 0.2


def test_env_temperature_non_numeric_ignored(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "abc")
    assert _env_temperature_override(0.2) == 0.2


def test_env_temperature_below_range_ignored(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "-0.1")
    assert _env_temperature_override(0.2) == 0.2


def test_env_temperature_above_range_ignored(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "2.5")
    assert _env_temperature_override(0.2) == 0.2


def test_env_temperature_upper_boundary_accepted(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "2")
    assert _env_temperature_override(0.2) == 2.0


# ── _build_run_settings ───────────────────────────────────────────────────


def _build_task(**overrides):
    base = dict(
        task_params={"quick_analysis_model": "qwen-turbo", "max_debate_rounds": 2},
        preference_type="aggressive",
        engine_type="v2",
        workflow_id="wf_test",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _service_class():
    from app.services.task_analysis_service import TaskAnalysisService

    return TaskAnalysisService


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_TEMPERATURE", raising=False)
    monkeypatch.delenv("AGENT_MAX_TOOL_CALLS", raising=False)


def test_build_run_settings_captures_core_fields(clean_env):
    result = {
        "model_info": "test-model",
        "research_depth": "deep",
        "analysts": ["market", "fundamentals"],
    }
    settings_snapshot = _service_class()._build_run_settings(_build_task(), result)

    assert settings_snapshot["quick_model"] == "qwen-turbo"
    assert settings_snapshot["model_info"] == "test-model"
    assert settings_snapshot["research_depth"] == "deep"
    assert settings_snapshot["analysts"] == ["market", "fundamentals"]
    assert settings_snapshot["max_debate_rounds"] == 2
    assert settings_snapshot["preference_type"] == "aggressive"
    assert settings_snapshot["engine_type"] == "v2"
    assert settings_snapshot["workflow_id"] == "wf_test"


def test_build_run_settings_filters_none_fields(clean_env):
    # 缺省字段不猜值：params/result 都没有的键必须整体缺项，而不是 None 占位
    task = _build_task(task_params={})
    settings_snapshot = _service_class()._build_run_settings(task, {})

    assert "quick_model" not in settings_snapshot
    assert "deep_model" not in settings_snapshot
    assert "max_debate_rounds" not in settings_snapshot
    # analysts 无值时回退空列表，保留键
    assert settings_snapshot.get("analysts") == []


def test_build_run_settings_records_env_overrides(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "0.5")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "30")
    settings_snapshot = _service_class()._build_run_settings(_build_task(), {})

    assert settings_snapshot["temperature_env_override"] == "0.5"
    assert settings_snapshot["analyst_max_tool_calls"] == "30"


def test_build_run_settings_reads_version_file(clean_env):
    # 实现从 app/services 上溯两级目录找 VERSION，测试用同一规则
    version_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "VERSION"
    )
    version_file = os.path.normpath(version_file)
    if not os.path.exists(version_file):
        pytest.skip("VERSION 文件缺失")

    with open(version_file, "r", encoding="utf-8") as f:
        expected = f.read().strip()[:40]
    if not expected:
        pytest.skip("VERSION 文件为空")

    settings_snapshot = _service_class()._build_run_settings(_build_task(), {})
    assert settings_snapshot["app_version"] == expected


# ── save_analysis_report run_settings 参数 ────────────────────────────────


@pytest.mark.asyncio
async def test_save_report_persists_run_settings(clean_env):
    from app.utils.report_saver import save_analysis_report

    inserted = {}

    class _FakeCollection:
        async def insert_one(self, doc):
            inserted["doc"] = doc
            return SimpleNamespace(inserted_id="oid")

    class _FakeDB:
        analysis_reports = _FakeCollection()

    analysis_id = await save_analysis_report(
        _FakeDB(),
        stock_symbol="600519",
        stock_name="贵州茅台",
        market_type="A股",
        model_info="m",
        reports={},
        decision={},
        recommendation="乐观",
        confidence_score=0.8,
        risk_level="中",
        run_settings={"quick_model": "qwen-turbo"},
    )

    assert analysis_id.startswith("600519_")
    assert inserted["doc"]["run_settings"] == {"quick_model": "qwen-turbo"}


@pytest.mark.asyncio
async def test_save_report_run_settings_defaults_to_empty_dict():
    from app.utils.report_saver import save_analysis_report

    inserted = {}

    class _FakeCollection:
        async def insert_one(self, doc):
            inserted["doc"] = doc
            return SimpleNamespace(inserted_id="oid")

    class _FakeDB:
        analysis_reports = _FakeCollection()

    # 旧调用方（simple_analysis_service 等）不带 run_settings——向后兼容
    await save_analysis_report(
        _FakeDB(),
        stock_symbol="600519",
        stock_name="贵州茅台",
        market_type="A股",
        model_info="m",
        reports={},
        decision={},
        recommendation="乐观",
        confidence_score=0.8,
        risk_level="中",
    )

    assert inserted["doc"]["run_settings"] == {}


# ── 快照扣留接线（get_stock_fundamentals_unified A 股分支）─────────────────


def _invoke_fundamentals(ticker="600519", curr_date="2026-01-10"):
    from core.tools.implementations.fundamentals.stock_fundamentals import (
        get_stock_fundamentals_unified as tool_func,
    )

    return tool_func.func(ticker=ticker, start_date="2026-01-10", end_date="2026-01-10", curr_date=curr_date)


def test_fundamentals_withholds_realtime_channel_for_historical_date():
    from core.tools.context import set_current_analysis_date, reset_current_analysis_date
    from tradingagents.dataflows.date_window import get_current_date

    today = get_current_date()
    past = "2024-06-03"
    assert past < today

    token = set_current_analysis_date(past)
    try:
        output = _invoke_fundamentals()
    finally:
        reset_current_analysis_date(token)

    assert "实时快照数据对该日期不提供" in output
    assert "Point-in-time 截至: 2024-06-03" in output
    # 公司静态信息与历史估值分位照常提供
    assert "A股公司基础信息" in output
    assert "A股历史估值分位" in output


def test_fundamentals_keeps_realtime_channel_for_today():
    from core.tools.context import set_current_analysis_date, reset_current_analysis_date
    from tradingagents.dataflows.date_window import get_current_date

    today = get_current_date()
    token = set_current_analysis_date(today)
    try:
        output = _invoke_fundamentals(curr_date=today)
    finally:
        reset_current_analysis_date(token)

    assert "实时快照数据对该日期不提供" not in output


def test_fundamentals_no_context_date_keeps_old_behavior():
    # graph 外直接调用（contextvar 未设置）：行为只由 curr_date 决定。
    # 传"今天"时不得扣留（实时通道本就合法）；传历史日期时工具自身已是
    # point-in-time 安全的（历史回放必须扣留），这是接线后的预期行为。
    from tradingagents.dataflows.date_window import get_current_date

    output_today = _invoke_fundamentals(curr_date=get_current_date())
    assert "实时快照数据对该日期不提供" not in output_today


def test_fundamentals_clamps_model_today_back_to_analysis_date():
    # 模型把 curr_date 传成"今天"想绕过钳制——必须被拉回历史分析日并扣留
    from core.tools.context import set_current_analysis_date, reset_current_analysis_date
    from tradingagents.dataflows.date_window import get_current_date

    past = "2024-06-03"
    assert past < get_current_date()

    token = set_current_analysis_date(past)
    try:
        output = _invoke_fundamentals(curr_date=get_current_date())
    finally:
        reset_current_analysis_date(token)

    assert "实时快照数据对该日期不提供" in output
    assert f"Point-in-time 截至: {past}" in output
    assert f"**分析日期**: {past}" in output
