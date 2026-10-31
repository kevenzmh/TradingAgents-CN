import asyncio
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402


async def main():
    from app.services.config_service import config_service

    await init_database()
    config = await config_service.get_system_config()
    for m in config.llm_configs:
        print(json.dumps({
            "config_id": m.config_id,
            "provider": m.provider,
            "model_name": m.model_name,
            "api_base": m.api_base,
            "enabled": m.enabled,
            "max_tokens": m.max_tokens,
        }, ensure_ascii=False))


asyncio.run(main())
