"""标准 cron 语义的触发器构建（分 时 日 月 周）。

背景：APScheduler 3.x 的 `CronTrigger.from_crontab` 把数字星期当成 **0=周一**，而标准 cron
是 **0/7=周日、1=周一**。直接使用会让 `1-5`（工作日）变成周二到周六：周一盘中行情不同步、
周六空跑，`0`（周日）变成周一。本模块自行把数字星期展开为英文缩写后再交给 APScheduler。

所有按 `.env` / 用户填写的 cron 字符串创建触发器的地方都应使用 `standard_cron_trigger`。
"""

from __future__ import annotations

from typing import List

from apscheduler.triggers.cron import CronTrigger

_DOW_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]  # 标准 cron：0 与 7 都是周日


def standard_dow_to_names(field: str) -> str:
    """把标准 cron 的星期字段转成 APScheduler 不会误解的英文缩写列表。

    支持 *, 数字, a-b, a,b, */n, a-b/n；字段含字母（如 mon-fri）时语义无歧义，原样返回。
    不合法时抛 ValueError。
    """
    if any(ch.isalpha() for ch in field):
        return field.lower()
    days: List[int] = []
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"星期字段步长无效：{field}")
            step = int(step_s)
        if part == "*":
            lo, hi = 0, 6
        elif "-" in part:
            lo_s, _, hi_s = part.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"星期字段无效：{field}")
            lo, hi = int(lo_s), int(hi_s)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            raise ValueError(f"星期字段无效：{field}")
        if not (0 <= lo <= 7 and 0 <= hi <= 7) or lo > hi:
            raise ValueError(f"星期字段超出范围（0-7，0 和 7 都表示周日）：{field}")
        days.extend(d % 7 for d in range(lo, hi + 1, step))
    ordered = sorted(set(days), key=lambda d: (d + 6) % 7)  # 周一在前，便于阅读
    return ",".join(_DOW_NAMES[d] for d in ordered)


def standard_cron_trigger(expr: str, timezone: str) -> CronTrigger:
    """按标准 cron 语义构建 CronTrigger；表达式不合法抛 ValueError。"""
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise ValueError("CRON 必须是 5 段：分 时 日 月 周，例如 0 9 * * 1-5")
    minute, hour, day, month, dow = fields
    dow_names = standard_dow_to_names(dow) if dow != "*" else None  # 自身的校验错误原样抛出
    try:
        return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow_names, timezone=timezone)
    except Exception as e:  # APScheduler 对非法取值抛多种异常（ValueError/TypeError 等）
        raise ValueError(f"CRON 表达式无效：{e}")
