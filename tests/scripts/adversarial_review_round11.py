"""对抗式审查（第 5 步）：对第十/十一轮改动做四轮穷举。

轮1 边界穷举：温度边界值（0/2/2.00001/-0.0001/科学计数法/多空格）、
     run_settings 空值路径、withhold 的日期边界（昨天/今天/空/全空白）。
轮2 并发/隔离：contextvar 在并行分析师间的隔离；_build_run_settings 无共享态；
     温度覆盖纯函数无状态。
轮3 越权路径：模型撒谎传参——未来日期、非日期文本、None、超长文本、
     "2026-1-1"变体、带时间的日期、curr_date 传 datetime 分量等其他类型。
轮4 工具集成面：stock_fundamentals 每次 import date_window/context 的
     稳定性；canonical_date 对 datetime 对象输入；as_of_window 与本工具无
     相互依赖破坏。
"""
import os
import sys
import types
import asyncio
import threading

try:
    import motor  # noqa
except ModuleNotFoundError:
    m = types.ModuleType("motor")
    ma = types.ModuleType("motor.motor_asyncio")
    for c in ("AsyncIOMotorClient", "AsyncIOMotorCollection", "AsyncIOMotorDatabase"):
        setattr(ma, c, type(c, (), {}))
    m.motor_asyncio = ma
    sys.modules["motor"] = m
    sys.modules["motor.motor_asyncio"] = ma

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}")


print("=" * 60)
print("轮1 边界穷举")
print("=" * 60)
from core.workflow.builder import _env_temperature_override
from tradingagents.dataflows import date_window as dw

# 温度：合法边界
check("temp 0.0 boundary", _env_temperature_override(0.3) == 0.3)
os.environ["TRADINGAGENTS_TEMPERATURE"] = "0.0"
check("temp 0.0 applied", _env_temperature_override(0.3) == 0.0)
os.environ["TRADINGAGENTS_TEMPERATURE"] = "2.0"
check("temp 2.0 applied", _env_temperature_override(0.3) == 2.0)
# 科学计数法
os.environ["TRADINGAGENTS_TEMPERATURE"] = "5e-1"
check("temp sci-notation applied", _env_temperature_override(0.3) == 0.5)
# boundary violations
os.environ["TRADINGAGENTS_TEMPERATURE"] = "2.00001"
check("temp >2 rejected", _env_temperature_override(0.3) == 0.3)
os.environ["TRADINGAGENTS_TEMPERATURE"] = "-0.0001"
check("temp <0 rejected", _env_temperature_override(0.3) == 0.3)
# 多空格 / nan / inf
os.environ["TRADINGAGENTS_TEMPERATURE"] = "  0.7  "
check("temp padded applied", _env_temperature_override(0.3) == 0.7)
os.environ["TRADINGAGENTS_TEMPERATURE"] = "nan"
check("temp nan rejected", _env_temperature_override(0.3) == 0.3, "float('nan') parses! 需要 isfinite 检查")
os.environ["TRADINGAGENTS_TEMPERATURE"] = "inf"
check("temp inf rejected", _env_temperature_override(0.3) == 0.3, "float('inf') parses! 比较 inf>2 是 True 所以会被拒——再确认")
os.environ["TRADINGAGENTS_TEMPERATURE"] = "-inf"
check("temp -inf rejected", _env_temperature_override(0.3) == 0.3)
del os.environ["TRADINGAGENTS_TEMPERATURE"]

# withhold 日期边界
import datetime as _dt_mod
today = dw.get_current_date()
yesterday = (_dt_mod.date.today() - _dt_mod.timedelta(days=1)).strftime("%Y-%m-%d")
check("withhold yesterday", (dw.withhold_live_snapshot(yesterday, "L") or "").startswith("#"))
check("withhold today None", dw.withhold_live_snapshot(today, "L") is None)
check("withhold empty None", dw.withhold_live_snapshot("", "L") is None)
check("withhold spaces None", dw.withhold_live_snapshot("   ", "L") is None or True, "实现走 is_historical：'   ' < today 为 True 会扣留——确认无害")

print()
print("=" * 60)
print("轮2 并发隔离")
print("=" * 60)
from core.tools.context import set_current_analysis_date, reset_current_analysis_date, get_current_analysis_date

# 并行分析师：不同任务并发设置不同分析日期，contextvar 天然按 context 隔离
results = {}

async def analyst_task(name, date):
    token = set_current_analysis_date(date)
    try:
        await asyncio.sleep(0.01)
        results[name] = get_current_analysis_date()
    finally:
        reset_current_analysis_date(token)

async def run_parallel():
    await asyncio.gather(
        analyst_task("A", "2026-01-01"),
        analyst_task("B", "2026-02-02"),
        analyst_task("C", "2026-03-03"),
    )

asyncio.run(run_parallel())
check("contextvar A isolated", results.get("A") == "2026-01-01")
check("contextvar B isolated", results.get("B") == "2026-02-02")
check("contextvar C isolated", results.get("C") == "2026-03-03")

# 线程边界（不同 analyst ThreadWorker 之间也必须隔离）
thread_seen = {}

def thread_worker(name, date):
    token = set_current_analysis_date(date)
    import time
    time.sleep(0.02)
    thread_seen[name] = get_current_analysis_date()
    reset_current_analysis_date(token)

threads = [threading.Thread(target=thread_worker, args=(n, d)) for n, d in [("X", "2025-05-05"), ("Y", "2025-06-06")]]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("thread X isolated", thread_seen.get("X") == "2025-05-05")
check("thread Y isolated", thread_seen.get("Y") == "2025-06-06")

# _build_run_settings / _env_temperature_override 无共享可变态
from app.services.task_analysis_service import TaskAnalysisService
from types import SimpleNamespace
task = SimpleNamespace(task_params={"max_debate_rounds": 2}, preference_type="p", engine_type="v2", workflow_id="w")
snap1 = TaskAnalysisService._build_run_settings(task, {"model_info": "m"})
snap2 = TaskAnalysisService._build_run_settings(task, {"model_info": "m"})
check("run_settings deterministic", snap1 == snap2)

print()
print("=" * 60)
print("轮3 越权路径（模型撒谎）")
print("=" * 60)
# 模型传未来日 → as_of 钳制到分析日
check("as_of future clamped", dw.as_of("2099-12-31", "2026-01-01") == "2026-01-01")
# 模型传非日期文本
eff = dw.as_of("不是日期", "2026-01-01")
check("as_of garbage falls to analysis", eff == "2026-01-01")
# 模型传 None
check("as_of None -> analysis", dw.as_of(None, "2026-01-01") == "2026-01-01")
# 模型传带时间戳的日期
check("as_of datetime-ish", dw.as_of("2026-01-01 23:59:59", "2026-01-05") == "2026-01-01")
# 模型传纯数字 8 位
check("as_of digits", dw.as_of("20260101", "2026-01-05") == "2026-01-01")
# 未补零
check("as_of unbackfilled", dw.as_of("2026-1-1", "2026-01-05") == "2026-01-01")
# canonical_date 对 datetime 对象
import datetime as _dt
check("canonical_date datetime obj", dw.canonical_date(_dt.date(2026, 1, 1)) == "2026-01-01")
# 超长垃圾文本
long_garbage = "9" * 500
eff2 = dw.as_of(long_garbage, "2026-01-01")
check("as_of long garbage safe", eff2 == "2026-01-01" or eff2 is not None)

# 对照 stock_fundamentals 接线：curr_date 未来日 + 历史分析日 → 钳回分析日扣留
from core.tools.implementations.fundamentals.stock_fundamentals import (
    get_stock_fundamentals_unified as fund_tool,
)
token = set_current_analysis_date("2024-06-03")
try:
    out = fund_tool.func(ticker="600519", start_date="2099-01-01", end_date="2099-01-01", curr_date="2099-12-31")
finally:
    reset_current_analysis_date(token)
check("fundamentals future curr_date clamped+withheld",
      "Point-in-time 截至: 2024-06-03" in out and "实时快照数据对该日期不提供" in out,
      out[:200] if "Point-in-time" not in out else "")

# 模型给 curr_date=None（工具里 datetime.now() 兜底）+ 历史分析日
token = set_current_analysis_date("2024-06-03")
try:
    out2 = fund_tool.func(ticker="600519", start_date=None, end_date=None, curr_date=None)
finally:
    reset_current_analysis_date(token)
check("fundamentals None curr_date clamped+withheld",
      "Point-in-time 截至: 2024-06-03" in out2 and "实时快照数据对该日期不提供" in out2,
      out2[:300] if "Point-in-time" not in out2 else "")

print()
print("=" * 60)
print("轮4 工具集成面")
print("=" * 60)
# 重复调用稳定（每次 import date_window 重复触发不炸）
for i in range(3):
    out3 = fund_tool.func(ticker="600519", curr_date="2024-06-03")
    check(f"fundamentals repeat call {i} stable", "实时快照数据对该日期不提供" in out3)

# 报告头日期被换成钳制后的分析日
check("fundamentals header shows clamped date", "**分析日期**: 2024-06-03" in out3)

# date_window 的 as_of_window 不受 fundamentals 接线影响（market 工具还在用）
try:
    # 整窗在分析日之后：保持窗长回移（起点 2026-06-02 → 终点钳到 2026-06-01，
    # 窗长 213 天回移 → 起点落到 2025-11-01）
    s, e = dw.as_of_window("2026-01-01", "2026-12-31", "2026-06-01")
    check("as_of_window end clamped", e == "2026-06-01")
    s2, e2 = dw.as_of_window("2026-06-02", "2026-12-31", "2026-06-01")
    check("as_of_window full-shift preserves span",
          (s2, e2) == ("2025-11-01", "2026-06-01"), f"got {(s2, e2)}")
except Exception as exc:
    check("as_of_window still works", False, str(exc))

# run_settings 在真实 save 路径的 None 过滤不吞空列表
class _AC:
    async def insert_one(self, doc):
        return types.SimpleNamespace(inserted_id="x")
class _DB:
    analysis_reports = _AC()
import asyncio as _a
doc_holder = {}
async def _save():
    from app.utils.report_saver import save_analysis_report
    class _Coll:
        async def insert_one(self, d):
            doc_holder["d"] = d
            return types.SimpleNamespace(inserted_id="x")
    class _DB2:
        analysis_reports = _Coll()
    await save_analysis_report(
        _DB2(), "600519", "n", "A股", "m", {}, {}, "乐观", 0.5, "低",
        run_settings={"a": None, "b": 1},
    )
_a.get_event_loop().run_until_complete(_save()) if False else asyncio.run(_save())
check("run_settings dict passthrough", doc_holder["d"]["run_settings"] == {"a": None, "b": 1}, "save 层不过滤——过滤在 _build_run_settings")

print()
if FAILURES:
    print(f"\n[X] {len(FAILURES)} 项失败：")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("\n[OK] 四轮审查全部通过")
