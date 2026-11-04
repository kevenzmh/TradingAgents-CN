"""定时分析配置的校验 / 规整 / CRON 工具（Pro）

配置文档存放于 scheduled_analysis_configs，由 app/worker/watchlist_analysis_task.py 的
run_scheduled_analysis_slot(config_id, user_id, slot_index) 逐时间段执行。字段与
AI 助理工具 create_scheduled_analysis 写入的结构一致。

成本护栏：每次触发会为分组内每只股票创建一个 LLM 分析任务，所以限制
- CRON 最小间隔 30 分钟
- 每个配置最多 10 个时间段、每个用户最多 20 个配置
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger
from bson import ObjectId
from bson.errors import InvalidId

from app.utils.cron import standard_cron_trigger

COLLECTION = "scheduled_analysis_configs"
HISTORY_COLLECTION = "scheduled_analysis_history"
GROUP_COLLECTION = "watchlist_groups"

MAX_CONFIGS_PER_USER = 20
MAX_SLOTS = 10
MIN_INTERVAL_MINUTES = 30
MAX_NAME_LEN = 50
MAX_DESC_LEN = 200


class ScheduleError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def to_oid(value: Any, what: str = "配置ID") -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        raise ScheduleError(f"无效的{what}", 400)


# ==================== CRON ====================

def build_trigger(expr: Any, timezone: str) -> CronTrigger:
    """按**标准 cron 语义**构建触发器（分 时 日 月 周；周 0/7=周日，1=周一），见 app/utils/cron.py。"""
    if not isinstance(expr, str) or not expr.strip():
        raise ScheduleError("请填写 CRON 表达式")
    fields = expr.split()
    if len(fields) == 5 and fields[2] != "*" and fields[4] != "*":
        # 标准 cron 对“日”和“周”同时限定取并集，APScheduler 取交集，语义不同，直接拒绝避免误解
        raise ScheduleError("暂不支持同时限定“日”和“星期”，请只填其中一个")
    try:
        return standard_cron_trigger(expr, timezone)
    except ValueError as e:
        raise ScheduleError(str(e))


def next_fire_times(expr: str, timezone: str, count: int = 5) -> List[datetime]:
    trigger = build_trigger(expr, timezone)
    out: List[datetime] = []
    prev = None
    now = datetime.now(trigger.timezone)
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, prev or now)
        if nxt is None:
            break
        out.append(nxt)
        prev = nxt
    return out


def validate_cron(expr: Any, timezone: str) -> str:
    """校验 CRON 并检查最小触发间隔；返回规整后的表达式。"""
    expr = " ".join(str(expr or "").split())
    times = next_fire_times(expr, timezone, 6)
    if not times:
        raise ScheduleError("该 CRON 表达式不会再触发")
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    if gaps and min(gaps) < MIN_INTERVAL_MINUTES:
        raise ScheduleError(f"触发间隔过短：每次触发都会创建 LLM 分析任务，最小间隔为 {MIN_INTERVAL_MINUTES} 分钟")
    return expr


# ==================== 配置规整 ====================

def _depth(v: Any, default: Optional[int] = None) -> Optional[int]:
    if v in (None, ""):
        return default
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
        raise ScheduleError("分析深度必须是 1-5 的整数")
    return v


async def _check_groups(db: Any, user_id: str, group_ids: List[str]) -> List[str]:
    cleaned: List[str] = []
    for gid in group_ids or []:
        to_oid(gid, "分组ID")
        if gid not in cleaned:
            cleaned.append(gid)
    if cleaned:
        owned = {str(d["_id"]) async for d in db[GROUP_COLLECTION].find(
            {"user_id": user_id, "_id": {"$in": [ObjectId(g) for g in cleaned]}}, {"_id": 1})}
        missing = [g for g in cleaned if g not in owned]
        if missing:
            raise ScheduleError("引用了不存在的分组，请刷新后重试")
    return cleaned


async def normalize_config(db: Any, user_id: str, data: Dict[str, Any], timezone: str,
                           existing_slots: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """校验并规整创建/更新入参，返回可直接 $set 的字段（不含 user_id/created_at）。

    existing_slots：更新时传入旧时间段。通用研究流程（workflow_id / workflow_params）没有页面入口，
    同名时间段更新时原样保留，避免编辑配置时把它们悄悄丢掉。
    """
    old_by_name = {s.get("name"): s for s in (existing_slots or [])}
    name = (data.get("name") or "").strip()
    if not name:
        raise ScheduleError("配置名称不能为空")
    if len(name) > MAX_NAME_LEN:
        raise ScheduleError(f"配置名称不能超过 {MAX_NAME_LEN} 字符")
    description = (data.get("description") or "").strip()
    if len(description) > MAX_DESC_LEN:
        raise ScheduleError(f"描述不能超过 {MAX_DESC_LEN} 字符")

    raw_slots = data.get("time_slots") or []
    if not raw_slots:
        raise ScheduleError("至少需要一个时间段")
    if len(raw_slots) > MAX_SLOTS:
        raise ScheduleError(f"最多 {MAX_SLOTS} 个时间段")

    default_groups = await _check_groups(db, user_id, data.get("default_group_ids") or [])
    default_depth = _depth(data.get("default_analysis_depth"), 3)

    slots: List[Dict[str, Any]] = []
    seen_names = set()
    for i, s in enumerate(raw_slots, 1):
        sname = (s.get("name") or "").strip()
        if not sname:
            raise ScheduleError(f"第 {i} 个时间段缺少名称")
        if sname in seen_names:
            raise ScheduleError(f"时间段名称重复：{sname}")
        seen_names.add(sname)
        try:
            cron = validate_cron(s.get("cron_expression"), timezone)
        except ScheduleError as e:
            raise ScheduleError(f"时间段「{sname}」：{e.message}")
        groups = await _check_groups(db, user_id, s.get("group_ids") or [])
        slot: Dict[str, Any] = {
            "name": sname[:30], "cron_expression": cron, "enabled": bool(s.get("enabled", True)),
            "group_ids": groups, "analysis_depth": _depth(s.get("analysis_depth")),
        }
        old = old_by_name.get(slot["name"]) or {}
        if old.get("workflow_id"):
            slot["workflow_id"] = old["workflow_id"]
            slot["workflow_params"] = old.get("workflow_params") or {}
        elif not groups and not default_groups:
            raise ScheduleError(f"时间段「{sname}」没有选择分组（也没有默认分组）")
        slots.append(slot)

    return {
        "name": name, "description": description, "enabled": bool(data.get("enabled", True)),
        "time_slots": slots, "default_group_ids": default_groups, "default_analysis_depth": default_depth,
        "notify_on_complete": bool(data.get("notify_on_complete", True)),
        "notify_on_error": bool(data.get("notify_on_error", True)),
        "send_email": bool(data.get("send_email", False)),
    }


def serialize(doc: Dict[str, Any], next_runs: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    def iso(v):
        return v.isoformat() if isinstance(v, datetime) else v

    slots = []
    for i, s in enumerate(doc.get("time_slots") or []):
        slots.append({
            "name": s.get("name", ""), "cron_expression": s.get("cron_expression", ""),
            "enabled": bool(s.get("enabled", True)), "group_ids": list(s.get("group_ids") or []),
            "analysis_depth": s.get("analysis_depth"), "has_workflow": bool(s.get("workflow_id")),
            "next_run_at": (next_runs or {}).get(i),
        })
    return {
        "id": str(doc["_id"]), "name": doc.get("name", ""), "description": doc.get("description", ""),
        "enabled": bool(doc.get("enabled")), "time_slots": slots,
        "default_group_ids": list(doc.get("default_group_ids") or []),
        "default_analysis_depth": doc.get("default_analysis_depth", 3),
        "notify_on_complete": bool(doc.get("notify_on_complete", True)),
        "notify_on_error": bool(doc.get("notify_on_error", True)),
        "send_email": bool(doc.get("send_email", False)),
        "last_run_at": iso(doc.get("last_run_at")), "created_at": iso(doc.get("created_at")),
        "updated_at": iso(doc.get("updated_at")),
    }
