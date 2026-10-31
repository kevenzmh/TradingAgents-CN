import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402

TASK_ID = "6099e046-4c1e-4eac-a037-32c4a8f1a99c"


async def main():
    from app.core.database import get_mongo_db

    await init_database()
    db = get_mongo_db()
    task = await db.unified_analysis_tasks.find_one({"task_id": TASK_ID})
    result = task.get("result") or {}
    print("[run_settings]", json.dumps(result.get("run_settings") or {}, ensure_ascii=False)[:300])
    print("[decision]", json.dumps(result.get("decision"), ensure_ascii=False)[:300])
    print("[summary]", str(result.get("summary"))[:300])

    reports = result.get("reports") or {}
    for name, content in reports.items():
        text = content if isinstance(content, str) else ""
        markers = [m for m in ("分位", "市盈率", "ROE", "毛利率", "净利率") if m in text]
        print(f"[report:{name}] len={len(text)} markers={markers}")

    print("\n=== trading_signals (本任务) ===")
    async for sig in db.trading_signals.find({"task_id": TASK_ID}):
        print(json.dumps({k: sig.get(k) for k in
                          ("code", "side", "status", "reference_price", "quantity")},
                         ensure_ascii=False))


asyncio.run(main())
