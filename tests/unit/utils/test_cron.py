"""标准 cron 语义回归：APScheduler 原生 from_crontab 把数字星期当 0=周一，会让工作日变成周二到周六。"""

from datetime import datetime

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.utils.cron import standard_cron_trigger, standard_dow_to_names

TZ = "Asia/Shanghai"


def _weekdays(trigger, n=1500):
    out, prev = set(), None
    now = datetime.now(trigger.timezone)
    for _ in range(n):
        prev = trigger.get_next_fire_time(prev, prev or now)
        out.add(prev.weekday())
    return out


def test_native_from_crontab_is_off_by_one_which_is_why_we_wrap_it():
    """记录第三方库的坑：若此用例失败说明 APScheduler 已修复，可以评估移除本模块。"""
    native = _weekdays(CronTrigger.from_crontab("0 9 * * 1-5", timezone=TZ))
    assert native == {1, 2, 3, 4, 5}  # 周二到周六


@pytest.mark.parametrize("expr,expected", [
    ("0 9 * * 1-5", {0, 1, 2, 3, 4}),
    ("*/5 9-15 * * 1-5", {0, 1, 2, 3, 4}),   # .env.example 里的盘中行情同步
    ("0 3 * * 0", {6}),                       # .env.example 里的周日财务同步
    ("0 3 * * 7", {6}), ("0 9 * * 6", {5}), ("0 9 * * 1,3,5", {0, 2, 4}),
    ("0 9 * * 0-6", set(range(7))), ("0 9 * * 5-7", {4, 5, 6}), ("0 9 * * */2", {6, 1, 3, 5}),
    ("0 9 * * mon-fri", {0, 1, 2, 3, 4}), ("0 9 * * *", set(range(7))),
])
def test_weekday_semantics(expr, expected):
    assert _weekdays(standard_cron_trigger(expr, TZ)) == expected


def test_other_fields_unchanged():
    t = standard_cron_trigger("30 18 15 6 *", TZ)
    nxt = t.get_next_fire_time(None, datetime.now(t.timezone))
    assert (nxt.minute, nxt.hour, nxt.day, nxt.month) == (30, 18, 15, 6)


@pytest.mark.parametrize("expr", ["", "0 9 * *", "0 9 * * * *", "61 9 * * *", "0 9 * * 8", "0 9 * * 5-1", "0 9 * * x-", "0 9 * * 1/0"])
def test_invalid(expr):
    with pytest.raises(ValueError):
        standard_cron_trigger(expr, TZ)


def test_names_passthrough():
    assert standard_dow_to_names("MON-FRI") == "mon-fri"
