"""端到端验收：分析完成后核验报告内容与 trading_signals 落库。

用法: python verify_signal.py <task_id>
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else ""


async def main():
    from app.core.database import get_mongo_db

    await init_database()
    db = get_mongo_db()

    # 1. 任务状态与结果
    task = await db.unified_analysis_tasks.find_one({"task_id": TASK_ID})
    if not task:
        print(f"❌ task not found: {TASK_ID}")
        return
    status = task.get("status")
    print(f"[task] status={status}")
    result = task.get("result") or {}
    print(f"[result] keys={sorted(result.keys())[:20]}")

    # 2. 交易信号
    print("\n=== trading_signals (本次任务) ===")
    cursor = db.trading_signals.find({"task_id": TASK_ID})
    found = False
    async for sig in cursor:
        found = True
        print(json.dumps({
            "code": sig.get("code"),
            "side": sig.get("side"),
            "status": sig.get("status"),
            "reference_price": sig.get("reference_price"),
            "quantity": sig.get("quantity"),
            "rejected_reasons": sig.get("rejected_reasons") or sig.get("gate_results"),
            "created_at": str(sig.get("created_at")),
        }, ensure_ascii=False, indent=1))
    if not found:
        print("(本任务无信号——查看最近所有信号)")
        async for sig in db.trading_signals.find(sort=[("created_at", -1)], limit=5):
            print(json.dumps({"code": sig.get("code"), "side": sig.get("side"),
                              "status": sig.get("status"), "ref": sig.get("reference_price"),
                              "task": sig.get("task_id")}, ensure_ascii=False))

    # 3. 决策日志
    print("\n=== 决策日志 (最近5条) ===")
    coll = getattr(db, "decision_logs", None)
    if coll is not None:
        async for dec in coll.find(sort=[("created_at", -1)], limit=5):
            print(json.dumps({k: dec.get(k) for k in
                              ("symbol", "decision", "analysis_date", "status", "source_task_id")},
                             ensure_ascii=False))

    # 4. 报告检查：估值分位/财报比率是否进入工具输出或报告
    print("\n=== 报告相关字段 ===")
    for key in ("report", "final_report", "formatted_result", "content"):
        val = result.get(key) or task.get(key)
        if isinstance(val, str) and len(val) > 100:
            text = val
            print(f"[{key}] len={len(text)}")
            for marker in ("分位", "市盈率", "ROE", "价格分析区间", "参考价", "乐观", "审慎", "中性"):
                idx = text.find(marker)
                if idx >= 0:
                    print(f"  - 含'{marker}' @{idx}: ...{text[max(0, idx-40):idx+80]}...")
            break


asyncio.run(main())
