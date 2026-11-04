"""渠道推送配置路由（Pro）：飞书 / 钉钉 / 企业微信 / 邮件渠道的增删改查与测试推送

挂载前缀 /api/channel-configs。配置存放于 user_channel_configs（见 app/services/channel_notifier.py），
通知产生时由 NotificationsService → ChannelNotifier.dispatch 按「启用 + 订阅事件」匹配推送。

安全：Webhook 地址只接受平台官方域名（防 SSRF），接口不回传明文地址。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.permissions import require_pro
from app.models.channel_config import CHANNEL_TYPE_NAMES, EVENT_TYPES, ChannelConfig, ChannelType
from app.pro.services import notification_channel_service as svc
from app.routers.auth_db import get_current_user
from app.services.channel_notifier import get_channel_notifier
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-configs", tags=["channel-configs"])

EVENT_LABELS = {
    "analysis_complete": "分析完成",
    "alert": "预警（止盈止损 / 交易告警）",
    "skill_gap": "工具缺口感知",
    "system": "系统通知",
}
TEST_COOLDOWN_SECONDS = 5

# 渠道配置以“用户整份文档覆盖写”的方式保存，同进程内按用户串行化读-改-写，避免并发请求互相覆盖
_user_locks: Dict[str, asyncio.Lock] = {}
_last_test_at: Dict[str, float] = {}


def _lock_for(user_id: str) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


def _uid(user: Any) -> str:
    return str((user or {}).get("id") or (user or {}).get("_id") or "")


def _bad(e: svc.ValidationError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


class ChannelCreate(BaseModel):
    type: ChannelType
    name: str = Field(..., min_length=1, max_length=50)
    enabled: bool = True
    webhook_url: Optional[str] = None
    email_address: Optional[str] = None
    events: List[str] = Field(default_factory=lambda: ["analysis_complete", "alert"])


class ChannelUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    enabled: Optional[bool] = None
    webhook_url: Optional[str] = Field(None, description="留空表示不修改")
    email_address: Optional[str] = None
    events: Optional[List[str]] = None


class TestRequest(BaseModel):
    title: str = Field("TradingAgents-CN 测试通知", max_length=100)
    content: str = Field("这是一条测试消息，如果您收到此消息，说明渠道配置正确。", max_length=500)


@router.get("")
async def list_channels(user=Depends(get_current_user)):
    channels = await get_channel_notifier().get_channels(_uid(user))
    return {
        "success": True,
        "data": {
            "items": [svc.serialize_channel(c) for c in channels],
            "types": [{"value": t.value, "label": CHANNEL_TYPE_NAMES[t]} for t in ChannelType],
            "events": [{"value": e, "label": EVENT_LABELS.get(e, e)} for e in EVENT_TYPES],
            "max_channels": svc.MAX_CHANNELS_PER_USER,
        },
    }


@router.post("", dependencies=[Depends(require_pro)])
async def create_channel(req: ChannelCreate, user=Depends(get_current_user)):
    uid = _uid(user)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="渠道名称不能为空")
    try:
        events = svc.validate_events(req.events)
        webhook_url = email = None
        if req.type == ChannelType.EMAIL:
            email = svc.validate_email_address(req.email_address)
        else:
            webhook_url = svc.validate_webhook_url(req.type, req.webhook_url)
    except svc.ValidationError as e:
        _bad(e)

    notifier = get_channel_notifier()
    async with _lock_for(uid):
        existing = await notifier.get_channels(uid)
        if len(existing) >= svc.MAX_CHANNELS_PER_USER:
            raise HTTPException(status_code=400, detail=f"最多配置 {svc.MAX_CHANNELS_PER_USER} 个渠道")
        if any(c.name == name for c in existing):
            raise HTTPException(status_code=409, detail=f"已存在同名渠道：{name}")
        channel = await notifier.add_channel(uid, ChannelConfig(
            channel_id="", type=req.type, name=name, enabled=req.enabled,
            webhook_url=webhook_url, email_address=email, events=events,
        ))
    logger.info("[渠道推送] 新增 user=%s type=%s id=%s", uid, req.type.value, channel.channel_id)
    return {"success": True, "data": svc.serialize_channel(channel)}


@router.put("/{channel_id}", dependencies=[Depends(require_pro)])
async def update_channel(channel_id: str, req: ChannelUpdate, user=Depends(get_current_user)):
    uid = _uid(user)
    notifier = get_channel_notifier()
    async with _lock_for(uid):
        channels = await notifier.get_channels(uid)
        current = next((c for c in channels if c.channel_id == channel_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="渠道不存在")

        updates: Dict[str, Any] = {}
        try:
            if req.name is not None:
                name = req.name.strip()
                if not name:
                    raise svc.ValidationError("渠道名称不能为空")
                if any(c.name == name and c.channel_id != channel_id for c in channels):
                    raise HTTPException(status_code=409, detail=f"已存在同名渠道：{name}")
                updates["name"] = name
            if req.enabled is not None:
                updates["enabled"] = req.enabled
            if req.events is not None:
                updates["events"] = svc.validate_events(req.events)
            if req.webhook_url:  # 空串/None 均视为不修改
                if current.type == ChannelType.EMAIL:
                    raise svc.ValidationError("邮件渠道不使用 Webhook 地址")
                updates["webhook_url"] = svc.validate_webhook_url(current.type, req.webhook_url)
            if req.email_address is not None:
                if current.type != ChannelType.EMAIL:
                    raise svc.ValidationError("仅邮件渠道可设置邮箱地址")
                updates["email_address"] = svc.validate_email_address(req.email_address)
        except svc.ValidationError as e:
            _bad(e)
        if not updates:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")
        updated = await notifier.update_channel(uid, channel_id, updates)
    return {"success": True, "data": svc.serialize_channel(updated)}


@router.delete("/{channel_id}", dependencies=[Depends(require_pro)])
async def delete_channel(channel_id: str, user=Depends(get_current_user)):
    uid = _uid(user)
    async with _lock_for(uid):
        ok = await get_channel_notifier().delete_channel(uid, channel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return {"success": True}


@router.post("/{channel_id}/test", dependencies=[Depends(require_pro)])
async def test_channel(channel_id: str, req: TestRequest, user=Depends(get_current_user)):
    """向该渠道发一条测试消息，并返回平台/SMTP 的真实结果（不是“已提交”）。"""
    uid = _uid(user)
    channels = await get_channel_notifier().get_channels(uid)
    channel = next((c for c in channels if c.channel_id == channel_id), None)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    key = f"{uid}:{channel_id}"
    now = time.monotonic()
    if now - _last_test_at.get(key, 0) < TEST_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail=f"测试过于频繁，请 {TEST_COOLDOWN_SECONDS} 秒后再试")
    _last_test_at[key] = now

    if channel.type == ChannelType.EMAIL:
        from app.services.email_service import get_email_service
        email_service = get_email_service()
        smtp = await email_service.get_smtp_config()
        if not smtp:
            return {"success": False, "message": "系统尚未配置 SMTP，请联系管理员在「邮件通知」页配置发信服务器"}
        ok, message = await email_service.deliver(
            channel.email_address, f"[TradingAgents-CN] {req.title}",
            f"<p>{_escape(req.content)}</p>", req.content, smtp,
        )
    else:
        from app.services.channel_senders.webhook_sender import send_webhook_checked
        ok, message = await send_webhook_checked(channel, req.title, req.content)
    return {"success": ok, "message": message, "tested_at": now_tz().isoformat()}


def _escape(text: str) -> str:
    import html
    return html.escape(text)
