"""
Webhook 推送发送器

支持飞书、钉钉、企业微信三种 Webhook 机器人格式，
协议一致，只有 JSON body 格式略有差异。
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from app.models.channel_config import ChannelConfig, ChannelType

logger = logging.getLogger("webapi.channel_senders.webhook")

# HTTP 请求超时（秒）
WEBHOOK_TIMEOUT = 10


def _build_feishu_payload(title: str, content: str) -> Dict[str, Any]:
    """
    飞书自定义机器人消息格式
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    return {
        "msg_type": "text",
        "content": {
            "text": f"【{title}】\n{content}"
        }
    }


def _build_dingtalk_payload(title: str, content: str) -> Dict[str, Any]:
    """
    钉钉自定义机器人消息格式
    https://open.dingtalk.com/document/robots/custom-robot-access
    """
    return {
        "msgtype": "text",
        "text": {
            "content": f"【{title}】\n{content}"
        }
    }


def _build_wechatwork_payload(title: str, content: str) -> Dict[str, Any]:
    """
    企业微信自定义机器人消息格式
    https://developer.work.weixin.qq.com/document/path/91770
    """
    return {
        "msgtype": "text",
        "text": {
            "content": f"【{title}】\n{content}"
        }
    }


_BUILDERS = {
    ChannelType.FEISHU: _build_feishu_payload,
    ChannelType.DINGTALK: _build_dingtalk_payload,
    ChannelType.WECHATWORK: _build_wechatwork_payload,
}


def check_webhook_response(channel_type: ChannelType, status_code: int, body: Any) -> Optional[str]:
    """校验平台响应，返回 None 表示成功，否则返回失败原因。

    飞书/钉钉/企微在业务失败（关键词不匹配、签名错误、限流、token 失效）时
    仍返回 HTTP 200，真正的结果在 JSON 里：飞书 code（旧版 StatusCode）、钉钉与企微 errcode。
    只看 HTTP 状态会把这些失败误报为成功，用户永远收不到通知却看不到任何错误。
    """
    if not isinstance(body, dict):
        return f"响应不是预期的 JSON 对象（HTTP {status_code}）"
    if channel_type == ChannelType.FEISHU:
        code = body.get("code", body.get("StatusCode", 0))
        msg = body.get("msg") or body.get("StatusMessage") or ""
    else:
        code = body.get("errcode", 0)
        msg = body.get("errmsg") or ""
    if code in (0, "0", None):
        return None
    return f"平台返回错误 {code}: {msg}".strip()


async def send_webhook_checked(
    channel: ChannelConfig,
    title: str,
    content: str,
) -> Tuple[bool, str]:
    """发送 Webhook 通知，返回 (是否成功, 说明)。说明用于“测试推送”回显真实原因。"""
    if not channel.webhook_url:
        logger.warning(f"渠道 [{channel.name}] 未配置 Webhook URL，跳过")
        return False, "未配置 Webhook 地址"

    builder = _BUILDERS.get(channel.type)
    if not builder:
        logger.warning(f"未知渠道类型: {channel.type}，跳过")
        return False, f"不支持的渠道类型: {channel.type}"

    payload = builder(title, content)

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(
                channel.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError:
                body = None
            problem = check_webhook_response(channel.type, resp.status_code, body)
            if problem:
                logger.warning(f"⚠️ Webhook 业务失败: [{channel.name}({channel.type})] {problem}")
                return False, problem

            logger.info(
                f"✅ Webhook 推送成功: [{channel.name}({channel.type})] "
                f"status={resp.status_code}"
            )
            return True, "发送成功"

    except httpx.TimeoutException:
        logger.warning(f"⚠️ Webhook 超时: [{channel.name}]")
        return False, f"请求超时（{WEBHOOK_TIMEOUT} 秒）"
    except httpx.HTTPStatusError as e:
        logger.warning(
            f"⚠️ Webhook HTTP 错误: [{channel.name}] status={e.response.status_code} "
            f"body={e.response.text[:200]}"
        )
        return False, f"HTTP {e.response.status_code}"
    except Exception as e:
        # 不记录 URL：其中含机器人 token
        logger.error(f"❌ Webhook 推送失败: [{channel.name}] {type(e).__name__}")
        return False, f"{type(e).__name__}"


async def send_webhook(
    channel: ChannelConfig,
    title: str,
    content: str,
) -> bool:
    """发送 Webhook 通知（True 成功 / False 失败）"""
    ok, _ = await send_webhook_checked(channel, title, content)
    return ok
