"""渠道推送与邮件设置的校验 / 脱敏工具（Pro）

供 app/pro/routers/channel_configs.py 与 email_settings.py 使用，纯函数、无 IO，便于单测。

安全要点：
- Webhook 地址由用户填写，服务端会向其发起 POST：必须限定为三家平台的官方域名 + https，
  否则等价于让任何登录用户用服务器身份请求内网地址（SSRF）
- Webhook 地址里带机器人 token、SMTP 密码都是凭据：接口一律不回传明文，只回传脱敏值 / 是否已设置
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from app.models.channel_config import EVENT_TYPES, ChannelType

MAX_CHANNELS_PER_USER = 20

# 渠道类型 → (允许的主机, 允许的路径前缀, 必须出现的查询参数)
_WEBHOOK_RULES: Dict[ChannelType, Tuple[Tuple[str, ...], str, Optional[str]]] = {
    ChannelType.FEISHU: (("open.feishu.cn", "open.larksuite.com"), "/open-apis/bot/v2/hook/", None),
    ChannelType.DINGTALK: (("oapi.dingtalk.com",), "/robot/send", "access_token"),
    ChannelType.WECHATWORK: (("qyapi.weixin.qq.com",), "/cgi-bin/webhook/send", "key"),
}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ==================== 渠道 ====================

def validate_webhook_url(channel_type: ChannelType, url: Any) -> str:
    """校验并返回规范化的 Webhook 地址；不合法抛 ValidationError。"""
    rule = _WEBHOOK_RULES.get(channel_type)
    if rule is None:
        raise ValidationError(f"{channel_type.value} 渠道不使用 Webhook 地址")
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("请填写 Webhook 地址")
    url = url.strip()
    if len(url) > 512:
        raise ValidationError("Webhook 地址过长")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValidationError("Webhook 地址格式不正确")
    hosts, path_prefix, required_param = rule
    if parts.scheme != "https":
        raise ValidationError("Webhook 地址必须使用 https")
    if parts.username or parts.password:
        raise ValidationError("Webhook 地址不允许包含用户名或密码")
    if (parts.hostname or "").lower() not in hosts:
        raise ValidationError(f"Webhook 地址域名必须是：{'、'.join(hosts)}")
    if port not in (None, 443):
        raise ValidationError("Webhook 地址不允许自定义端口")
    if not parts.path.startswith(path_prefix) or (path_prefix.endswith("/") and parts.path == path_prefix):
        raise ValidationError(f"Webhook 地址路径应以 {path_prefix} 开头，请从机器人设置页复制完整地址")
    if required_param and not (parse_qs(parts.query).get(required_param) or [""])[0]:
        raise ValidationError(f"Webhook 地址缺少 {required_param} 参数，请复制完整地址")
    return url


def mask_secret(value: str, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def mask_webhook_url(url: Optional[str]) -> Optional[str]:
    """脱敏：保留主机与路径形状，把 token / key / hook id 打码。"""
    if not url:
        return None
    parts = urlsplit(url)
    path = parts.path
    if "/hook/" in path:  # 飞书：/open-apis/bot/v2/hook/<id>
        head, _, token = path.rpartition("/")
        path = f"{head}/{mask_secret(token)}"
    query = ""
    if parts.query:
        masked = []
        for k, vs in parse_qs(parts.query, keep_blank_values=True).items():
            masked.append(f"{k}={mask_secret(vs[0]) if k in ('access_token', 'key', 'secret', 'sign') else vs[0]}")
        query = "?" + "&".join(masked)
    return f"{parts.scheme}://{parts.netloc}{path}{query}"


def validate_events(events: Any) -> List[str]:
    if not isinstance(events, list) or not events:
        raise ValidationError("请至少选择一个订阅事件")
    unknown = [e for e in events if e not in EVENT_TYPES]
    if unknown:
        raise ValidationError(f"不支持的事件类型：{'、'.join(map(str, unknown))}")
    return [e for e in EVENT_TYPES if e in events]  # 去重并保持规范顺序


def validate_email_address(value: Any, *, required: bool = True) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError("请填写邮箱地址")
        return None
    if not isinstance(value, str) or len(value) > 254 or not _EMAIL_RE.match(value.strip()):
        raise ValidationError("邮箱地址格式不正确")
    return value.strip()


def serialize_channel(ch: Any) -> Dict[str, Any]:
    """渠道配置 → 对外字段（不含明文 Webhook 地址）"""
    return {
        "channel_id": ch.channel_id,
        "type": ch.type.value if hasattr(ch.type, "value") else ch.type,
        "name": ch.name,
        "enabled": ch.enabled,
        "events": list(ch.events),
        "has_webhook": bool(ch.webhook_url),
        "webhook_url_masked": mask_webhook_url(ch.webhook_url),
        "email_address": ch.email_address,
        "created_at": ch.created_at,
        "updated_at": ch.updated_at,
    }


# ==================== 邮件设置 ====================

def validate_quiet_hours(start: Any, end: Any) -> None:
    for label, v in (("开始", start), ("结束", end)):
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            raise ValidationError(f"免打扰{label}时间必须是 HH:MM 格式")
    if start == end:
        raise ValidationError("免打扰开始与结束时间不能相同")


def validate_smtp_input(data: Dict[str, Any], *, existing_password_set: bool) -> Dict[str, Any]:
    """校验并规整 SMTP 配置输入；密码为空表示沿用已保存的密码。"""
    enabled = bool(data.get("enabled", True))
    host = (data.get("host") or "").strip()
    username = (data.get("username") or "").strip()
    from_email = (data.get("from_email") or "").strip()
    port = data.get("port", 587)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise ValidationError("端口必须是 1-65535 的整数")
    use_tls = bool(data.get("use_tls", True))
    use_ssl = bool(data.get("use_ssl", False))
    if use_tls and use_ssl:
        raise ValidationError("STARTTLS 与 SSL 不能同时启用（465 端口用 SSL，587 端口用 STARTTLS）")
    password = data.get("password") or ""
    if enabled:
        if not host:
            raise ValidationError("请填写 SMTP 服务器地址")
        if not username:
            raise ValidationError("请填写 SMTP 用户名")
        if not password and not existing_password_set:
            raise ValidationError("请填写 SMTP 密码")
        validate_email_address(from_email)
    elif from_email:
        validate_email_address(from_email)
    from_name = (data.get("from_name") or "TradingAgents-CN").strip()[:50]
    return {
        "enabled": enabled, "host": host, "port": port, "username": username,
        "password": password, "from_email": from_email, "from_name": from_name,
        "use_tls": use_tls, "use_ssl": use_ssl,
    }
