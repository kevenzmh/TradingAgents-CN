"""交易核心 API：信号/订单查询、人工确认、风控状态、kill switch。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.services.trading_core import TradingExecutionEngine

router = APIRouter(prefix="/api/trading", tags=["trading-core"])
logger = logging.getLogger(__name__)


def _resolve_user_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("id") or "")


@router.get("/signals")
async def list_signals(
    limit: int = Query(20, ge=1, le=100),
    symbol: str = Query("", description="按代码过滤"),
    current_user: dict = Depends(get_current_user),
):
    """查询交易信号（分析产出的可交易倾向记录）。"""
    db = get_mongo_db()
    query = {"user_id": _resolve_user_id(current_user)}
    if symbol.strip():
        query["symbol"] = symbol.strip()
    cursor = db["trading_signals"].find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"success": True, "data": {"signals": items}}


@router.get("/orders")
async def list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """查询交易引擎订单（含风控拒绝与待确认单）。"""
    db = get_mongo_db()
    query = {"user_id": _resolve_user_id(current_user)}
    if status_filter:
        query["status"] = status_filter
    cursor = db["trading_orders"].find(query).sort("created_at", -1).limit(limit)
    items = []
    for doc in await cursor.to_list(length=limit):
        doc["order_id"] = str(doc.pop("_id", ""))
        # 风控细节里的 ObjectId 不可序列化场景已避免（risk_check 为纯 dict）
        items.append(doc)
    return {"success": True, "data": {"orders": items}}


class ConfirmRequest(BaseModel):
    order_id: str


@router.post("/orders/confirm")
async def confirm_order(
    req: ConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """人工确认待确认订单（HITL）：确认后重跑风控再撮合。"""
    db = get_mongo_db()
    engine = TradingExecutionEngine(db)
    result = await engine.confirm_order(req.order_id, _resolve_user_id(current_user))
    if result.get("success") is False:
        return JSONResponse(status_code=400, content={"success": False, "error": result.get("error")})
    return {"success": True, "data": result}


@router.post("/orders/cancel")
async def cancel_order(
    req: ConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """取消待确认订单。"""
    db = get_mongo_db()
    engine = TradingExecutionEngine(db)
    result = await engine.cancel_order(req.order_id, _resolve_user_id(current_user))
    if result.get("success") is False:
        return JSONResponse(status_code=400, content={"success": False, "error": result.get("error")})
    return {"success": True, "data": result}


@router.get("/risk/status")
async def risk_status(current_user: dict = Depends(get_current_user)):
    """风控状态：模式、限额、今日已用、kill switch。"""
    from app.services.trading_core import RiskGate

    db = get_mongo_db()
    gate = RiskGate(db)
    status = await gate.status(_resolve_user_id(current_user))
    return {"success": True, "data": status}


class KillSwitchRequest(BaseModel):
    reason: str = "手动触发"
    off: bool = False


@router.post("/risk/kill-switch")
async def set_kill_switch(
    req: KillSwitchRequest,
    current_user: dict = Depends(get_current_user),
):
    """设置/解除全局交易熔断。开启后所有执行请求立即拒绝。"""
    # 仅 admin 可操作（社区版单管理员体系）
    if not current_user.get("is_admin"):
        return JSONResponse(status_code=403, content={"success": False, "error": "仅管理员可操作"})

    from app.services.trading_core import RiskGate

    db = get_mongo_db()
    gate = RiskGate(db)
    doc = await gate.set_kill_switch(req.reason, off=req.off)
    return {"success": True, "data": doc}
