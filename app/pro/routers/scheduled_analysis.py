"""定时分析：调度注册 + 配置管理路由（Pro）

调度模型：每个配置的每个启用时间段对应一个 APScheduler 任务
    job_id = scheduled_analysis:{config_id}:{slot_index}
到点调用 app.worker.watchlist_analysis_task.run_scheduled_analysis_slot(config_id, user_id, slot_index)。
应用启动时由 load_all_enabled_configs() 重新挂载所有已启用配置（APScheduler 使用内存存储，重启即丢）。

兼容：AI 助理工具（core/tools/.../schedule_manager.py）仍从本模块导入
_register_scheduled_tasks / _unregister_scheduled_tasks。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.database import get_mongo_db
from app.core.permissions import require_pro
from app.pro.services import scheduled_analysis_service as svc
from app.routers.auth_db import get_current_user
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduled-analysis", tags=["scheduled-analysis"])

_JOB_PREFIX = "scheduled_analysis"
_running: Set[str] = set()
_last_run_now: Dict[str, float] = {}
RUN_NOW_COOLDOWN = 30


def _job_id(config_id: str, slot_index: Any) -> str:
    return f"{_JOB_PREFIX}:{config_id}:{slot_index}"


def _scheduler():
    from app.services.scheduler_service import get_scheduler_service
    return get_scheduler_service().scheduler


# ==================== 调度注册 ====================

async def unregister_config(config_id: str) -> int:
    """移除某配置的全部调度任务，返回移除数量。"""
    try:
        scheduler = _scheduler()
    except Exception as e:  # 调度器未初始化（如单测）
        logger.debug(f"[定时分析] 调度器不可用: {e}")
        return 0
    prefix = f"{_JOB_PREFIX}:{config_id}:"
    removed = 0
    for job in list(scheduler.get_jobs()):
        if str(job.id).startswith(prefix):
            scheduler.remove_job(job.id)
            removed += 1
    return removed


async def register_config(config: Dict[str, Any]) -> List[str]:
    """（重新）挂载配置：先清掉旧任务，再为每个启用的时间段注册。返回已注册的 job_id。"""
    from app.worker.watchlist_analysis_task import run_scheduled_analysis_slot

    config_id = str(config.get("id") or config.get("_id") or "")
    if not config_id:
        logger.warning("[定时分析] 配置缺少 id，跳过注册")
        return []
    await unregister_config(config_id)
    if not config.get("enabled"):
        return []

    scheduler = _scheduler()
    user_id = str(config.get("user_id") or "")
    registered: List[str] = []
    for index, slot in enumerate(config.get("time_slots") or []):
        if not slot.get("enabled", True):
            continue
        try:
            trigger = svc.build_trigger(slot.get("cron_expression"), settings.TIMEZONE)
        except svc.ScheduleError as e:
            logger.warning(f"[定时分析] 配置「{config.get('name')}」时间段 {index} CRON 无效，已跳过: {e.message}")
            continue
        job_id = _job_id(config_id, index)
        scheduler.add_job(
            run_scheduled_analysis_slot, trigger, args=[config_id, user_id, index], id=job_id,
            name=f"定时分析·{config.get('name', config_id)}·{slot.get('name', index)}", replace_existing=True,
        )
        registered.append(job_id)
    logger.info(f"[定时分析] 配置「{config.get('name')}」已挂载 {len(registered)} 个时间段")
    return registered


async def load_all_enabled_configs() -> int:
    """应用启动时调用：挂载所有已启用的配置。"""
    db = get_mongo_db()
    total = 0
    async for doc in db[svc.COLLECTION].find({"enabled": True}):
        try:
            doc["id"] = str(doc["_id"])
            total += len(await register_config(doc))
        except Exception as e:
            logger.error(f"[定时分析] 挂载配置失败 {doc.get('_id')}: {e}")
    logger.info(f"[定时分析] 启动加载完成，共挂载 {total} 个定时任务")
    return total


# 兼容旧名称（schedule_manager 工具使用）
async def _register_scheduled_tasks(config: Dict[str, Any]) -> None:
    await register_config(config)


async def _unregister_scheduled_tasks(config_id: str) -> None:
    await unregister_config(str(config_id))


def _next_runs(config_id: str, count: int) -> Dict[int, str]:
    out: Dict[int, str] = {}
    try:
        scheduler = _scheduler()
        for i in range(count):
            job = scheduler.get_job(_job_id(config_id, i))
            if job and getattr(job, "next_run_time", None):
                out[i] = job.next_run_time.isoformat()
    except Exception:
        pass
    return out


# ==================== 请求模型 ====================

class SlotBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    cron_expression: str
    enabled: bool = True
    group_ids: List[str] = Field(default_factory=list)
    analysis_depth: Optional[int] = None


class ConfigBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=svc.MAX_NAME_LEN)
    description: str = ""
    enabled: bool = True
    time_slots: List[SlotBody] = Field(..., min_length=1, max_length=svc.MAX_SLOTS)
    default_group_ids: List[str] = Field(default_factory=list)
    default_analysis_depth: int = 3
    notify_on_complete: bool = True
    notify_on_error: bool = True
    send_email: bool = False


class ToggleBody(BaseModel):
    enabled: bool


class RunBody(BaseModel):
    slot_index: int = Field(0, ge=0)


class CronPreviewBody(BaseModel):
    cron_expression: str
    count: int = Field(5, ge=1, le=10)


def _uid(user: Any) -> str:
    return str((user or {}).get("id") or (user or {}).get("_id") or "")


def _fail(e: svc.ScheduleError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


async def _load(db, uid: str, config_id: str) -> Dict[str, Any]:
    try:
        doc = await db[svc.COLLECTION].find_one({"_id": svc.to_oid(config_id), "user_id": uid})
    except svc.ScheduleError as e:
        _fail(e)
    if not doc:
        raise HTTPException(status_code=404, detail="配置不存在")
    return doc


async def _ensure_unique_name(db, uid: str, name: str, exclude=None):
    q: Dict[str, Any] = {"user_id": uid, "name": name}
    if exclude is not None:
        q["_id"] = {"$ne": exclude}
    if await db[svc.COLLECTION].find_one(q, {"_id": 1}):
        raise HTTPException(status_code=409, detail=f"已存在同名配置：{name}")


# ==================== 路由 ====================

@router.get("/configs")
async def list_configs(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    uid = _uid(user)
    items = []
    async for doc in db[svc.COLLECTION].find({"user_id": uid}).sort("created_at", -1):
        items.append(svc.serialize(doc, _next_runs(str(doc["_id"]), len(doc.get("time_slots") or []))
                                   if doc.get("enabled") else None))
    return {"success": True, "data": {
        "items": items, "timezone": settings.TIMEZONE, "min_interval_minutes": svc.MIN_INTERVAL_MINUTES,
        "max_configs": svc.MAX_CONFIGS_PER_USER, "max_slots": svc.MAX_SLOTS}}


@router.post("/configs", dependencies=[Depends(require_pro)])
async def create_config(body: ConfigBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    uid = _uid(user)
    try:
        fields = await svc.normalize_config(db, uid, body.model_dump(), settings.TIMEZONE)
    except svc.ScheduleError as e:
        _fail(e)
    if await db[svc.COLLECTION].count_documents({"user_id": uid}) >= svc.MAX_CONFIGS_PER_USER:
        raise HTTPException(status_code=400, detail=f"最多创建 {svc.MAX_CONFIGS_PER_USER} 个定时分析配置")
    await _ensure_unique_name(db, uid, fields["name"])
    now = now_tz()
    doc = {**fields, "user_id": uid, "created_at": now, "updated_at": now}
    res = await db[svc.COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    doc["id"] = str(res.inserted_id)
    await register_config(doc)
    logger.info("[定时分析] 创建 user=%s id=%s", uid, doc["id"])
    return {"success": True, "data": svc.serialize(doc, _next_runs(doc["id"], len(doc["time_slots"])))}


@router.get("/configs/{config_id}")
async def get_config(config_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    doc = await _load(db, _uid(user), config_id)
    return {"success": True, "data": svc.serialize(doc, _next_runs(config_id, len(doc.get("time_slots") or [])))}


@router.put("/configs/{config_id}", dependencies=[Depends(require_pro)])
async def update_config(config_id: str, body: ConfigBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    uid = _uid(user)
    old = await _load(db, uid, config_id)
    try:
        fields = await svc.normalize_config(db, uid, body.model_dump(), settings.TIMEZONE,
                                            existing_slots=old.get("time_slots"))
    except svc.ScheduleError as e:
        _fail(e)
    await _ensure_unique_name(db, uid, fields["name"], old["_id"])
    fields["updated_at"] = now_tz()
    await db[svc.COLLECTION].update_one({"_id": old["_id"]}, {"$set": fields})
    doc = await _load(db, uid, config_id)
    doc["id"] = config_id
    await register_config(doc)
    return {"success": True, "data": svc.serialize(doc, _next_runs(config_id, len(doc["time_slots"])))}


@router.post("/configs/{config_id}/toggle", dependencies=[Depends(require_pro)])
async def toggle_config(config_id: str, body: ToggleBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    uid = _uid(user)
    doc = await _load(db, uid, config_id)
    await db[svc.COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"enabled": body.enabled, "updated_at": now_tz()}})
    doc["enabled"] = body.enabled
    doc["id"] = config_id
    await register_config(doc)
    return {"success": True, "data": svc.serialize(doc, _next_runs(config_id, len(doc.get("time_slots") or []))
                                                   if body.enabled else None)}


@router.delete("/configs/{config_id}", dependencies=[Depends(require_pro)])
async def delete_config(config_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    doc = await _load(db, _uid(user), config_id)
    await unregister_config(config_id)
    await db[svc.COLLECTION].delete_one({"_id": doc["_id"]})
    return {"success": True}


@router.post("/configs/{config_id}/run", dependencies=[Depends(require_pro)])
async def run_now(config_id: str, body: RunBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    """立即执行某个时间段（异步，结果见执行历史）。会创建真实的 LLM 分析任务。"""
    uid = _uid(user)
    doc = await _load(db, uid, config_id)
    slots = doc.get("time_slots") or []
    if body.slot_index >= len(slots):
        raise HTTPException(status_code=400, detail="时间段不存在")
    if not doc.get("enabled"):
        raise HTTPException(status_code=400, detail="配置已停用，请先启用")
    if not slots[body.slot_index].get("enabled", True):
        raise HTTPException(status_code=400, detail="该时间段已停用")

    key = f"{config_id}:{body.slot_index}"
    if key in _running:
        raise HTTPException(status_code=409, detail="该时间段正在执行中")
    if time.monotonic() - _last_run_now.get(key, 0) < RUN_NOW_COOLDOWN:
        raise HTTPException(status_code=429, detail=f"操作过于频繁，请 {RUN_NOW_COOLDOWN} 秒后再试")
    _last_run_now[key] = time.monotonic()

    from app.worker.watchlist_analysis_task import run_scheduled_analysis_slot
    _running.add(key)

    async def _run():
        try:
            await run_scheduled_analysis_slot(config_id, uid, body.slot_index)
        finally:
            _running.discard(key)

    asyncio.create_task(_run())
    logger.info("[定时分析] 手动触发 user=%s config=%s slot=%s", uid, config_id, body.slot_index)
    return {"success": True, "message": "已开始执行，稍后在执行历史中查看结果"}


@router.get("/history")
async def history(config_id: Optional[str] = Query(None), limit: int = Query(20, ge=1, le=100),
                  db=Depends(get_mongo_db), user=Depends(get_current_user)):
    uid = _uid(user)
    ids = [str(d["_id"]) async for d in db[svc.COLLECTION].find({"user_id": uid}, {"_id": 1})]
    if config_id:
        if config_id not in ids:
            raise HTTPException(status_code=404, detail="配置不存在")
        ids = [config_id]
    items = []
    cursor = db[svc.HISTORY_COLLECTION].find({"config_id": {"$in": ids}}).sort("created_at", -1).limit(limit)
    async for h in cursor:
        items.append({
            "id": str(h["_id"]), "config_id": h.get("config_id"), "config_name": h.get("config_name"),
            "time_slot_name": h.get("time_slot_name"), "status": h.get("status"),
            "total_count": h.get("total_count", 0), "success_count": h.get("success_count", 0),
            "failed_count": h.get("failed_count", 0), "task_ids": h.get("task_ids", []),
            "result_summary": h.get("result_summary"),
            "created_at": h["created_at"].isoformat() if isinstance(h.get("created_at"), datetime) else h.get("created_at"),
        })
    return {"success": True, "data": {"items": items}}


@router.post("/cron/preview")
async def cron_preview(body: CronPreviewBody, user=Depends(get_current_user)):
    """校验 CRON 并返回接下来几次触发时间（供表单即时反馈）。"""
    try:
        expr = svc.validate_cron(body.cron_expression, settings.TIMEZONE)
        times = svc.next_fire_times(expr, settings.TIMEZONE, body.count)
    except svc.ScheduleError as e:
        return {"success": True, "data": {"valid": False, "message": e.message, "next": []}}
    return {"success": True, "data": {"valid": True, "message": "", "cron_expression": expr,
                                      "next": [t.isoformat() for t in times]}}
