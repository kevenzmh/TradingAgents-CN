import asyncio
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
    reports = (task.get("result") or {}).get("reports") or {}
    for name in reports:
        print("[report]", name)

    for name, content in reports.items():
        if "fundamental" not in name:
            continue
        text = content if isinstance(content, str) else ""
        idx = text.find("估值")
        while idx >= 0:
            print(f"\n--- {name} @ {idx} ---")
            print(text[max(0, idx - 100):idx + 400])
            idx = text.find("估值", idx + 400)
            if idx > 6000:
                break


asyncio.run(main())
