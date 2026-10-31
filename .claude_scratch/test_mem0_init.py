"""验证 mem0 记忆层在 Ollama provider（无真实 api_key）下可初始化并写入。"""
import asyncio
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402


async def main():
    from core.memory.config import build_mem0_config
    from core.memory.service import MemoryService

    await init_database()
    cfg = build_mem0_config()
    llm = cfg.get("llm", {})
    print("llm provider:", llm.get("provider"))
    print("llm model:", llm.get("config", {}).get("model"))
    print("llm api_key:", llm.get("config", {}).get("api_key"))
    print("llm base_url:", llm.get("config", {}).get("ollama_base_url"))
    print("embedder:", (cfg.get("embedder") or {}).get("provider"),
          (cfg.get("embedder") or {}).get("config", {}).get("model"))

    svc = MemoryService()
    ok = await svc._ensure_init()
    print("init ok:", ok, "| error:", svc._init_error or "(none)")

    if ok:
        r = await svc.store(
            [{"role": "user", "content": "系统功能验证：记忆层写入冒烟测试（600036 招商银行 PE_TTM 7.24 倍，历史分位 82.7%）"}],
            user_id="qa_smoke_test",
            agent_id="qa",
            session_id="qa_smoke",
        )
        print("store result:", {"success": r.success, "facts": r.facts_extracted,
                                "error": r.error})


asyncio.run(main())
