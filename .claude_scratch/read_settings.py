import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402


async def main():
    from app.services.config_service import config_service

    await init_database()
    settings = await config_service.get_system_settings()
    print(json.dumps(settings, ensure_ascii=False, indent=1, default=str)[:2000])


asyncio.run(main())
