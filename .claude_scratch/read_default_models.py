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
    doc = await db.system_configs.find_one({"is_active": True}, sort=[("version", -1)])
    print(json.dumps({
        "default_models": doc.get("default_models"),
        "quick_cfg": doc.get("system_settings", {}).get("quick_analysis_model"),
        "deep_cfg": doc.get("system_settings", {}).get("deep_analysis_model"),
    }, ensure_ascii=False, indent=1))


asyncio.run(main())
