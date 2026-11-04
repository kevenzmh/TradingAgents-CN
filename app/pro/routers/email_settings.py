"""邮件通知设置路由（Pro）

挂载前缀 /api/email-settings：
- 用户级：通知开关 / 收件邮箱 / 通知类型 / 免打扰，测试邮件，发送历史
- 管理员级：SMTP 发信服务器配置与连接测试（全站共用）

SMTP 密码只写不读：GET 只回传 password_set；PUT 密码留空表示沿用已保存的密码。
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings as app_settings
from app.core.database import get_mongo_db
from app.core.permissions import require_pro
from app.models.email import EmailNotificationSettings
from app.pro.services import notification_channel_service as svc
from app.routers.auth_db import get_current_user
from app.services.email_service import SMTP_CONFIG_COLLECTION, get_email_service
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email-settings", tags=["email-settings"])

TEST_COOLDOWN_SECONDS = 10
_last_test_at: Dict[str, float] = {}


def _uid(user: Any) -> str:
    return str((user or {}).get("id") or (user or {}).get("_id") or "")


def _require_admin(user: Any) -> None:
    if not (user or {}).get("is_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可以配置 SMTP 发信服务器")


def _bad(e: svc.ValidationError):
    raise HTTPException(status_code=e.status_code, detail=e.message)


def _oid(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的用户ID")


# ==================== 用户级 ====================

class EmailSettingsBody(BaseModel):
    enabled: bool = False
    email_address: Optional[str] = None
    single_analysis: bool = True
    batch_analysis: bool = True
    scheduled_analysis: bool = True
    important_signals: bool = True
    system_notifications: bool = False
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"
    format: str = Field("html", pattern="^(html|text)$")
    language: str = Field("zh", pattern="^(zh|en)$")


@router.get("")
async def get_my_settings(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    doc = await db.users.find_one({"_id": _oid(_uid(user))}, {"email": 1, "preferences.email_notifications": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="用户不存在")
    stored = ((doc.get("preferences") or {}).get("email_notifications")) or {}
    current = EmailNotificationSettings(**stored) if stored else EmailNotificationSettings()
    smtp = await get_email_service().get_smtp_config()
    return {
        "success": True,
        "data": {
            "settings": current.model_dump(exclude={"include_charts"}),
            "account_email": doc.get("email"),
            "smtp_ready": smtp is not None,
        },
    }


@router.put("", dependencies=[Depends(require_pro)])
async def save_my_settings(body: EmailSettingsBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    try:
        address = svc.validate_email_address(body.email_address, required=False)
        if body.quiet_hours_enabled:
            svc.validate_quiet_hours(body.quiet_hours_start, body.quiet_hours_end)
    except svc.ValidationError as e:
        _bad(e)
    new = EmailNotificationSettings(**{**body.model_dump(), "email_address": address})
    res = await db.users.update_one(
        {"_id": _oid(_uid(user))}, {"$set": {"preferences.email_notifications": new.model_dump()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"success": True, "data": new.model_dump(exclude={"include_charts"})}


@router.post("/test", dependencies=[Depends(require_pro)])
async def send_test_email(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    """给自己发一封测试邮件，返回 SMTP 的真实结果。收件地址取已保存的“接收邮箱”，其次账号邮箱。"""
    uid = _uid(user)
    now = time.monotonic()
    if now - _last_test_at.get(uid, 0) < TEST_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail=f"测试过于频繁，请 {TEST_COOLDOWN_SECONDS} 秒后再试")
    _last_test_at[uid] = now

    doc = await db.users.find_one({"_id": _oid(uid)}, {"email": 1, "preferences.email_notifications": 1})
    stored = ((doc or {}).get("preferences") or {}).get("email_notifications") or {}
    to_email = stored.get("email_address") or (doc or {}).get("email")
    if not to_email:
        return {"success": False, "message": "未设置接收邮箱，请先填写并保存"}

    service = get_email_service()
    smtp = await service.get_smtp_config()
    if not smtp:
        return {"success": False, "message": "系统尚未配置 SMTP，请联系管理员配置发信服务器"}

    text = f"这是一封测试邮件，用于验证邮件通知配置。发送时间：{now_tz().strftime('%Y-%m-%d %H:%M:%S')}"
    ok, message = await service.deliver(
        to_email, "[TradingAgents-CN] 邮件通知测试", f"<p>{html.escape(text)}</p>", text, smtp)
    return {"success": ok, "message": message, "to": _mask_email(to_email)}


@router.get("/history")
async def email_history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                        user=Depends(get_current_user)):
    data = await get_email_service().get_email_history(_uid(user), page, page_size)
    for item in data["items"]:
        item.pop("user_id", None)
    return {"success": True, "data": data}


def _mask_email(addr: str) -> str:
    name, _, domain = addr.partition("@")
    return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"


# ==================== 管理员级：SMTP ====================

class SmtpBody(BaseModel):
    enabled: bool = True
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = Field("", description="留空表示沿用已保存的密码")
    from_email: str = ""
    from_name: str = "TradingAgents-CN"
    use_tls: bool = True
    use_ssl: bool = False


async def _saved_smtp(db) -> Optional[Dict[str, Any]]:
    return await db[SMTP_CONFIG_COLLECTION].find_one({"_id": "default"})


@router.get("/smtp")
async def get_smtp(db=Depends(get_mongo_db), user=Depends(get_current_user)):
    _require_admin(user)
    saved = await _saved_smtp(db)
    if saved:
        source, cfg = "database", saved
    elif app_settings.SMTP_USERNAME:
        source = "env"
        cfg = {
            "enabled": app_settings.SMTP_ENABLED, "host": app_settings.SMTP_HOST, "port": app_settings.SMTP_PORT,
            "username": app_settings.SMTP_USERNAME, "password": app_settings.SMTP_PASSWORD,
            "from_email": app_settings.SMTP_FROM_EMAIL, "from_name": app_settings.SMTP_FROM_NAME,
            "use_tls": app_settings.SMTP_USE_TLS, "use_ssl": app_settings.SMTP_USE_SSL,
        }
    else:
        source, cfg = "none", {}
    return {
        "success": True,
        "data": {
            "source": source,
            "enabled": bool(cfg.get("enabled", False)),
            "host": cfg.get("host", ""),
            "port": cfg.get("port", 587),
            "username": cfg.get("username", ""),
            "from_email": cfg.get("from_email", ""),
            "from_name": cfg.get("from_name", "TradingAgents-CN"),
            "use_tls": cfg.get("use_tls", True),
            "use_ssl": cfg.get("use_ssl", False),
            "password_set": bool(cfg.get("password")),
        },
    }


@router.put("/smtp", dependencies=[Depends(require_pro)])
async def save_smtp(body: SmtpBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    _require_admin(user)
    saved = await _saved_smtp(db)
    existing_password = (saved or {}).get("password") or ""
    try:
        cfg = svc.validate_smtp_input(body.model_dump(), existing_password_set=bool(existing_password))
    except svc.ValidationError as e:
        _bad(e)
    if not cfg["password"]:
        cfg["password"] = existing_password
    await get_email_service().save_smtp_config(cfg)
    logger.info("[邮件] SMTP 配置已更新 by=%s host=%s enabled=%s", _uid(user), cfg["host"], cfg["enabled"])
    return {"success": True}


@router.post("/smtp/test", dependencies=[Depends(require_pro)])
async def test_smtp(body: SmtpBody, db=Depends(get_mongo_db), user=Depends(get_current_user)):
    """用表单里的参数测试 SMTP 连接与登录（不发邮件、不保存）；密码留空则用已保存的。"""
    _require_admin(user)
    saved = await _saved_smtp(db)
    existing_password = (saved or {}).get("password") or (app_settings.SMTP_PASSWORD if not saved else "")
    try:
        cfg = svc.validate_smtp_input({**body.model_dump(), "enabled": True},
                                      existing_password_set=bool(existing_password))
    except svc.ValidationError as e:
        _bad(e)
    if not cfg["password"]:
        cfg["password"] = existing_password
    result = await get_email_service().test_smtp_connection(cfg)
    return {"success": bool(result.get("success")), "message": result.get("message", "")}
