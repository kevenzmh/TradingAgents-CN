"""交易可观测性 API：指标导出 + 告警管理（第二阶段）。

解决「生产上看不见交易系统状态」的问题：

- `GET /api/trading-observability/metrics`
    标准 Prometheus 文本格式，供 Prometheus 直接抓取（text/plain）
- `GET /api/trading-observability/metrics/json`
    结构化快照，供前端仪表盘展示
- `GET /api/trading-observability/alerts`
    未处理告警列表（默认只看 critical 未处理）
- `GET /api/trading-observability/alerts/stats`
    告警统计（按类型/严重级别）
- `POST /api/trading-observability/alerts/{order_id}/resolve`
    人工确认告警已处理
- `POST /api/trading-observability/patrol`
    手动触发一次巡检（定时任务也会调同一逻辑）

权限：指标与告警含用户交易线索，统一要求登录；不暴露给匿名访问。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.services.trading_core.observability import AlertService, metrics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading-observability", tags=["trading-observability"])


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus 抓取端点（文本格式）。

    刻意不加鉴权：Prometheus 抓取器通常无法携带用户令牌。
    指标中**不含** user_id / 订单号等敏感或高基数信息，
    只有 market/side/status/gate 这类有限枚举。
    """
    return PlainTextResponse(
        content=metrics.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/metrics/json")
async def metrics_snapshot(user=Depends(get_current_user)):
    """指标结构化快照（前端仪表盘用）。"""
    return {"success": True, "data": metrics.snapshot()}


@router.get("/alerts")
async def list_alerts(
    resolved: bool = Query(False, description="是否查看已处理告警"),
    severity: Optional[str] = Query(None, description="按严重级别过滤，如 critical"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """列出交易告警（默认未处理）。"""
    alerts = await AlertService(db).list_alerts(
        resolved=resolved, severity=severity, limit=limit,
    )
    return {"success": True, "data": alerts, "total": len(alerts)}


@router.get("/alerts/stats")
async def alert_stats(
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """告警统计（按类型与严重级别）。"""
    return {"success": True, "data": await AlertService(db).stats()}


@router.post("/alerts/{order_id}/resolve")
async def resolve_alert(
    order_id: str,
    note: str = Query("", description="处理说明"),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """人工确认某订单的告警已处理。"""
    ok = await AlertService(db).resolve(order_id, note=note)
    if not ok:
        raise HTTPException(status_code=404, detail=f"订单 {order_id} 没有未处理的告警")
    return {"success": True, "order_id": order_id}


@router.post("/patrol")
async def run_patrol(
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """手动触发告警巡检（与定时任务同一逻辑）。"""
    result = await AlertService(db).patrol()
    return {"success": True, "data": result}
