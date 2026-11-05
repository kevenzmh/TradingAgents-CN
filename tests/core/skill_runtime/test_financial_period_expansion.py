"""财务快照展开为多期记录的行为约束。

回归背景：stock_financial_periods 曾出现全部字段为 null，且每个 symbol 只有
一行（本应展开出多期）。两个独立缺陷：
1. _flatten_financial_period_record 在分项说明为空时用 None 覆盖了顶层已有值；
2. akshare 的 raw_data.main_indicators 是「指标 × 报告期」矩阵，展开逻辑不认识，
   因此只能产出最新一期，且丢失矩阵里的历史期。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.skill_runtime.data_access import (  # noqa: E402
    _flatten_financial_period_record,
    expand_financial_document_to_periods,
)


def test_flatten_keeps_existing_top_level_value_when_section_is_empty():
    record = {
        "symbol": "600519",
        "report_period": "20260930",
        "revenue": 133283652508.95,
        "net_income": 64467425981.95,
        "raw_data": {
            "income_statement": {},
            "balance_sheet": {},
            "cashflow_statement": {},
            "financial_indicators": {},
        },
    }

    flattened = _flatten_financial_period_record(record)

    assert flattened["revenue"] == 133283652508.95
    assert flattened["net_income"] == 64467425981.95


def test_flatten_prefers_section_value_over_existing():
    record = {
        "symbol": "600519",
        "report_period": "20260930",
        "revenue": 1.0,
        "income_statement": {"revenue": 2.0},
        "balance_sheet": {},
        "cashflow_statement": {},
        "financial_indicators": {},
    }

    flattened = _flatten_financial_period_record(record)

    assert flattened["revenue"] == 2.0


def _akshare_matrix_document():
    """akshare stock_financial_abstract 的落库形态：指标名 × 报告期矩阵。"""
    return {
        "symbol": "600519",
        "code": "600519",
        "name": "贵州茅台",
        "data_source": "akshare",
        "report_period": "20260930",
        "report_type": "quarterly",
        "updated_at": "2026-11-03T00:00:00Z",
        "revenue": 133283652508.95,
        "net_income": 64467425981.95,
        "raw_data": {
            "main_indicators": [
                {"选项": "常用指标", "指标": "归母净利润", "20260930": 64467425981.95, "20251231": 82320067101.68},
                {"选项": "常用指标", "指标": "营业总收入", "20260930": 133283652508.95, "20251231": 172054172000.0},
                {"选项": "常用指标", "指标": "营业成本", "20260930": 44486918820.21, "20251231": 60000000000.0},
                {"选项": "常用指标", "指标": "净利润", "20260930": 66717143218.04, "20251231": 85000000000.0},
                {"选项": "常用指标", "指标": "股东权益合计(净资产)", "20260930": 250000000000.0, "20251231": 240000000000.0},
                {"选项": "常用指标", "指标": "经营现金流量净额", "20260930": 50000000000.0, "20251231": 70000000000.0},
                {"选项": "盈利能力", "指标": "净资产收益率(ROE)", "20260930": 12.5, "20251231": 30.1},
                {"选项": "盈利能力", "指标": "总资产报酬率(ROA)", "20260930": 9.8, "20251231": 22.4},
                {"选项": "盈利能力", "指标": "毛利率", "20260930": 66.6, "20251231": 65.1},
                {"选项": "盈利能力", "指标": "销售净利率", "20260930": 50.1, "20251231": 49.4},
                {"选项": "财务风险", "指标": "资产负债率", "20260930": 18.2, "20251231": 19.0},
                {"选项": "财务风险", "指标": "流动比率", "20260930": 4.1, "20251231": 4.0},
            ]
        },
    }


def test_expand_akshare_matrix_into_each_report_period():
    records = expand_financial_document_to_periods(_akshare_matrix_document())

    periods = [item["report_period"] for item in records]
    assert periods == ["20260930", "20251231"]

    latest = next(item for item in records if item["report_period"] == "20260930")
    annual = next(item for item in records if item["report_period"] == "20251231")

    assert latest["revenue"] == 133283652508.95
    assert latest["net_profit"] == 64467425981.95
    assert latest["net_income"] == 66717143218.04
    assert latest["oper_cost"] == 44486918820.21
    assert latest["total_equity"] == 250000000000.0
    assert latest["n_cashflow_act"] == 50000000000.0
    assert latest["roe"] == 12.5
    assert latest["gross_margin"] == 66.6
    assert latest["netprofit_margin"] == 50.1
    assert latest["debt_to_assets"] == 18.2
    assert latest["current_ratio"] == 4.1
    assert latest["report_type"] == "quarterly"

    assert annual["revenue"] == 172054172000.0
    assert annual["roe"] == 30.1
    assert annual["report_type"] == "annual"
    assert annual["symbol"] == "600519"
    assert annual["name"] == "贵州茅台"


def test_expand_akshare_matrix_is_idempotent_across_calls():
    document = _akshare_matrix_document()

    first = expand_financial_document_to_periods(document)
    second = expand_financial_document_to_periods(document)

    assert [item["report_period"] for item in first] == [item["report_period"] for item in second]
    assert first[0]["revenue"] == second[0]["revenue"]


def _akshare_statement_document():
    """akshare 三大报表的落库形态：REPORT_DATE + 大写英文列名。"""
    return {
        "symbol": "600519",
        "code": "600519",
        "data_source": "akshare",
        "report_period": "20260930",
        "raw_data": {
            "balance_sheet": [
                {
                    "REPORT_DATE": "2026-09-30 00:00:00",
                    "NOTICE_DATE": "2026-10-29 00:00:00",
                    "TOTAL_ASSETS": 300000000000.0,
                    "TOTAL_LIABILITIES": 35700000000.0,
                    "TOTAL_PARENT_EQUITY": 250000000000.0,
                    "MONETARYFUNDS": 60000000000.0,
                    "INVENTORY": 50000000000.0,
                }
            ],
            "income_statement": [
                {
                    "REPORT_DATE": "2026-09-30 00:00:00",
                    "NOTICE_DATE": "2026-10-29 00:00:00",
                    "TOTAL_OPERATE_INCOME": 133283652508.95,
                    "OPERATE_COST": 44486918820.21,
                    "PARENT_NETPROFIT": 64467425981.95,
                    "NETPROFIT": 66717143218.04,
                }
            ],
            "cashflow_statement": [
                {
                    "REPORT_DATE": "2026-09-30 00:00:00",
                    "NETCASH_OPERATE": 50000000000.0,
                    "END_CCE": 90000000000.0,
                }
            ],
        },
    }


def test_expand_akshare_statements_maps_uppercase_columns():
    records = expand_financial_document_to_periods(_akshare_statement_document())

    assert len(records) == 1
    record = records[0]
    assert record["report_period"] == "20260930"
    assert record["ann_date"] == "20261029"
    assert record["revenue"] == 133283652508.95
    assert record["oper_cost"] == 44486918820.21
    assert record["net_profit"] == 64467425981.95
    assert record["net_income"] == 66717143218.04
    assert record["total_assets"] == 300000000000.0
    assert record["total_liab"] == 35700000000.0
    assert record["total_equity"] == 250000000000.0
    assert record["money_cap"] == 60000000000.0
    assert record["inventories"] == 50000000000.0
    assert record["n_cashflow_act"] == 50000000000.0
    assert record["c_cash_equ_end_period"] == 90000000000.0


def test_expand_keeps_statement_period_separate_from_document_period():
    document = _akshare_statement_document()
    document["report_period"] = "20260630"

    records = expand_financial_document_to_periods(document)

    assert "20260930" in [item["report_period"] for item in records]


def test_expand_accepts_cash_flow_key_from_akshare_provider():
    document = _akshare_statement_document()
    document["raw_data"]["cash_flow"] = document["raw_data"].pop("cashflow_statement")

    record = expand_financial_document_to_periods(document)[0]

    assert record["n_cashflow_act"] == 50000000000.0
    assert record["c_cash_equ_end_period"] == 90000000000.0
