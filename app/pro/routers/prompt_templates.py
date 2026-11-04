"""提示词模板管理路由（Pro）

挂载前缀 /api/prompt-templates。读取对所有登录用户开放；写入端点依赖 require_pro。
业务规则见 app/pro/services/prompt_template_service.py。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.pro.services import prompt_template_service as svc
from app.core.permissions import require_pro

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])


def _uid(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("id") or user.get("_id") or "")
    return str(getattr(user, "id", "") or "")


def _raise(e: svc.TemplateError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


class CreateRequest(BaseModel):
    agent_type: str = Field(..., min_length=1, max_length=64)
    agent_name: str = Field(..., min_length=1, max_length=128)
    template_name: str = Field(..., min_length=1, max_length=svc.MAX_NAME_LEN)
    content: Dict[str, Any]
    remark: str = ""
    base_template_id: Optional[str] = Field(None, description="基于哪个模板创建（用于记录来源）")


class UpdateRequest(BaseModel):
    template_name: Optional[str] = None
    content: Optional[Dict[str, Any]] = None
    remark: Optional[str] = None


class DuplicateRequest(BaseModel):
    template_name: Optional[str] = None


class PreviewRequest(BaseModel):
    template_id: Optional[str] = Field(None, description="按已有模板预览；与 content 二选一")
    content: Optional[Dict[str, Any]] = Field(None, description="按编辑中的内容预览（不落库）")
    variables: Dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def list_templates(
    scope: Literal["all", "system", "mine"] = Query("all"),
    agent_type: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    status: Optional[Literal["draft", "active"]] = Query(None),
    keyword: Optional[str] = Query(None, max_length=100),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_mongo_db), user=Depends(get_current_user),
):
    try:
        data = await svc.list_templates(db, _uid(user), scope=scope, agent_type=agent_type,
                                        agent_name=agent_name, status=status, keyword=keyword,
                                        skip=skip, limit=limit)
    except svc.TemplateError as e:
        _raise(e)
    return {"success": True, "data": data}


@router.get("/agents")
async def list_agents(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.list_agents(db, _uid(user))}
    except svc.TemplateError as e:
        _raise(e)


@router.post("/preview")
async def preview(req: PreviewRequest, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        if bool(req.template_id) == bool(req.content is not None):
            raise svc.TemplateError("template_id 与 content 必须二选一", 400)
        if req.template_id:
            content = (await svc.get_template(db, _uid(user), req.template_id))["content"]
        else:
            content = svc.normalize_content(req.content)
        result = svc.render_preview(content, req.variables)
        result["variables"] = svc.extract_variables(content)
        return {"success": True, "data": result}
    except svc.TemplateError as e:
        _raise(e)


@router.get("/{template_id}")
async def get_template(template_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.get_template(db, _uid(user), template_id)}
    except svc.TemplateError as e:
        _raise(e)


@router.post("", dependencies=[Depends(require_pro)])
async def create_template(req: CreateRequest, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        data = await svc.create_template(
            db, _uid(user), agent_type=req.agent_type, agent_name=req.agent_name,
            template_name=req.template_name, content=req.content, remark=req.remark,
            base_template_id=req.base_template_id)
    except svc.TemplateError as e:
        _raise(e)
    logger.info("[提示词模板] 创建 user=%s %s/%s id=%s", _uid(user), req.agent_type, req.agent_name, data["id"])
    return {"success": True, "data": data}


@router.put("/{template_id}", dependencies=[Depends(require_pro)])
async def update_template(template_id: str, req: UpdateRequest, db=Depends(get_mongo_db),
                          user=Depends(get_current_user)):
    try:
        data = await svc.update_template(db, _uid(user), template_id, template_name=req.template_name,
                                         content=req.content, remark=req.remark)
    except svc.TemplateError as e:
        _raise(e)
    return {"success": True, "data": data}


@router.post("/{template_id}/duplicate", dependencies=[Depends(require_pro)])
async def duplicate_template(template_id: str, req: DuplicateRequest, db=Depends(get_mongo_db),
                             user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.duplicate_template(db, _uid(user), template_id, req.template_name)}
    except svc.TemplateError as e:
        _raise(e)


@router.post("/{template_id}/publish", dependencies=[Depends(require_pro)])
async def publish_template(template_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.set_status(db, _uid(user), template_id, "active")}
    except svc.TemplateError as e:
        _raise(e)


@router.post("/{template_id}/unpublish", dependencies=[Depends(require_pro)])
async def unpublish_template(template_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        return {"success": True, "data": await svc.set_status(db, _uid(user), template_id, "draft")}
    except svc.TemplateError as e:
        _raise(e)


@router.post("/{template_id}/activate", dependencies=[Depends(require_pro)])
async def activate_template(template_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        data = await svc.activate_template(db, _uid(user), template_id)
    except svc.TemplateError as e:
        _raise(e)
    logger.info("[提示词模板] 生效 user=%s %s/%s id=%s", _uid(user), data["agent_type"], data["agent_name"], template_id)
    return {"success": True, "data": data}


@router.delete("/active/{agent_type}/{agent_name}", dependencies=[Depends(require_pro)])
async def deactivate_agent(agent_type: str, agent_name: str, db=Depends(get_mongo_db),
                           user=Depends(get_current_user)):
    """取消该 Agent 的自定义模板，恢复系统默认。"""
    try:
        removed = await svc.deactivate_agent(db, _uid(user), agent_type, agent_name)
    except svc.TemplateError as e:
        _raise(e)
    return {"success": True, "removed": removed}


@router.delete("/{template_id}", dependencies=[Depends(require_pro)])
async def delete_template(template_id: str, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        await svc.delete_template(db, _uid(user), template_id)
    except svc.TemplateError as e:
        _raise(e)
    return {"success": True}
