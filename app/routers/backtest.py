"""网格回测 API：启动/轮询/统计。

路径前缀 /api/backtest；Grid backtest 服务把结论写进回测专用
决策日志，与真实分析完全隔离。长任务跑在后台线程，状态落
backtest_runs 集合供前端轮询。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.services.grid_backtest_service import (
    get_backtest_status,
    get_backtest_summary,
    list_backtest_runs,
    start_backtest,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)


class StartBacktestRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, description="6位A股代码列表")
    start_date: str = Field(..., description="网格起点 YYYY-MM-DD")
    end_date: str = Field(..., description="网格终点 YYYY-MM-DD")
    every_n_days: int = Field(5, ge=1, le=30, description="网格步长（天）")
    research_depth: str = Field("标准", description="研究深度")


@router.post("/start")
async def start_grid_backtest(
    request: StartBacktestRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    """启动一次网格回测（后台执行，返回 run_id 供轮询）。"""
    # 网格回测耗模型算力：管理员限定
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可启动网格回测")

    # 同一时刻只允许一个 run（本地单模型，串行执行）
    running = await db["backtest_runs"].find_one({"status": {"$in": ["pending", "running", "settling"]}})
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"已有回测在执行（run={running.get('run_id')}，进度 {running.get('cells_done')}/{running.get('total_cells')}）",
        )

    try:
        run_id = await start_backtest(
            db,
            user_id=str(user.get("user_id") or user.get("id")),
            tickers=request.tickers,
            start_date=request.start_date,
            end_date=request.end_date,
            every_n_days=request.every_n_days,
            research_depth=request.research_depth,
        )
        return {"success": True, "data": {"run_id": run_id}}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ 启动回测失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get("/status/{run_id}")
async def backtest_status(
    run_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    """轮询一次回测的状态与进度。"""
    doc = await get_backtest_status(db, run_id)
    if not doc:
        raise HTTPException(status_code=404, detail="回测 run 不存在")
    return {"success": True, "data": doc}


@router.get("/runs")
async def backtest_runs(
    limit: int = 20,
    user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    """历史回测 run 列表（最近优先）。"""
    runs = await list_backtest_runs(db, limit=limit)
    return {"success": True, "data": runs}


@router.get("/summary/{run_id}")
async def backtest_summary(
    run_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_mongo_db),
):
    """对 run 的决策日志重算分倾向统计（pending 格子结算后会更新）。"""
    result = await get_backtest_summary(db, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="回测 run 不存在")
    if result.get("success") is False:
        raise HTTPException(status_code=409, detail=result.get("error", "统计不可用"))
    return {"success": True, "data": result}
