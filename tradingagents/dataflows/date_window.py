"""Point-in-time 时间窗规则（移植自上游 TauricResearch/TradingAgents v0.7.1 date_window.py，
针对本项目 A 股/中文数据源适配）。

三条硬规则，供全部"带日期的数据工具"共用：

- ``as_of`` / ``as_of_window``：模型要的日期被钳制到运行的分析日期——
  工具永远无法把晚于分析日的请求送到数据源（上游 #1220），并总是以
  YYYY-MM-DD 规范形输出（"2026-7-5" 文本比较会大于 "2026-07-24"）。
  窗口起点晚于终点、起点不可读时抛 ValueError，由调用方把错误送回
  模型让它纠正（上游 #1476/#1595）——而不是静默返回空数据。
- ``in_window``：时间戳归一到本地时区后按半开窗口 [start, end+1day)
  过滤；无时间戳的条目只在窗口抵达当下时保留（回测无法证明一条
  无日期的消息不是未来发生的，上游 #1126）。
- ``coverage_gap``：数据源只服务最近 N 条时，历史窗口的"查无"必须
  显式声明为"覆盖不到"而非"当时没有"（上游核心主题：一个数据源
  的"无数据"不得替整条链路宣布缺席）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional

# A 股市场时区：全部数据源（东财/新浪/腾讯/同花顺）时间戳均为北京本地时间，
# 无 UTC 位移问题；统一按本地时间比较即可，无需上游的 UTC 归一（那是
# Yahoo/Reddit/StockTwits 多时区混合才需要的）。保留 to_local 以对齐接口。


def get_current_date() -> str:
    """今天的日期，YYYY-MM-DD。"""
    return date.today().strftime("%Y-%m-%d")


def is_historical(run_date: Optional[str]) -> bool:
    """运行日期是否早于今天（= 历史回放，实时数据都属于"未来"）。"""
    return bool(run_date) and str(run_date) < get_current_date()


def to_local(dt: datetime) -> datetime:
    """把 datetime 规整为可比较形态：naive 视为本地时间，aware 去掉时区。"""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _parse(date_text: Optional[str]) -> Optional[datetime]:
    if date_text is None:
        return None
    if isinstance(date_text, (datetime, date)):
        return date_text if isinstance(date_text, datetime) else datetime.combine(date_text, time.min)
    text = str(date_text).strip()
    # 宽容输入：接受 "2026-01-05 15:30:00"、纯数字 20260105 等
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8 and "-" not in text:
        text = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    # ISO 形态（含分钟精度的 "2026-01-05 08:00"）优先 fromisoformat
    try:
        return datetime.fromisoformat(text[:19].replace("/", "-"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19].replace("/", "-"), fmt)
        except ValueError:
            continue
    return None


def canonical_date(date_text: Optional[str]) -> Optional[str]:
    """规范形 YYYY-MM-DD；不可解析返回 None。"""
    parsed = _parse(date_text)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def as_of(requested: Optional[str], analysis_date: str) -> Optional[str]:
    """工具实际服务的日期：模型给的日期，但绝不晚于分析日。

    模型可能省略日期或传"今天"——那会越过工具身后的所有 point-in-time
    防线（上游 #1220）。空 analysis_date（graph 运行外的直接调用）原样
    放行。不可解析的请求也回退到分析日（与上游一致：绝不猜）。
    """
    parsed = _parse(requested)
    if not analysis_date:
        return canonical_date(requested) if parsed else (requested if requested else None)
    analysis = _parse(analysis_date)
    if analysis and parsed and parsed <= analysis:
        return parsed.strftime("%Y-%m-%d")
    return analysis.strftime("%Y-%m-%d") if analysis else analysis_date


class DateWindowError(ValueError):
    """模型给的日期窗口非法（不可读/倒置），调用方应把错误送回模型纠正。"""


def as_of_window(
    start_date: Optional[str], end_date: Optional[str], analysis_date: str
) -> tuple[str, str]:
    """[start, end] 且终点被钳制到分析日。

    终点晚于分析日 → 整窗保持长度回移到分析日截止（上游 #1220 语义）。
    起点不可读、或起点晚于终点 → 抛 DateWindowError（上游 #1476/#1595：
    送回模型纠正，不静默换窗口——换窗会答非所问）。
    """
    start = _parse(start_date)
    if start is None:
        raise DateWindowError(
            f"start_date {start_date!r} 无法解析为日期，请按 YYYY-MM-DD 提供"
        )
    old_end = _parse(end_date)
    if old_end is not None and start > old_end:
        raise DateWindowError(
            f"start_date {start_date} 晚于 end_date {end_date}，请先给较早的日期"
        )
    end_text = as_of(end_date, analysis_date) or analysis_date
    end = _parse(end_text)
    if end is None:
        end = _parse(analysis_date)
    if end is None or end == old_end or start <= end:
        start_text = start.strftime("%Y-%m-%d")
        end_text_out = (end or old_end).strftime("%Y-%m-%d") if (end or old_end) else analysis_date
        return start_text, end_text_out
    # 整窗在分析日之后：保持窗长回移
    span = (old_end - start) if (old_end is not None and old_end >= start) else timedelta(days=0)
    new_start = end - span
    return new_start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def in_window(
    publish_dt: Optional[datetime],
    start_dt: datetime,
    end_dt: datetime,
) -> bool:
    """条目是否属于半开窗口 [start, end 当日结束时点]。

    本项目窗口统一用 [start 00:00, end 23:59:59.999]（见
    _build_a_share_news_window），此函数按同一语义比较；
    ``publish_dt`` None（无日期条目）只在窗口抵达当下时保留——
    回测无法证明一条无日期的消息不是未来发生的（上游 #1126）。
    """
    start_local = to_local(start_dt)
    end_local = to_local(end_dt)
    if publish_dt is not None:
        pub = to_local(publish_dt)
        return start_local <= pub <= end_local
    # 无日期：窗口终点必须触及"昨天以后"才保留
    return end_local.date() >= (datetime.now() - timedelta(days=1)).date()


def window_bounds(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    """把日期字符串转为 [start 00:00, end 23:59:59.999] 边界（本项目窗口语义）。"""
    start = _parse(start_date)
    end = _parse(end_date)
    if start is None or end is None:
        raise DateWindowError(f"窗口日期不可解析: {start_date!r}..{end_date!r}")
    return (
        datetime.combine(start.date(), time.min),
        datetime.combine(end.date(), time.max),
    )


def coverage_gap(
    dates: Iterable[Optional[Any]],
    start_date: str,
    end_date: str,
    source: str,
    subject: str,
) -> Optional[str]:
    """数据源覆盖不到请求窗口时的显式声明；完整覆盖返回 None。

    ``dates`` 为返回条目的时间戳集合（加上固定回看源的回看起点）。
    只服务最近条目的源在历史窗口上"查无"是覆盖缺口，不是当时的缺席
    ——声明必须写清这一点，���分析师不会把"看不到"当"没有"。
    """
    def _as_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return to_local(value)
        if value is None:
            return None
        return _parse(str(value))

    parsed_dates = [d for d in (_as_dt(x) for x in dates) if d is not None]
    oldest = min(parsed_dates) if parsed_dates else datetime.now()
    end_dt = _parse(end_date)
    start_dt = _parse(start_date)
    now = datetime.now()
    if end_dt is not None and end_dt.date() > now.date():
        reason = "窗口延伸到未来"
    elif not parsed_dates and start_dt is not None and start_dt.date() < now.date():
        # 空结果 + 历史窗口：无任何条目可估覆盖起点 → 声明覆盖缺口
        reason = "该数据源只提供最近条目，覆盖不到请求的起点"
    elif start_dt is not None and oldest.date() > start_dt.date():
        reason = "该数据源只提供最近条目，覆盖不到请求的起点"
    else:
        return None
    return (
        f"<{source} 对 {start_date}..{end_date} 不可用：{reason}，"
        f"因此这不是“当时没有{subject}”，而是本工具看不到>"
    )


def withhold_live_snapshot(as_of_date: Optional[str], label: str) -> Optional[str]:
    """A 股实时快照（估值/市值/实时盘口）对历史分析日的替代声明。

    实时行情接口没有历史版本：市值、市盈率、52 周区间都随今日行情移动。
    历史运行读到它们就是把决策后的信息泄进决策前的分析（上游 #1300）。
    A 股的每日指标快照（tushare daily_basic 按日落库）有真实 vintage，
    不在此列——只拦"实时快照"通道。
    """
    if not is_historical(as_of_date):
        return None
    return (
        f"# {label}\n"
        f"# Point-in-time 截至: {as_of_date}\n\n"
        f"实时快照数据对该日期不提供。实时行情接口只返回当日值：市值、"
        f"市盈率、52 周区间都随今日行情移动，服务它们会把 {as_of_date} 分析"
        f"之后的行情信息混入当时的判断。按日入库的历史快照（如有）不受影响。"
    )
