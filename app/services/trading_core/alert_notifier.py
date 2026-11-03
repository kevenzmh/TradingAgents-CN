"""交易告警邮件推送（第二阶段遗留补齐）。

补上告警链路的最后一环：

    trading_alerts 集合 ✅
      → 巡检打日志 ✅
      → 邮件通知运维   ← 本模块

设计要点：
- **复用现有 SMTP 通道**（EmailService.get_smtp_config + _send_smtp），
  不重复造轮子，也不引入新依赖
- **批量合并**：一轮巡检的多条告警合并为一封邮件，避免一次故障刷爆邮箱
- **幂等止血**：同一批告警在 alert['emailed_at'] 上打标，配合 patrol 的
  last_patrolled_at 双保险，30 分钟内不重复发送
- **失败不阻断**：SMTP 异常只记日志，绝不影响巡检与交易主流程
- **未配置即静默**：没有 TRADING_ALERT_EMAIL_ENABLED 或没有收件人时直接跳过，
  不产生错误噪音
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 告警类型的可读名称（邮件正文用）
_ALERT_LABELS = {
    "order_status_unknown": "委托状态未知（柜台可能已受理）",
    "order_reconcile_missing": "柜台查不到该委托（对账失联）",
    "settlement_incomplete": "结算中断（资金/持仓可能不一致）",
}
_HUMANIZE_PLACEHOLDER = "未分类告警"


def _severity_rank(sev: str) -> int:
    return {"critical": 2, "warning": 1}.get(str(sev or "").lower(), 0)


def _humanize_alert_type(alert_type: str) -> str:
    return _ALERT_LABELS.get(str(alert_type or ""), _HUMANIZE_PLACEHOLDER)


class TradingAlertNotifier:
    """交易告警邮件通知器。"""

    def __init__(self, db: Any):
        self._db = db

    def _recipients(self) -> List[str]:
        """解析收件人列表（逗号分隔，去空去重）。"""
        from app.core.config import get_settings

        settings = get_settings()
        raw = str(getattr(settings, "TRADING_ALERT_EMAIL_TO", "") or "")
        seen: List[str] = []
        for part in raw.replace(";", ",").split(","):
            addr = part.strip()
            if addr and addr not in seen:
                seen.append(addr)
        return seen

    def _enabled(self) -> bool:
        from app.core.config import get_settings

        settings = get_settings()
        return bool(getattr(settings, "TRADING_ALERT_EMAIL_ENABLED", False))

    def _min_severity(self) -> str:
        from app.core.config import get_settings

        settings = get_settings()
        return str(
            getattr(settings, "TRADING_ALERT_EMAIL_MIN_SEVERITY", "critical") or "critical"
        ).lower()

    def _should_send(self, alerts: List[Dict[str, Any]]) -> bool:
        """判断是否值得发送（未启用/无收件人/无达标告警 → 不发）。"""
        if not self._enabled():
            return False
        if not self._recipients():
            logger.debug("[告警邮件] 未配置收件人，跳过")
            return False
        threshold = _severity_rank(self._min_severity())
        return any(_severity_rank(a.get("severity")) >= threshold for a in alerts)

    @staticmethod
    def _build_body(alerts: List[Dict[str, Any]]) -> tuple:
        """构造邮件正文（返回 html 与纯文本）。"""
        rows_html = []
        lines_txt = []
        for a in alerts:
            label = _humanize_alert_type(a.get("alert_type"))
            order_id = a.get("order_id") or a.get("settlement_id") or "-"
            broker = a.get("broker_order_id") or "-"
            symbol = a.get("symbol") or "-"
            side = a.get("side") or "-"
            qty = a.get("quantity")
            reason = a.get("reason") or "-"
            created = a.get("created_at") or "-"

            rows_html.append(
                "<tr>"
                f"<td>{label}</td>"
                f"<td>{symbol}</td>"
                f"<td>{side}</td>"
                f"<td>{qty if qty is not None else '-'}</td>"
                f"<td>{broker}</td>"
                f"<td>{reason}</td>"
                f"<td>{created}</td>"
                "</tr>"
            )
            lines_txt.append(
                f"- [{label}] 标的={symbol} 方向={side} 数量={qty if qty is not None else '-'} "
                f"受理号={broker} 原因={reason} 时间={created}"
            )

        html = (
            "<div style='font-family:system-ui,-apple-system,sans-serif'>"
            "<h3 style='color:#c0392b;margin:0 0 8px'>⚠️ 交易告警需人工处理</h3>"
            f"<p style='color:#666;margin:0 0 12px'>共 {len(alerts)} 条 critical 级别的交易告警，"
            "请在处理完毕后于告警管理中确认关闭。</p>"
            "<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse;font-size:13px'>"
            "<thead style='background:#f5f5f5'><tr>"
            "<th>类型</th><th>标的</th><th>方向</th><th>数量</th>"
            "<th>受理号</th><th>原因</th><th>时间</th>"
            "</tr></thead><tbody>"
            + "".join(rows_html)
            + "</tbody></table>"
            "<p style='color:#999;font-size:12px;margin-top:12px'>"
            "此邮件由 TradingAgents-CN 交易告警系统自动发送，请勿直接回复。"
            "</p></div>"
        )
        text = (
            f"交易告警需人工处理（共 {len(alerts)} 条）\n\n"
            + "\n".join(lines_txt)
            + "\n\n请处理后在告警管理中确认关闭。"
        )
        return html, text

    async def send(self, alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """发送告警邮件（一次调用合并所有告警）。

        返回 {"sent": bool, "reason": str, "count": int}。
        """
        if not alerts:
            return {"sent": False, "reason": "no_alerts", "count": 0}
        if not self._should_send(alerts):
            reason = "disabled" if not self._enabled() else (
                "no_recipients" if not self._recipients() else "below_threshold"
            )
            return {"sent": False, "reason": reason, "count": 0}

        try:
            from app.services.email_service import get_email_service

            service = get_email_service()
            smtp_config = await service.get_smtp_config()
            if not smtp_config or not smtp_config.get("enabled"):
                logger.warning("[告警邮件] SMTP 未配置或未启用，无法发送")
                return {"sent": False, "reason": "smtp_not_configured", "count": 0}

            recipients = self._recipients()
            html, text = self._build_body(alerts)
            subject = (
                f"【交易告警】{len(alerts)} 条 critical 告警需处理 "
                f"({datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')})"
            )

            sent_to: List[str] = []
            for addr in recipients:
                ok = await service._send_smtp(
                    to_email=addr,
                    subject=subject,
                    html_content=html,
                    text_content=text,
                    smtp_config=smtp_config,
                )
                if ok:
                    sent_to.append(addr)
                else:
                    logger.warning("[告警邮件] 发送失败: %s", addr)

            if not sent_to:
                return {"sent": False, "reason": "smtp_send_failed", "count": len(alerts)}

            # 打标，配合 patrol 的 last_patrolled_at 做幂等
            now_iso = datetime.now(timezone.utc).isoformat()
            ids = [a.get("_id") for a in alerts if a.get("_id") is not None]
            if ids:
                try:
                    await self._db["trading_alerts"].update_many(
                        {"_id": {"$in": ids}},
                        {"$set": {"emailed_at": now_iso, "emailed_to": sent_to}},
                    )
                except Exception as exc:
                    logger.warning("[告警邮件] 打标失败（不影响已发出的邮件）: %s", exc)

            # 指标：便于在仪表盘上看「告警推了多少封」
            try:
                from app.services.trading_core.observability import metrics
                metrics.inc("trading_alert_email_total",
                            {"result": "sent"}, len(alerts))
            except Exception:
                pass

            logger.warning(
                "[告警邮件] 已发送 %d 条 critical 告警至 %s",
                len(alerts), ", ".join(sent_to),
            )
            return {"sent": True, "reason": "ok", "count": len(alerts),
                    "recipients": sent_to}
        except Exception as exc:
            logger.error("[告警邮件] 发送异常: %s", exc, exc_info=True)
            return {"sent": False, "reason": f"exception: {exc}", "count": 0}


async def send_trading_alerts_email(
    db: Any, alerts: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """便捷入口：可显式传 alerts，或由巡检模块传入本轮命中的告警。"""
    return await TradingAlertNotifier(db).send(alerts or [])
