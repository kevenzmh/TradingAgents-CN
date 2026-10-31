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
    for name, content in (reports.items() if isinstance(reports, dict) else []):
        text = content if isinstance(content, str) else str(content)
        markers = [m for m in ("分位", "市盈率", "ROE", "毛利率", "净利率", "经营现金流", "价格分析区间")
                   if m in text]
        print(f"[{name}] len={len(text)} markers={markers}")

    # 估值分位证据：找含“分位”的报告段落
    for name, content in reports.items():
        text = content if isinstance(content, str) else ""
        idx = text.find("分位")
        if idx >= 0:
            print(f"\n--- {name} 分位上下文 ---")
            print(text[max(0, idx - 150):idx + 250])


asyncio.run(main())
