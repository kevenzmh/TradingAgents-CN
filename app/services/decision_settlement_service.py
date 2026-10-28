"""决策结算闭环服务：定时结算、状态查询、结算通知。

决策日志（tradingagents.memory）是结果表——每条研究结论记录为 made，
持有窗口走出后结算实际收益与相对基准的 alpha，并由 LLM 生成反思教训。
本服务提供后台结算入口（定时任务与分析前触发共用）、状态统计与通知。
"""

import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_SUMMARY_LIMIT = 5


def _build_reflector():
    """构建反思用 LLM（与 TaskAnalysisService 的构建方式一致）。

    京东云模式下 create_llm_by_provider 自动套 _JDYunRateLimitedLLM
    串行化代理，结算反思不会与并行 Agent 的调用叠加触发 429。
    """
    from tradingagents.memory import Reflector
    from tradingagents.graph.trading_graph import create_llm_by_provider
    from app.services.simple_analysis_service import (
        get_provider_and_url_by_model_sync,
        _load_model_config_from_db,
    )

    model = _default_quick_model_name()
    cfg = get_provider_and_url_by_model_sync(model)
    db_cfg = _load_model_config_from_db(model)
    llm = create_llm_by_provider(
        provider=cfg.get("provider", "dashscope"),
        model=model,
        backend_url=cfg.get("backend_url", ""),
        temperature=db_cfg.get("temperature", 0.3),
        max_tokens=db_cfg.get("max_tokens", 2000),
        timeout=db_cfg.get("timeout", 120),
        api_key=cfg.get("api_key"),
    )
    return Reflector(llm)


def _default_quick_model_name() -> str:
    """系统默认快速模型；数据库不可用时退回环境变量或 qwen-turbo。"""
    try:
        from pymongo import MongoClient
        from app.core.config import settings

        client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=3000)
        db = client[settings.MONGO_DB]
        doc = db.system_configs.find_one({"is_active": True}, sort=[("version", -1)])
        if doc:
            defaults = doc.get("default_models") or {}
            sys_settings = doc.get("system_settings") or {}
            model = (
                defaults.get("quick_analysis_model")
                or sys_settings.get("quick_analysis_model")
            )
            if model:
                return str(model)
    except Exception:
        pass
    import os

    return os.getenv("TRADINGAGENTS_QUICK_MODEL", "qwen-turbo")


def run_settlement_sync() -> Dict[str, Any]:
    """结算全部到期决策（同步，供线程池调用）。

    返回 {"settled": n, "failed": n, "failures": [(ticker, date, reason)]}。
    反思模型不可用时退化为仅记录结果，结算本身不受影响。
    """
    from tradingagents.memory import (
        create_memory_log,
        get_holding_period_days,
        settle_all_pending,
    )

    memory_log = create_memory_log()
    config = {"holding_period_days": get_holding_period_days()}

    try:
        reflector = _build_reflector()
    except Exception as e:
        logger.warning(f"⚠️ [决策结算] 反思模型不可用，退化为仅记录结果: {e}")
        reflector = None

    result = settle_all_pending(memory_log, reflector, config=config)
    summary = {
        "settled": len(result.settled),
        "failed": len(result.failed),
        "failures": result.failed[:_SUMMARY_LIMIT],
    }
    logger.info(
        f"📜 [决策结算] 全量结算完成: 已结算 {summary['settled']} 条, "
        f"失败 {summary['failed']} 条"
    )
    return summary


async def run_scheduled_settlement() -> Dict[str, Any]:
    """定时任务入口：线程池中执行全量结算，并对 admin 发送结算结果通知。"""
    summary = await asyncio.to_thread(run_settlement_sync)
    if summary["settled"]:
        try:
            await _notify_admin_settlement(summary)
        except Exception as e:
            logger.warning(f"⚠️ [决策结算] 结算通知发送失败(忽略): {e}")
    return summary


async def _notify_admin_settlement(summary: Dict[str, Any]) -> None:
    """把结算结果通知给管理员（社区版无多用户场景，通知 admin）。"""
    from app.core.database import get_mongo_db
    from app.models.notification import NotificationCreate
    from app.services.notifications_service import get_notifications_service

    db = get_mongo_db()
    admin_doc = await db.users.find_one(
        {"username": "admin"}, {"_id": 1}
    )
    if not admin_doc:
        return

    lines = []
    for ticker, date, reason in summary.get("failures", []):
        lines.append(f"{ticker} {date}: {reason}")
    failure_note = ""
    if lines:
        failure_note = "\n\n失败示例:\n" + "\n".join(lines)

    await get_notifications_service().create_and_publish(NotificationCreate(
        user_id=str(admin_doc["_id"]),
        type="system",
        title=f"📜 决策结算完成：{summary['settled']} 条研究结论已评分",
        content=(
            f"持有窗口已走出的 {summary['settled']} 条研究结论已结算实际收益与"
            f"相对基准的超额收益，并生成反思教训，将在后续分析中自动参考。"
            f"{failure_note}"
        ),
        link="/tasks/unified",
        source="decision_settlement",
        severity="success",
    ))


async def get_settlement_status() -> Dict[str, Any]:
    """结算状态统计（只读，供 API 展示）。"""
    from tradingagents.memory import create_memory_log, get_holding_period_days

    memory_log = create_memory_log()
    entries = memory_log.load_entries()
    pending = [e for e in entries if e.get("pending")]
    resolved = [e for e in entries if not e.get("pending")]

    by_rating: Dict[str, Dict[str, Any]] = {}
    for e in resolved:
        rating = e.get("rating") or "未知"
        bucket = by_rating.setdefault(rating, {"count": 0, "hit": 0, "alpha_sum": 0.0})
        bucket["count"] += 1
        alpha_text = (e.get("alpha") or "").strip().rstrip("%")
        try:
            bucket["alpha_sum"] += float(alpha_text) / 100
        except ValueError:
            pass
        # 方向命中：乐观要求 alpha>0，审慎要求 alpha<0，中性不计
        direction = 1 if rating == "乐观" else (-1 if rating == "审慎" else 0)
        if direction and alpha_text:
            try:
                if (float(alpha_text) / 100) * direction > 0:
                    bucket["hit"] += 1
            except ValueError:
                pass

    pending_by_ticker: Dict[str, int] = {}
    for e in pending:
        pending_by_ticker[e["ticker"]] = pending_by_ticker.get(e["ticker"], 0) + 1

    for rating, bucket in by_rating.items():
        bucket["mean_alpha"] = (
            round(bucket.pop("alpha_sum") / bucket["count"], 4) if bucket["count"] else 0.0
        )
        if rating in ("乐观", "审慎") and bucket["count"]:
            bucket["hit_rate"] = round(bucket["hit"] / bucket["count"], 4)
        bucket.pop("hit", None)

    return {
        "total": len(entries),
        "pending": len(pending),
        "resolved": len(resolved),
        "by_rating": by_rating,
        "pending_by_ticker": pending_by_ticker,
        "holding_period_days": get_holding_period_days(),
        "log_path": str(memory_log._log_path) if memory_log._log_path else None,
    }
