"""数据导入 API（Pro）：历史K线 + 社媒消息批量导入，以及导入指南与测试程序下载。

已有的导入端点保持不变，这里补齐原先缺失的两类，并统一提供指南：

| 数据 | 端点 |
|---|---|
| 股票基本信息 | POST /api/stock-data/save-basic-info（既有） |
| 实时行情 | POST /api/stock-data/save-quotes（既有） |
| 财务数据 | POST /api/financial-data/save（既有） |
| 新闻数据 | POST /api/news-data/save（既有） |
| 历史K线 | POST /api/data-import/historical（本文件） |
| 社媒消息 | POST /api/data-import/social-media（本文件） |

本文件另提供社媒消息的查询 / 统计 / 删除，供「数据导入管理」页管理已导入数据。
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.permissions import require_pro
from app.pro.services import historical_import_service as kline_svc
from app.pro.services import social_media_service as sm_svc
from app.routers.auth_db import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data-import", tags=["data-import"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GUIDE_FILES = {
    "stock-data": PROJECT_ROOT / "docs" / "03-development" / "api" / "stock-data-import.md",
    "social-media": PROJECT_ROOT / "docs" / "03-development" / "api" / "social-media-import.md",
}
TEST_PROGRAM = PROJECT_ROOT / "scripts" / "test_batch_import_apis.py"


def _user_id(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("id") or user.get("_id") or user.get("username") or "")
    return str(getattr(user, "id", "") or getattr(user, "username", "") or "")


# ==================== 请求模型 ====================

class SocialMediaBatchRequest(BaseModel):
    messages: List[Dict[str, Any]] = Field(
        ..., min_length=1, max_length=sm_svc.MAX_BATCH,
        description=f"社媒消息列表（单次最多 {sm_svc.MAX_BATCH} 条）",
    )
    overwrite: bool = Field(False, description="已存在（同 platform + message_id）时是否覆盖")


class HistoricalBatchRequest(BaseModel):
    symbol: str = Field(..., description="股票代码，如 600519 / 600519.SH")
    market: Literal["CN", "HK", "US"] = Field("CN", description="市场")
    period: Literal["daily", "weekly", "monthly"] = Field("daily", description="K线周期")
    overwrite: bool = Field(True, description="同一交易日已存在时是否覆盖")
    records: List[Dict[str, Any]] = Field(
        ..., min_length=1, max_length=kline_svc.MAX_RECORDS,
        description=f"K线记录（单次最多 {kline_svc.MAX_RECORDS} 条）",
    )


# ==================== 历史K线 ====================

@router.post("/historical", dependencies=[Depends(require_pro)])
async def import_historical(req: HistoricalBatchRequest, user=Depends(get_current_user)):
    """批量导入历史K线（写入 stock_daily_quotes，数据源 user_import）。"""
    try:
        result = await kline_svc.save_records(
            req.symbol, req.records, market=req.market, period=req.period, overwrite=req.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result["saved"] == 0 and result["failed"] == result["total"]:
        raise HTTPException(status_code=422, detail={"message": "全部记录校验失败", **result})
    logger.info("[K线导入] user=%s %s %s %s: saved=%d failed=%d",
                _user_id(user), result["symbol"], req.market, req.period, result["saved"], result["failed"])
    return {"success": True, "data": result}


# ==================== 社媒消息 ====================

@router.post("/social-media", dependencies=[Depends(require_pro)])
async def import_social_media(req: SocialMediaBatchRequest, db=Depends(get_mongo_db),
                              user=Depends(get_current_user)):
    """批量导入社媒消息（自动补全情绪 / 关键词 / 重要性，规则方法）。"""
    result = await sm_svc.save_messages(db, req.messages, overwrite=req.overwrite, user_id=_user_id(user))
    if result["saved"] == 0 and result["updated"] == 0 and result["failed"] == result["total"]:
        raise HTTPException(status_code=422, detail={"message": "全部消息校验失败", **result})
    logger.info("[社媒导入] user=%s total=%d saved=%d updated=%d skipped=%d failed=%d",
                _user_id(user), result["total"], result["saved"], result["updated"],
                result["skipped"], result["failed"])
    return {"success": True, "data": result}


@router.get("/social-media")
async def list_social_media(
    symbol: Optional[str] = Query(None, description="股票代码"),
    platform: Optional[str] = Query(None, description="平台"),
    sentiment: Optional[str] = Query(None, description="positive / negative / neutral"),
    hours_back: Optional[int] = Query(None, ge=1, le=24 * 365, description="仅看最近 N 小时"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db=Depends(get_mongo_db), user=Depends(get_current_user),
):
    data = await sm_svc.list_messages(db, symbol=symbol, platform=platform, sentiment=sentiment,
                                      hours_back=hours_back, skip=skip, limit=limit)
    return {"success": True, "data": data}


@router.get("/social-media/stats")
async def social_media_stats(
    symbol: Optional[str] = Query(None),
    hours_back: Optional[int] = Query(None, ge=1, le=24 * 365),
    db=Depends(get_mongo_db), user=Depends(get_current_user),
):
    return {"success": True, "data": await sm_svc.message_stats(db, symbol=symbol, hours_back=hours_back)}


@router.delete("/social-media/{message_id}", dependencies=[Depends(require_pro)])
async def delete_social_media(message_id: str, platform: Optional[str] = Query(None),
                              db=Depends(get_mongo_db), user=Depends(get_current_user)):
    deleted = await sm_svc.delete_message(db, message_id, platform)
    if not deleted:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"success": True, "deleted": deleted}


# ==================== 指南与测试程序 ====================

@router.get("/guide/{kind}", response_class=PlainTextResponse)
async def get_guide(kind: Literal["stock-data", "social-media"], user=Depends(get_current_user)):
    """导入指南（Markdown 原文，前端渲染）。"""
    path = GUIDE_FILES[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"指南文件不存在: {path.name}")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@router.get("/test-program")
async def download_test_program(user=Depends(get_current_user)):
    """下载批量导入测试程序（命令行，覆盖全部 6 类数据）。"""
    if not TEST_PROGRAM.exists():
        raise HTTPException(status_code=404, detail="测试程序文件不存在")
    return FileResponse(str(TEST_PROGRAM), filename=TEST_PROGRAM.name, media_type="text/x-python")
