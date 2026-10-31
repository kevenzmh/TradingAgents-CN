import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402

TASK_ID = "de37338a-26d9-49d5-894d-f6ac676955ce"


async def main():
    from app.core.database import get_mongo_db

    await init_database()
    db = get_mongo_db()
    task = await db.unified_analysis_tasks.find_one({"task_id": TASK_ID})
    result = task.get("result") or {}

    print("=== decision ===")
    print(json.dumps(result.get("decision"), ensure_ascii=False, indent=1))
    print("=== recommendation ===")
    print(str(result.get("recommendation"))[:400])
    print("=== summary ===")
    print(str(result.get("summary"))[:500])
    print("=== key_points ===")
    for kp in (result.get("key_points") or [])[:8]:
        print("-", str(kp)[:150])
    print("=== reports ===")
    reports = result.get("reports")
    print(type(reports).__name__, len(reports) if hasattr(reports, "__len__") else "")


asyncio.run(main())
