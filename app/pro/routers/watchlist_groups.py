"""自选股分组路由（Pro）：挂载前缀 /api/watchlist-groups

读取对所有登录用户开放；写入端点依赖 require_pro。业务规则见 app/pro/services/watchlist_group_service.py。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.permissions import require_pro
from app.pro.services import watchlist_group_service as svc
from app.routers.auth_db import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist-groups", tags=["watchlist-groups"])


def _uid(user: Any) -> str:
    return str((user or {}).get("id") or (user or {}).get("_id") or "")


def _fail(e: svc.GroupError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=svc.MAX_NAME_LEN)
    description: str = ""
    color: Optional[str] = None
    stock_codes: List[str] = Field(default_factory=list)
    analysis_depth: Optional[int] = None
    is_active: bool = True


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    analysis_depth: Optional[int] = Field(None, description="1-5；传 0 表示清除（跟随配置默认）")
    is_active: Optional[bool] = None


class CodesBody(BaseModel):
    codes: List[str] = Field(..., min_length=1, max_length=svc.MAX_STOCKS_PER_GROUP)


class ImportBody(BaseModel):
    tag: Optional[str] = Field(None, max_length=50, description="只导入带该标签的自选股")


class OrderBody(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=svc.MAX_GROUPS)


@router.get("")
async def list_groups(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    groups = await svc.list_groups(db, _uid(user))
    names = await svc.resolve_names(db, (c for g in groups for c in g["stock_codes"]))
    for g in groups:
        g["stocks"] = [{"code": c, "name": names.get(c) or names.get(c.zfill(6)) or ""} for c in g["stock_codes"]]
    return {"success": True, "data": {"items": groups, "max_groups": svc.MAX_GROUPS,
                                      "max_stocks": svc.MAX_STOCKS_PER_GROUP}}


@router.put("/order", dependencies=[Depends(require_pro)])
async def reorder(body: OrderBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        await svc.reorder(db, _uid(user), body.ids)
    except svc.GroupError as e:
        _fail(e)
    return {"success": True}


@router.post("", dependencies=[Depends(require_pro)])
async def create_group(body: GroupCreate, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        data = await svc.create_group(db, _uid(user), name=body.name, description=body.description,
                                      color=body.color, stock_codes=body.stock_codes,
                                      analysis_depth=body.analysis_depth, is_active=body.is_active)
    except svc.GroupError as e:
        _fail(e)
    return {"success": True, "data": data}


@router.put("/{group_id}", dependencies=[Depends(require_pro)])
async def update_group(group_id: str, body: GroupUpdate, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if fields.get("analysis_depth") == 0:
        fields["analysis_depth"] = None
    try:
        data = await svc.update_group(db, _uid(user), group_id, **fields)
    except svc.GroupError as e:
        _fail(e)
    return {"success": True, "data": data}


@router.delete("/{group_id}", dependencies=[Depends(require_pro)])
async def delete_group(group_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        await svc.delete_group(db, _uid(user), group_id)
    except svc.GroupError as e:
        _fail(e)
    return {"success": True}


@router.post("/{group_id}/stocks", dependencies=[Depends(require_pro)])
async def add_stocks(group_id: str, body: CodesBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.add_stocks(db, _uid(user), group_id, body.codes)}
    except svc.GroupError as e:
        _fail(e)


@router.post("/{group_id}/stocks/remove", dependencies=[Depends(require_pro)])
async def remove_stocks(group_id: str, body: CodesBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.remove_stocks(db, _uid(user), group_id, body.codes)}
    except svc.GroupError as e:
        _fail(e)


@router.post("/{group_id}/import-favorites", dependencies=[Depends(require_pro)])
async def import_favorites(group_id: str, body: ImportBody, db=Depends(get_mongo_db),
                           user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.import_from_favorites(db, _uid(user), group_id, body.tag)}
    except svc.GroupError as e:
        _fail(e)
