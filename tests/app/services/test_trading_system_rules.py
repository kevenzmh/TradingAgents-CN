"""交易计划规则渲染单测：对接真实模板（router 里的 TRADING_SYSTEM_TEMPLATES）与旧版顶层/字符串形态。"""

import pytest

from app.pro.routers.trading_systems import TRADING_SYSTEM_TEMPLATES
from app.services import trading_system_rules as r


def _doc(template):
    """模拟 from-template 写入的文档：规则在 data 下。"""
    return {"name": template["name"], "style": template["style"], "data": template["data"]}


@pytest.mark.parametrize("template", TRADING_SYSTEM_TEMPLATES, ids=lambda t: t["id"])
def test_every_builtin_template_renders(template):
    text = r.build_rules_section(_doc(template))
    assert "**选股规则**" in text and "**纪律规则**" in text and "**风险管理规则**" in text
    assert "{" not in text and "dict" not in text  # 没有把字典直接 str() 进去


def test_short_term_template_details():
    text = r.build_rules_section(_doc(TRADING_SYSTEM_TEMPLATES[0]))
    assert "市值 > 50亿（避免小盘股流动性风险）" in text      # rule + description
    assert "MACD金叉（动能转强）" in text                      # signal 字段
    assert "单只股票上限: 15%" in text                         # 0.15 → 15%
    assert "最大持股数: 8只（最少 3 只）" in text
    assert "止损: percentage 7%（固定7%止损）" in text
    assert "加仓条件: 股价高于成本价5%以上（盈利加仓）" in text  # condition 字段


def test_legacy_top_level_string_items():
    system = {
        "name": "旧版",
        "stock_selection": {"must_meet": ["ROE>15%"], "exclude": ["ST"]},
        "timing": {"entry_signals": ["突破"], "exit_signals": ["跌破10日线"]},
        "position": {"single_stock_limit": 20, "max_stocks": 5},
        "risk_management": {"stop_loss": {"type": "fixed", "value": "8%"}, "take_profit": {"type": "fixed", "value": "20%"}},
        "discipline": {"must_do": ["写日志"], "forbidden": ["追涨"]},
    }
    text = r.build_rules_section(system)
    for expected in ("必须满足: ROE>15%", "排除条件: ST", "出场信号: 跌破10日线", "单只股票上限: 20%",
                     "最大持股数: 5只", "止损: fixed 8%", "止盈: fixed 20%", "严禁操作: 追涨"):
        assert expected in text


def test_data_takes_precedence_over_top_level():
    system = {"stock_selection": {"must_have": ["顶层"]}, "data": {"stock_selection": {"must_have": ["data内"]}}}
    text = r.build_rules_section(system)
    assert "data内" in text and "顶层" not in text


@pytest.mark.parametrize("doc", [{}, {"name": "空"}, {"data": {}}, {"data": {"timing": {}, "position": {}}}])
def test_empty_returns_blank(doc):
    assert r.build_rules_section(doc) == ""


def test_item_text_variants():
    assert r.item_text("  纯文本 ") == "纯文本"
    assert r.item_text({"rule": "R", "description": "D"}) == "R（D）"
    assert r.item_text({"signal": "S"}) == "S"
    assert r.item_text({"description": "只有说明"}) == "只有说明"
    assert r.item_text({}) == "" and r.item_text(None) == ""


def test_pct_and_garbage_tolerance():
    assert r._pct(0.15) == "15%" and r._pct(1) == "100%" and r._pct(20) == "20%" and r._pct(True) is None
    # 异常类型的字段不应抛错
    r.build_rules_section({"data": {"stock_selection": {"must_have": "不是列表"}, "position": {"max_per_stock": "abc"},
                                    "risk_management": {"stop_loss": "字符串"}, "timing": {"entry_signals": [None, 3]}}})
