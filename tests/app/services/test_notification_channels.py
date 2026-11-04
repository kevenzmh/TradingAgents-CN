"""渠道推送 / 邮件设置的校验、脱敏与 Webhook 响应判定单测。"""

import httpx
import pytest

from app.models.channel_config import ChannelConfig, ChannelType
from app.pro.services import notification_channel_service as svc
from app.services.channel_senders import webhook_sender as ws

FEISHU = "https://open.feishu.cn/open-apis/bot/v2/hook/abcdef12-3456-7890-abcd-ef1234567890"
DINGTALK = "https://oapi.dingtalk.com/robot/send?access_token=0123456789abcdef0123456789abcdef"
WECOM = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=aaaa-bbbb-cccc-dddd"


class TestWebhookUrl:
    @pytest.mark.parametrize("t,url", [
        (ChannelType.FEISHU, FEISHU),
        (ChannelType.FEISHU, FEISHU.replace("open.feishu.cn", "open.larksuite.com")),
        (ChannelType.DINGTALK, DINGTALK),
        (ChannelType.WECHATWORK, WECOM),
    ])
    def test_valid(self, t, url):
        assert svc.validate_webhook_url(t, f"  {url} ") == url

    @pytest.mark.parametrize("t,url,msg", [
        (ChannelType.FEISHU, "http://open.feishu.cn/open-apis/bot/v2/hook/x", "https"),
        (ChannelType.FEISHU, "https://127.0.0.1/open-apis/bot/v2/hook/x", "域名"),
        (ChannelType.FEISHU, "https://169.254.169.254/open-apis/bot/v2/hook/x", "域名"),
        (ChannelType.FEISHU, "https://open.feishu.cn.evil.com/open-apis/bot/v2/hook/x", "域名"),
        (ChannelType.FEISHU, "https://evil.com@open.feishu.cn/open-apis/bot/v2/hook/x", "用户名"),
        (ChannelType.FEISHU, "https://open.feishu.cn:8443/open-apis/bot/v2/hook/x", "端口"),
        (ChannelType.FEISHU, "https://open.feishu.cn/open-apis/bot/v2/hook/", "路径"),
        (ChannelType.FEISHU, "https://open.feishu.cn/other", "路径"),
        (ChannelType.DINGTALK, "https://oapi.dingtalk.com/robot/send", "access_token"),
        (ChannelType.DINGTALK, "https://oapi.dingtalk.com/robot/send?access_token=", "access_token"),
        (ChannelType.WECHATWORK, "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?x=1", "key"),
        (ChannelType.FEISHU, "", "填写"),
        (ChannelType.FEISHU, None, "填写"),
        (ChannelType.FEISHU, "https://open.feishu.cn/open-apis/bot/v2/hook/" + "a" * 600, "过长"),
        (ChannelType.EMAIL, FEISHU, "不使用"),
    ])
    def test_rejected(self, t, url, msg):
        with pytest.raises(svc.ValidationError, match=msg):
            svc.validate_webhook_url(t, url)


class TestMasking:
    def test_mask_secret(self):
        assert svc.mask_secret("0123456789abcdef") == "0123******cdef"
        assert svc.mask_secret("abc") == "***"

    @pytest.mark.parametrize("url,secret", [
        (FEISHU, "abcdef12-3456-7890-abcd-ef1234567890"),
        (DINGTALK, "0123456789abcdef0123456789abcdef"),
        (WECOM, "aaaa-bbbb-cccc-dddd"),
    ])
    def test_secret_never_leaks(self, url, secret):
        masked = svc.mask_webhook_url(url)
        assert secret not in masked
        assert masked.startswith("https://")

    def test_none(self):
        assert svc.mask_webhook_url(None) is None

    def test_serialize_has_no_raw_url(self):
        ch = ChannelConfig(channel_id="1", type=ChannelType.DINGTALK, name="n", webhook_url=DINGTALK,
                           events=["alert"])
        out = svc.serialize_channel(ch)
        assert "webhook_url" not in out and out["has_webhook"] is True
        assert "0123456789abcdef0123456789abcdef" not in str(out)


class TestEventsAndEmail:
    def test_events_dedup_ordered(self):
        assert svc.validate_events(["alert", "analysis_complete", "alert"]) == ["analysis_complete", "alert"]

    @pytest.mark.parametrize("bad", [[], None, "alert", ["bogus"], ["alert", "bogus"]])
    def test_events_rejected(self, bad):
        with pytest.raises(svc.ValidationError):
            svc.validate_events(bad)

    @pytest.mark.parametrize("ok", ["a@b.com", "first.last+tag@mail.example.co", " x@y.cn "])
    def test_email_ok(self, ok):
        assert svc.validate_email_address(ok) == ok.strip()

    @pytest.mark.parametrize("bad", ["a@b", "@b.com", "a b@c.com", "a@@b.com", "x" * 260 + "@a.com"])
    def test_email_bad(self, bad):
        with pytest.raises(svc.ValidationError):
            svc.validate_email_address(bad)

    def test_email_optional(self):
        assert svc.validate_email_address("", required=False) is None
        with pytest.raises(svc.ValidationError):
            svc.validate_email_address("")

    def test_quiet_hours(self):
        svc.validate_quiet_hours("22:00", "08:00")
        for s, e in (("25:00", "08:00"), ("22:00", "8:00"), ("22:00", "22:00"), (None, "08:00")):
            with pytest.raises(svc.ValidationError):
                svc.validate_quiet_hours(s, e)


class TestSmtpInput:
    BASE = dict(enabled=True, host="smtp.qq.com", port=465, username="u@qq.com", password="pw",
                from_email="u@qq.com", use_tls=False, use_ssl=True)

    def test_ok(self):
        out = svc.validate_smtp_input(dict(self.BASE), existing_password_set=False)
        assert out["host"] == "smtp.qq.com" and out["use_ssl"] is True

    def test_blank_password_allowed_only_when_already_saved(self):
        data = {**self.BASE, "password": ""}
        svc.validate_smtp_input(data, existing_password_set=True)
        with pytest.raises(svc.ValidationError, match="密码"):
            svc.validate_smtp_input(data, existing_password_set=False)

    @pytest.mark.parametrize("patch,msg", [
        ({"host": ""}, "服务器"), ({"username": ""}, "用户名"), ({"port": 0}, "端口"), ({"port": 70000}, "端口"),
        ({"port": True}, "端口"), ({"use_tls": True, "use_ssl": True}, "同时"), ({"from_email": "bad"}, "邮箱"),
    ])
    def test_rejected(self, patch, msg):
        with pytest.raises(svc.ValidationError, match=msg):
            svc.validate_smtp_input({**self.BASE, **patch}, existing_password_set=False)

    def test_disabled_skips_required_fields(self):
        out = svc.validate_smtp_input({"enabled": False}, existing_password_set=False)
        assert out["enabled"] is False


# ---------- Webhook 业务失败判定（HTTP 200 + 平台错误码） ----------

class TestResponseCheck:
    @pytest.mark.parametrize("t,body", [
        (ChannelType.FEISHU, {"code": 0, "msg": "success"}),
        (ChannelType.FEISHU, {"StatusCode": 0, "StatusMessage": "success"}),
        (ChannelType.DINGTALK, {"errcode": 0, "errmsg": "ok"}),
        (ChannelType.WECHATWORK, {"errcode": 0, "errmsg": "ok"}),
    ])
    def test_success(self, t, body):
        assert ws.check_webhook_response(t, 200, body) is None

    @pytest.mark.parametrize("t,body,fragment", [
        (ChannelType.FEISHU, {"code": 19024, "msg": "Key Words Not Found"}, "Key Words"),
        (ChannelType.FEISHU, {"StatusCode": 9499, "StatusMessage": "bad"}, "9499"),
        (ChannelType.DINGTALK, {"errcode": 310000, "errmsg": "keywords not in content"}, "310000"),
        (ChannelType.WECHATWORK, {"errcode": 93000, "errmsg": "invalid webhook url"}, "93000"),
        (ChannelType.DINGTALK, "<html>", "JSON"),
        (ChannelType.DINGTALK, None, "JSON"),
    ])
    def test_failure(self, t, body, fragment):
        assert fragment in ws.check_webhook_response(t, 200, body)


def _channel(t=ChannelType.DINGTALK, url=DINGTALK):
    return ChannelConfig(channel_id="1", type=t, name="测试", webhook_url=url, events=["alert"])


_REAL_CLIENT = httpx.AsyncClient  # 模块加载时保存，避免多次打桩时套娃


def _patch_client(monkeypatch, handler):
    def factory(*a, **kw):
        return _REAL_CLIENT(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(ws.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_http_200_with_platform_error_is_failure(monkeypatch):
    _patch_client(monkeypatch, lambda req: httpx.Response(200, json={"errcode": 310000, "errmsg": "keywords not in content"}))
    ok, msg = await ws.send_webhook_checked(_channel(), "t", "c")
    assert ok is False and "310000" in msg
    assert await ws.send_webhook(_channel(), "t", "c") is False


@pytest.mark.asyncio
async def test_success_and_payload_shape(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    _patch_client(monkeypatch, handler)
    ok, msg = await ws.send_webhook_checked(_channel(ChannelType.FEISHU, FEISHU), "标题", "内容")
    assert ok and msg == "发送成功"
    import json
    payload = json.loads(seen["body"])
    assert payload["msg_type"] == "text" and "标题" in payload["content"]["text"]


@pytest.mark.asyncio
async def test_http_error_and_timeout_messages_do_not_leak_token(monkeypatch):
    _patch_client(monkeypatch, lambda req: httpx.Response(403, text="forbidden"))
    ok, msg = await ws.send_webhook_checked(_channel(), "t", "c")
    assert not ok and msg == "HTTP 403"

    def boom(req):
        raise httpx.ConnectTimeout("timed out", request=req)

    _patch_client(monkeypatch, boom)
    ok, msg = await ws.send_webhook_checked(_channel(), "t", "c")
    assert not ok and "超时" in msg
    assert "0123456789abcdef" not in msg


@pytest.mark.asyncio
async def test_redirects_are_not_followed(monkeypatch):
    """重定向到内网地址是 SSRF 的常见绕过；不跟随，直接按 3xx 失败处理。"""
    _patch_client(monkeypatch, lambda req: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}))
    ok, msg = await ws.send_webhook_checked(_channel(), "t", "c")
    assert not ok


@pytest.mark.asyncio
async def test_missing_url():
    ok, msg = await ws.send_webhook_checked(_channel(url=None), "t", "c")
    assert not ok and "未配置" in msg
