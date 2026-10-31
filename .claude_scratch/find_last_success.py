import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402


async def main():
    from app.core.database import get_mongo_db

    await init_database()
    db = get_mongo_db()

    cursor = db.unified_analysis_tasks.find(
        {"status": "completed"},
        sort=[("created_at", -1)],
        limit=3,
    )
    async for doc in cursor:
        params = doc.get("task_params") or {}
        print(json.dumps({
            "task_id": doc.get("task_id"),
            "symbol": params.get("symbol") or params.get("stock_code"),
            "created_at": str(doc.get("created_at")),
            "quick": params.get("quick_analysis_model"),
            "deep": params.get("deep_analysis_model"),
            "engine": params.get("engine") or doc.get("engine_type"),
            "workflow_id": doc.get("workflow_id"),
        }, ensure_ascii=False))


asyncio.run(main())
