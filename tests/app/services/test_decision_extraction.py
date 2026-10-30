"""决策倾向提取的回归测试（关键词误判修复）。

历史 bug：`_extract_decision_from_text` 先匹配"乐观"再匹配"审慎"，导致
报告正文里出现"若数据修正则可能上修至乐观"这类假设表述时，明确的
"审慎"结论被误判为"乐观"——分析结论直接进交易信号链路，这个误判会
让审慎结论生成买入信号。
"""

import sys
import types

import pytest

try:  # 本机可能未装 motor；app.core.database 顶层 import 需要它
    import motor  # noqa: F401
except ModuleNotFoundError:
    _motor_mod = types.ModuleType("motor")
    _motor_asyncio = types.ModuleType("motor.motor_asyncio")
    for _cls_name in ("AsyncIOMotorClient", "AsyncIOMotorCollection", "AsyncIOMotorDatabase"):
        setattr(_motor_asyncio, _cls_name, type(_cls_name, (), {}))
    _motor_mod.motor_asyncio = _motor_asyncio
    sys.modules.setdefault("motor", _motor_mod)
    sys.modules.setdefault("motor.motor_asyncio", _motor_asyncio)

from app.services.task_analysis_service import TaskAnalysisService


class _StubLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Self:
    """_extract_decision_from_text 是实例方法（内部只用 self.logger）。"""

    logger = _StubLogger()


@pytest.fixture()
def extract():
    fn = TaskAnalysisService._extract_decision_from_text

    def _call(text):
        return fn(_Self(), text)

    return _call


@pytest.mark.parametrize(
    "name,text,expected",
    [
        (
            "结构化JSON优先",
            '## 研究结论\n{"analysis_view": "审慎", "confidence": 0.1, '
            '"reasoning": "多方曾提及乐观情景但数据无效", '
            '"invalidation_conditions": ["若数据修正则可能上修至乐观"]}',
            "审慎",
        ),
        (
            "Markdown观点块",
            "## 💡 研究观点\n\n**观点**: 🟡 审慎\n\n**信心度**: 10/100",
            "审慎",
        ),
        ("显式结论句", "当前研究结论为审慎，因为分析日期是未来日期", "审慎"),
        ("纯文本看涨", "整体看涨，建议关注", "乐观"),
        ("观望措辞", "建议观望，等待更好时机", "中性"),
        ("代码块JSON", '```json\n{"analysis_view": "乐观", "confidence": 0.8}\n```', "乐观"),
        ("无任何信号", "数据不足，无法判断。", "中性"),
        (
            "回归：正文后段含乐观假设",
            "综合判断为审慎；若后续数据修正，则可能转为乐观。",
            "审慎",
        ),
        ("乐观在前提及", "技术面乐观，但数据无效，总体审慎。", "乐观"),
    ],
)
def test_action_extraction_priority(extract, name, text, expected):
    assert extract(text).get("action") == expected, name


def test_reasoning_bug_repro_never_labelled_optimistic(extract):
    # 本次真实产出（2026-10-30 600519 分析）的文本形态
    text = (
        "## 📋 研究结论\n"
        '{"analysis_view": "审慎", "confidence": 0.1, "risk_score": 0.9, '
        '"summary": "当前研究结论偏向审慎", '
        '"invalidation_conditions": ["分析日期修正后可能上修至乐观"]}'
    )
    decision = extract(text)
    assert decision["action"] == "审慎"
    assert decision["action"] != "乐观"