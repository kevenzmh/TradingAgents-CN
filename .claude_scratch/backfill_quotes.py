"""为关注列表补齐缺失的日线数据。

用与生产 JIT 数据准备一致的通道：dataflows 接口按优先级
（tushare→akshare→baostock），我们只有 akshare/baostock 可用。
写成与 stock_daily_quotes 同构的字段。
"""
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.core.database import init_database  # noqa: E402

TARGETS = {
    "601318": "中国平安",
    "600887": "伊利股份",
    "601890": "长江电力",
    "300750": "宁德时代",
    "601398": "工商银行",
}


async def fetch_via_akshare(code: str):
    import akshare as ak
    import pandas as pd

    df = ak.stock_zh_a_daily(
        symbol=f"sh{code}" if code.startswith("6") else f"sz{code}",
        start_date="20250901",
        end_date="20261031",
    )
    if df is None or df.empty:
        return None
    rows = []
    now = datetime.utcnow()
    for _, r in df.iterrows():
        td = str(r["date"])[:10].replace("-", "")
        rows.append({
            "symbol": code,
            "code": code,
            "full_symbol": f"sh{code}" if code.startswith("6") else f"sz{code}",
            "market": "A股",
            "period": "daily",
            "trade_date": td,
            "open": float(r.get("open") or 0),
            "high": float(r.get("high") or 0),
            "low": float(r.get("low") or 0),
            "close": float(r.get("close") or 0),
            "pre_close": None,
            "volume": float(r.get("volume") or 0),
            "amount": None,
            "change": None,
            "pct_chg": None,
            "data_source": "akshare",
            "version": 1,
            "created_at": now,
            "updated_at": now,
        })
    return rows


async def main():
    from app.core.database import get_mongo_db

    await init_database()
    db = get_mongo_db()
    for code, name in TARGETS.items():
        n = await db.stock_daily_quotes.count_documents({"symbol": code})
        if n > 0:
            print(f"{code} {name}: 已有 {n} 条，跳过")
            continue
        try:
            rows = await fetch_via_akshare(code)
            if not rows:
                print(f"{code} {name}: akshare 无数据")
                continue
            await db.stock_daily_quotes.insert_many(rows)
            print(f"{code} {name}: 补入 {len(rows)} 条（{rows[0]['trade_date']} → {rows[-1]['trade_date']}）")
        except Exception as e:
            print(f"{code} {name}: 失败 {str(e)[:90]}")

asyncio.run(main())
