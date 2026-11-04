"""交易计划规则 → 复盘提示词文本

交易计划文档（trading_systems 集合）的规则位于 `data` 下，条目为 `{rule, description}` 字典
（`{signal}`、`{condition}` 同理），仓位用 `max_per_stock`（0-1 比例）/ `max_holdings`，
止损止盈用 `type` + `percentage` + `description`。早期文档或助理工具可能把规则放在文档顶层、
条目是纯字符串、字段叫 `single_stock_limit` / `max_stocks` / `value`，这里两种形态都兼容。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_ITEM_KEYS = ("rule", "signal", "condition", "text", "name")


def rules_root(system: Dict[str, Any]) -> Dict[str, Any]:
    """规则容器：优先 `data`，其次文档顶层。"""
    data = system.get("data")
    return data if isinstance(data, dict) and data else system


def item_text(item: Any) -> str:
    """把规则条目规整成一行文字：字符串原样；字典取 rule/signal/condition，并附 description。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        main = next((str(item[k]).strip() for k in _ITEM_KEYS if item.get(k)), "")
        desc = str(item.get("description") or "").strip()
        if main and desc:
            return f"{main}（{desc}）"
        return main or desc
    return str(item).strip() if item is not None else ""


def _texts(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [t for t in (item_text(i) for i in items) if t]


def _pct(value: Any) -> Optional[str]:
    """0-1 的比例按百分比展示；>1 视为已是百分数。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    pct = value * 100 if 0 < value <= 1 else value
    return f"{pct:g}%"


def _first(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _exit_rule(rule: Any) -> str:
    """止损/止盈：type + 数值 + 说明"""
    if not isinstance(rule, dict) or not rule:
        return ""
    parts = [str(rule.get("type") or "").strip()]
    value = _first(rule, "percentage", "value")
    shown = _pct(value) if rule.get("percentage") is not None else (str(value) if value is not None else None)
    if shown:
        parts.append(shown)
    desc = str(rule.get("description") or "").strip()
    text = " ".join(p for p in parts if p)
    return f"{text}（{desc}）" if text and desc else (text or desc)


def build_rules_section(system: Dict[str, Any]) -> str:
    """生成『交易计划规则』Markdown 片段（不含标题）；没有任何规则返回空串。"""
    root = rules_root(system)
    out: List[str] = []

    sel = root.get("stock_selection") or {}
    must = _texts(sel.get("must_have") or sel.get("must_meet"))
    excl = _texts(sel.get("exclude"))
    if must or excl:
        out.append("**选股规则**:")
        if must:
            out.append("- 必须满足: " + "; ".join(must))
        if excl:
            out.append("- 排除条件: " + "; ".join(excl))
        out.append("")

    timing = root.get("timing") or {}
    entry, exit_ = _texts(timing.get("entry_signals")), _texts(timing.get("exit_signals"))
    if entry or exit_:
        out.append("**择时规则**:")
        if entry:
            out.append("- 入场信号: " + "; ".join(entry))
        if exit_:
            out.append("- 出场信号: " + "; ".join(exit_))
        out.append("")

    pos = root.get("position") or {}
    limit = _pct(_first(pos, "max_per_stock", "single_stock_limit"))
    max_n = _first(pos, "max_holdings", "max_stocks")
    min_n = pos.get("min_holdings")
    if limit or max_n is not None:
        out.append("**仓位规则**:")
        if limit:
            out.append(f"- 单只股票上限: {limit}")
        if max_n is not None:
            out.append(f"- 最大持股数: {max_n}只" + (f"（最少 {min_n} 只）" if min_n else ""))
        out.append("")

    holding = root.get("holding") or {}
    add, reduce_ = _texts(holding.get("add_conditions")), _texts(holding.get("reduce_conditions"))
    if add or reduce_:
        out.append("**持仓管理**:")
        if add:
            out.append("- 加仓条件: " + "; ".join(add))
        if reduce_:
            out.append("- 减仓条件: " + "; ".join(reduce_))
        out.append("")

    risk = root.get("risk_management") or {}
    stop, take = _exit_rule(risk.get("stop_loss")), _exit_rule(risk.get("take_profit"))
    if stop or take:
        out.append("**风险管理规则**:")
        if stop:
            out.append(f"- 止损: {stop}")
        if take:
            out.append(f"- 止盈: {take}")
        out.append("")

    disc = root.get("discipline") or {}
    must_do, forbidden = _texts(disc.get("must_do")), _texts(disc.get("forbidden"))
    if must_do or forbidden:
        out.append("**纪律规则**:")
        if must_do:
            out.append("- 必须做到: " + "; ".join(must_do))
        if forbidden:
            out.append("- 严禁操作: " + "; ".join(forbidden))
        out.append("")

    return "\n".join(out).rstrip() + "\n" if out else ""
