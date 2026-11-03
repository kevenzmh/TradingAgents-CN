"""交易可观测性基础设施（第二阶段）。

解决三个具体缺口：

1. **trace_id 断链**：`app/middleware/request_id.py` 只为 HTTP 请求生成 trace_id。
   交易订单在后台线程 / APScheduler 定时任务里执行，contextvars 中没有值，
   日志里 trace_id 恒为 "-"，无法把「一笔订单的完整生命周期」串起来。
   本模块提供 `trading_trace()`，为任何执行入口绑定 trace_id + 订单上下文字段。

2. **零指标埋点**：无法回答「今天下了多少单、风控拒了多少、平均耗时多少」。
   本模块提供零依赖的 `MetricsRegistry`（不引入 prometheus_client），
   输出标准 Prometheus 文本格式，可直接被 Prometheus 抓取。

3. **告警无消费方**：P0 修复已把 critical 告警写入 `trading_alerts`，
   但没有人在看。本模块提供 `AlertService`：查询/确认/统计，
   以及一个定时把未处理 critical 告警打到日志（可选 webhook）的巡检任务。

设计原则：
- 不引入第三方依赖（prometheus_client 未安装，且多一个依赖就多一个故障点）
- 指标必须带标签但**不包含用户隐私**（user_id 不作为 label，避免高基数与泄露）
- 所有采集失败静默降级，绝不影响交易主流程
"""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logging_context import trace_id_var

logger = logging.getLogger(__name__)

ALERTS_COLLECTION = "trading_alerts"


# ============================================================================
# 1. 日志上下文：让后台交易链路也带 trace_id 与订单字段
# ============================================================================

# 结构化日志字段（不进入 LogRecord 的固定属性，而是放在 extra 里供 JSON 格式器取用）
log_fields_var = None
try:  # 兼容两种导入路径，避免循环依赖
    import contextvars

    log_fields_var = contextvars.ContextVar("log_fields", default=None)
except Exception:  # pragma: no cover - contextvars 恒可用，此处仅防御
    pass


def _order_trace_id(order: Dict[str, Any]) -> str:
    """为一笔订单派生稳定的 trace_id（同一订单跨线程/跨任务保持一致）。"""
    oid = order.get("_id")
    if oid is not None:
        return f"order-{oid}"
    signal_id = str(order.get("signal_id") or "").strip()
    if signal_id:
        return f"signal-{signal_id}"
    return f"order-{int(time.time() * 1000)}"


@contextmanager
def trading_trace(
    trace_id: Optional[str] = None,
    *,
    order: Optional[Dict[str, Any]] = None,
    **fields: Any,
):
    """为交易执行绑定 trace_id 与结构化字段（可嵌套，退出时恢复原值）。

    用法::

        with trading_trace(order=order):
            await engine._submit(order, gate)

    效果：
    - 该作用域内所有日志自动带 trace_id（经由 LoggingContextFilter）
    - 可选的 order / 自定义字段进入 log_fields_var，供需要时读取
    """
    if trace_id is None and order is not None:
        trace_id = _order_trace_id(order)

    token = trace_id_var.set(trace_id or f"trace-{int(time.time() * 1000)}")
    fields_token = None
    merged: Dict[str, Any] = dict(fields)
    if order is not None:
        merged.setdefault("order_id", str(order.get("_id", "")))
        merged.setdefault("symbol", order.get("symbol"))
        merged.setdefault("side", order.get("side"))
        merged.setdefault("market", order.get("market"))
    if log_fields_var is not None and merged:
        fields_token = log_fields_var.set(merged)
    try:
        yield trace_id
    finally:
        try:
            trace_id_var.reset(token)
        except Exception:
            trace_id_var.set("-")
        if fields_token is not None:
            try:
                log_fields_var.reset(fields_token)
            except Exception:
                pass


def current_log_fields() -> Dict[str, Any]:
    """读取当前作用域的结构化字段（无则空 dict）。"""
    if log_fields_var is None:
        return {}
    value = log_fields_var.get()
    return dict(value) if isinstance(value, dict) else {}


# ============================================================================
# 2. 指标注册表（零依赖，Prometheus 文本格式）
# ============================================================================

_LABEL_SEP = "\x1f"


class MetricsRegistry:
    """线程安全的最小指标注册表。

    支持两种指标：
    - Counter：只增计数（订单数、拒绝数、错误数）
    - Histogram：固定桶的耗时分布（p50/p95/p99 可由桶估算）

    刻意不实现 Gauge/Summary：交易场景用不上，少一层复杂度。
    """

    DEFAULT_BUCKETS: Tuple[float, ...] = (
        0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # name -> {"help": str, "type": str, "values": {labels_tuple: float}, "buckets": tuple,
        #          "bucket_counts": {labels_tuple: [counts]}, "sum": {labels: float}, "count": {labels: int}}
        self._metrics: Dict[str, Dict[str, Any]] = {}

    # ---- 定义 ----

    def declare_counter(self, name: str, help_text: str = "") -> None:
        with self._lock:
            entry = self._metrics.setdefault(name, {
                "type": "counter", "help": help_text, "values": {},
            })
            entry["type"] = "counter"
            if help_text:
                entry["help"] = help_text

    def declare_histogram(
        self, name: str, help_text: str = "",
        buckets: Optional[Iterable[float]] = None,
    ) -> None:
        with self._lock:
            entry = self._metrics.setdefault(name, {
                "type": "histogram", "help": help_text,
                "bucket_counts": {}, "sum": {}, "count": {},
            })
            entry["type"] = "histogram"
            entry["buckets"] = tuple(sorted(buckets or self.DEFAULT_BUCKETS))
            if help_text:
                entry["help"] = help_text

    # ---- 写入 ----

    @staticmethod
    def _key(labels: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def inc(self, name: str, labels: Optional[Dict[str, Any]] = None, value: float = 1.0) -> None:
        key = self._key(labels)
        try:
            with self._lock:
                entry = self._metrics.setdefault(name, {
                    "type": "counter", "help": "", "values": {},
                })
                entry["type"] = "counter"
                entry["values"][key] = float(entry["values"].get(key, 0.0)) + value
        except Exception:  # pragma: no cover - 埋点绝不能影响主流程
            pass

    def observe(self, name: str, seconds: float, labels: Optional[Dict[str, Any]] = None) -> None:
        key = self._key(labels)
        try:
            with self._lock:
                entry = self._metrics.setdefault(name, {
                    "type": "histogram", "help": "",
                    "bucket_counts": {}, "sum": {}, "count": {},
                    "buckets": self.DEFAULT_BUCKETS,
                })
                buckets = entry.get("buckets") or self.DEFAULT_BUCKETS
                counts = entry["bucket_counts"].get(key)
                if counts is None:
                    counts = [0] * (len(buckets) + 1)  # 最后一个是 +Inf
                    entry["bucket_counts"][key] = counts
                for i, edge in enumerate(buckets):
                    if seconds <= edge:
                        counts[i] += 1
                counts[-1] += 1  # +Inf 桶恒累加
                entry["sum"][key] = float(entry["sum"].get(key, 0.0)) + float(seconds)
                entry["count"][key] = int(entry["count"].get(key, 0)) + 1
        except Exception:  # pragma: no cover
            pass

    # ---- 导出 ----

    @staticmethod
    def _fmt_labels(key: Tuple[Tuple[str, str], ...], extra: Optional[Tuple[str, str]] = None) -> str:
        pairs = list(key)
        if extra:
            pairs.append(extra)
        if not pairs:
            return ""
        body = ",".join(f'{k}="{v}"' for k, v in pairs)
        return "{" + body + "}"

    def render(self) -> str:
        """输出 Prometheus 文本格式（# HELP / # TYPE / 样本行）。"""
        lines: List[str] = []
        with self._lock:
            for name, entry in sorted(self._metrics.items()):
                help_text = str(entry.get("help") or name).replace("\\", "\\\\").replace("\n", " ")
                if entry.get("type") == "histogram":
                    lines.append(f"# HELP {name} {help_text}")
                    lines.append(f"# TYPE {name} histogram")
                    buckets = entry.get("buckets") or self.DEFAULT_BUCKETS
                    for key, counts in sorted(entry.get("bucket_counts", {}).items()):
                        cumulative = 0
                        for i, edge in enumerate(buckets):
                            cumulative += counts[i]
                            lines.append(
                                f"{name}_bucket{self._fmt_labels(key, ('le', str(edge)))} {cumulative}"
                            )
                        lines.append(
                            f"{name}_bucket{self._fmt_labels(key, ('le', '+Inf'))} {counts[-1]}"
                        )
                        lines.append(f"{name}_sum{self._fmt_labels(key)} {entry['sum'].get(key, 0.0)}")
                        lines.append(f"{name}_count{self._fmt_labels(key)} {entry['count'].get(key, 0)}")
                else:
                    lines.append(f"# HELP {name} {help_text}")
                    lines.append(f"# TYPE {name} counter")
                    for key, value in sorted(entry.get("values", {}).items()):
                        lines.append(f"{name}{self._fmt_labels(key)} {value}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> Dict[str, Any]:
        """结构化快照（供 JSON API 使用）。"""
        with self._lock:
            out: Dict[str, Any] = {}
            for name, entry in self._metrics.items():
                if entry.get("type") == "histogram":
                    out[name] = {
                        "type": "histogram",
                        "series": [
                            {
                                "labels": dict(key),
                                "count": entry["count"].get(key, 0),
                                "sum_seconds": round(entry["sum"].get(key, 0.0), 4),
                                "avg_seconds": round(
                                    entry["sum"].get(key, 0.0) / entry["count"][key], 4
                                ) if entry["count"].get(key) else None,
                            }
                            for key in entry.get("bucket_counts", {})
                        ],
                    }
                else:
                    out[name] = {
                        "type": "counter",
                        "series": [
                            {"labels": dict(key), "value": value}
                            for key, value in entry.get("values", {}).items()
                        ],
                    }
            return out

    def reset(self) -> None:
        """清空所有指标（测试用）。"""
        with self._lock:
            self._metrics.clear()


# 全局单例
metrics = MetricsRegistry()

# ---- 指标定义（集中声明，避免拼写漂移）----
metrics.declare_counter(
    "trading_order_total",
    "交易订单总数（按市场/方向/最终状态）",
)
metrics.declare_counter(
    "trading_risk_rejected_total",
    "风控闸门拒绝次数（按闸门代码）",
)
metrics.declare_counter(
    "trading_settlement_total",
    "结算结果计数（done/failed/repaired/needs_review）",
)
metrics.declare_counter(
    "trading_reconcile_total",
    "对账结果计数（reconciled/missing/still_open/error）",
)
metrics.declare_counter(
    "trading_alert_total",
    "交易告警计数（按类型与严重级别）",
)
metrics.declare_histogram(
    "trading_order_latency_seconds",
    "订单从提交到落终态的耗时（秒）",
)


# ============================================================================
# 3. 告警服务：查询 / 确认 / 巡检
# ============================================================================

class AlertService:
    """trading_alerts 集合的读写与巡检。"""

    def __init__(self, db: Any):
        self._db = db

    async def list_alerts(
        self,
        *,
        resolved: Optional[bool] = False,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if resolved is not None:
            query["resolved"] = resolved
        if severity:
            query["severity"] = severity
        try:
            cursor = self._db[ALERTS_COLLECTION].find(query, {"_id": 0})
            cursor = cursor.sort("created_at", -1).limit(max(1, min(int(limit), 500)))
            return await cursor.to_list(length=limit)
        except Exception as exc:
            logger.warning("[告警] 查询失败: %s", exc)
            return []

    async def resolve(self, order_id: str, note: str = "") -> bool:
        """把某订单的未处理告警标记为已处理。"""
        try:
            res = await self._db[ALERTS_COLLECTION].update_one(
                {"order_id": str(order_id), "resolved": False},
                {"$set": {
                    "resolved": True,
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                    "resolve_note": note,
                }},
            )
            metrics.inc("trading_alert_total", {"result": "resolved"})
            return bool(getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0))
        except Exception as exc:
            logger.warning("[告警] 确认失败: %s", exc)
            return False

    async def stats(self) -> Dict[str, Any]:
        """告警统计（按类型/严重级别/是否未处理）。"""
        out: Dict[str, Any] = {"open": 0, "resolved": 0, "by_type": {}, "by_severity": {}}
        try:
            coll = self._db[ALERTS_COLLECTION]
            out["open"] = await coll.count_documents({"resolved": False})
            out["resolved"] = await coll.count_documents({"resolved": True})
            for doc in await coll.aggregate([
                {"$group": {"_id": "$alert_type", "n": {"$sum": 1}}},
            ]).to_list(length=50):
                out["by_type"][str(doc.get("_id") or "unknown")] = int(doc.get("n", 0))
            for doc in await coll.aggregate([
                {"$match": {"resolved": False}},
                {"$group": {"_id": "$severity", "n": {"$sum": 1}}},
            ]).to_list(length=10):
                out["by_severity"][str(doc.get("_id") or "unknown")] = int(doc.get("n", 0))
        except Exception as exc:
            logger.warning("[告警] 统计失败: %s", exc)
        return out

    async def patrol(self, *, repeat_after_minutes: int = 30) -> Dict[str, Any]:
        """巡检未处理的 critical 告警：打日志 +（可选）邮件推送。

        `repeat_after_minutes` 做止血：同一告警在窗口内不重复打印/发送，
        避免一次故障刷爆日志与邮箱。

        执行顺序刻意如此：
        1. 先逐条写日志——即使邮件通道挂了，日志这道防线仍然有效
        2. 再把本轮告警合并成一封邮件发送（批量，避免 N 封邮件）
        3. 最后统一打 last_patrolled_at 时间戳

        邮件未配置（TRADING_ALERT_EMAIL_ENABLED=false 或无收件人）时静默跳过，
        不产生错误噪音。
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=repeat_after_minutes)).isoformat()
        try:
            cursor = self._db[ALERTS_COLLECTION].find({
                "resolved": False,
                "severity": "critical",
                "$or": [{"last_patrolled_at": {"$exists": False}},
                        {"last_patrolled_at": {"$lte": cutoff}}],
            }).limit(50)
            docs = await cursor.to_list(length=50)
        except Exception as exc:
            logger.warning("[告警] 巡检查询失败: %s", exc)
            return {"patrolled": 0, "errors": [str(exc)]}

        if not docs:
            return {"patrolled": 0, "errors": [], "email": {"sent": False, "reason": "no_alerts"}}

        # 1. 逐条写日志（第一道防线，先于邮件）
        for doc in docs:
            logger.error(
                "[交易告警·需处理] type=%s order=%s broker=%s symbol=%s %s x%s reason=%s created=%s",
                doc.get("alert_type"), doc.get("order_id"), doc.get("broker_order_id"),
                doc.get("symbol"), doc.get("side"), doc.get("quantity"),
                doc.get("reason"), doc.get("created_at"),
            )
            metrics.inc("trading_alert_total", {
                "alert_type": str(doc.get("alert_type") or "unknown"),
                "severity": "critical",
            })

        # 2. 合并发送邮件（失败只记日志，绝不阻断巡检）
        email_result: Dict[str, Any] = {"sent": False, "reason": "skipped"}
        try:
            from app.services.trading_core.alert_notifier import TradingAlertNotifier

            email_result = await TradingAlertNotifier(self._db).send(docs)
        except Exception as exc:  # pragma: no cover - notifier 内部已兜底
            logger.error("[告警] 邮件推送异常（巡检继续）: %s", exc, exc_info=True)
            email_result = {"sent": False, "reason": f"exception: {exc}"}

        # 3. 打时间戳（幂等止血）
        now_iso = datetime.now(timezone.utc).isoformat()
        for doc in docs:
            try:
                await self._db[ALERTS_COLLECTION].update_one(
                    {"_id": doc.get("_id")},
                    {"$set": {"last_patrolled_at": now_iso}},
                )
            except Exception:
                pass

        logger.error("[告警] 本轮巡检发现 %d 条未处理的 critical 告警", len(docs))
        return {"patrolled": len(docs), "errors": [], "email": email_result}


def record_alert_metric(alert_type: str, severity: str = "critical") -> None:
    """记录一条告警的指标（写库方在写成功后调用）。"""
    metrics.inc("trading_alert_total", {
        "alert_type": str(alert_type or "unknown"),
        "severity": str(severity or "unknown"),
    })
