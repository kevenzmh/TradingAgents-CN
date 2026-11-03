"""定时分析调度注册（社区版重建：最小可用契约）

原 Pro 版为定时分析任务的完整调度注册（含 cron 解析、任务编排、通知）。
社区版按调用方契约（core/tools/implementations/assistant_ops/schedule_manager.py）
重建两个注册函数；配置落库已由调用方完成，本模块负责把配置挂到
APScheduler（统一调度服务），实现定时分析真正可执行。

调度语义（对齐 Pro）：cron 到点 → 对每个分组股票按默认深度创建分析任务。
"""

import logging
from typing import Any, Dict, Optional

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def _run_scheduled_analysis_job(config: Dict[str, Any]) -> None:
    """定时任务入口：对配置内所有分组的股票创建分析任务"""
    try:
        from app.core.database import get_mongo_db
        from app.services.task_analysis_service import get_task_analysis_service
        from app.models.analysis import AnalysisTaskType

        db = get_mongo_db()
        service = get_task_analysis_service()
        user_id = config.get("user_id")

        # 展开分组 → 股票列表（watchlist_groups 存组内股票；配置的
        # default_group_ids 为空时回退到用户全部自选）
        group_ids = [str(g) for g in (config.get("default_group_ids") or []) if str(g)]
        codes: list = []
        if group_ids:
            cursor = db.watchlist_groups.find({"_id": {"$in": group_ids}}, {"stocks": 1})
            async for group in cursor:
                codes.extend([str(s.get("code") or s.get("stock_code") or "") for s in (group.get("stocks") or [])])
        else:
            fav = await db.user_favorites.find_one({"user_id": user_id})
            codes = [str(f.get("stock_code") or "") for f in ((fav or {}).get("favorites") or [])]
        codes = [c for c in codes if c][:10]  # 与批量分析上限一致

        if not codes:
            logger.info(f"[定时分析] 配置「{config.get('name')}」分组无股票，跳过")
            return

        depth_map = {1: "快速", 2: "基础", 3: "标准", 4: "深度", 5: "全面"}
        depth = depth_map.get(int(config.get("default_analysis_depth") or 3), "标准")

        created = 0
        for code in codes:
            try:
                from app.models.user import PyObjectId
                await service.create_task(
                    user_id=PyObjectId(user_id),
                    task_type=AnalysisTaskType.STOCK_ANALYSIS,
                    task_params={"symbol": code, "stock_code": code, "research_depth": depth},
                    engine_type="v2",
                )
                created += 1
            except Exception as e:
                logger.warning(f"[定时分析] 创建任务失败 {code}: {e}")

        logger.info(f"[定时分析] 配置「{config.get('name')}」已创建 {created} 个分析任务")
    except Exception as e:
        logger.error(f"[定时分析] 任务执行失败: {e}")


def _job_id(config_id: str, slot_key: Optional[str] = None) -> str:
    return f"scheduled_analysis:{config_id}:{slot_key or 'default'}"


async def _register_scheduled_tasks(config: Dict[str, Any]) -> None:
    """把定时分析配置的时段挂到 APScheduler"""
    from app.core.config import settings
    from app.services.scheduler_service import get_scheduler_service

    svc = get_scheduler_service()
    scheduler = svc.scheduler

    cron_expression = str(config.get("cron_expression") or "0 9 * * 1-5")
    config_id = str(config.get("id") or config.get("_id") or "")
    if not config_id:
        logger.warning("[定时分析] 配置缺少 id，跳过注册")
        return

    job_id = _job_id(config_id)
    try:
        trigger = CronTrigger.from_crontab(cron_expression, timezone=settings.TIMEZONE)
    except Exception as e:
        logger.warning(f"[定时分析] 无效 CRON「{cron_expression}」: {e}")
        return

    job_config = dict(config)
    scheduler.add_job(
        _run_scheduled_analysis_job,
        trigger,
        id=job_id,
        name=f"定时分析·{config.get('name', config_id)}",
        kwargs={"config": job_config},
        replace_existing=True,
    )
    logger.info(f"[定时分析] 已注册调度任务「{config.get('name')}」: {cron_expression}")


async def _unregister_scheduled_tasks(config_id: str) -> None:
    """移除定时分析配置的调度任务"""
    from app.services.scheduler_service import get_scheduler_service

    svc = get_scheduler_service()
    scheduler = svc.scheduler
    prefix = _job_id(str(config_id), "")
    try:
        for job in scheduler.get_jobs():
            if str(job.id).startswith(prefix):
                scheduler.remove_job(job.id)
                logger.info(f"[定时分析] 已移除调度任务 {job.id}")
    except Exception as e:
        logger.warning(f"[定时分析] 移除调度任务失败: {e}")
