"""决策结算闭环 API：结算状态查询与手动触发结算。"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.routers.auth_db import get_current_user
from app.services.decision_settlement_service import (
    get_settlement_status,
    run_settlement_sync,
)

router = APIRouter(prefix="/api/decisions", tags=["decision-settlement"])
logger = logging.getLogger(__name__)


@router.get("/settlement-status")
async def decision_settlement_status(user: dict = Depends(get_current_user)):
    """决策结算状态：总数、待结算、已结算及按结论倾向的命中率与平均 alpha。"""
    try:
        status = await get_settlement_status()
        return {"success": True, "data": status}
    except Exception as e:
        logger.error(f"❌ 查询决策结算状态失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/settle")
async def trigger_decision_settlement(user: dict = Depends(get_current_user)):
    """手动触发一次全量结算（与每交易日收盘后的定时任务同一入口）。"""
    import asyncio

    try:
        # 结算含在线行情拉取与 LLM 反思，放到线程池避免阻塞事件循环
        summary = await asyncio.to_thread(run_settlement_sync)
        return {
            "success": True,
            "data": {
                "settled": summary["settled"],
                "failed": summary["failed"],
                "failures": [
                    {"ticker": t, "date": d, "reason": r}
                    for t, d, r in summary["failures"]
                ],
            },
        }
    except Exception as e:
        logger.error(f"❌ 手动结算失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get("/lessons")
async def decision_lessons(
    symbol: str = "",
    user: dict = Depends(get_current_user),
):
    """查看该股票的历史决策教训（注入后续分析的同一内容）。"""
    try:
        from tradingagents.memory import create_memory_log

        symbol_clean = symbol.strip()
        if not symbol_clean:
            return {"success": False, "error": "缺少 symbol 参数"}
        memory_log = create_memory_log()
        context = memory_log.get_past_context(symbol_clean)
        return {"success": True, "data": {"symbol": symbol_clean, "lessons": context}}
    except Exception as e:
        logger.error(f"❌ 查询决策教训失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
