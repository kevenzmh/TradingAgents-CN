"""v3.2.1 migration - 修复日线日期格式与财务报告期展开

迁移内容：
1. stock_daily_quotes: 把误写成凑位 YYYYMMDD 的 trade_date 归一为 YYYY-MM-DD
2. stock_financial_periods: 由 stock_financial_data 快照重新展开报告期

背景：
- 日线写入器（HistoricalDataService._format_date）统一产出 YYYY-MM-DD，但早期
  一次性补数脚本按 YYYYMMDD 直插，导致同一集合内两种日期格式并存。读取侧按
  YYYY-MM-DD 做 $gte/$lte，字符序比较下凑位日期整体错位，那批股票在分析链路上
  表现为"库里没数据"。
- 财务报告期展开曾有两个缺陷：分项说明为空时用 None 覆盖了顶层已有值；akshare 的
  raw_data.main_indicators 是「指标 × 报告期」矩阵而展开逻辑不认识，结果每只股票
  只落一行且字段全空。筛选和因子链路读 stock_financial_periods，因此一直拿不到
  财务数据。

所有操作必须幂等（可安全多次运行）。
"""

import logging
import re

logger = logging.getLogger("migrations.v3_2_1")

VERSION = "3.2.1"
DESCRIPTION = "归一 stock_daily_quotes 日期格式，并按快照重建 stock_financial_periods"

_COMPACT_DATE = re.compile(r"^\d{8}$")
_QUOTE_COLLECTION = "stock_daily_quotes"
_FINANCIAL_SNAPSHOT = "stock_financial_data"
_FINANCIAL_PERIODS = "stock_financial_periods"
_WRITE_BATCH_SIZE = 2000


def _to_dash(value) -> str:
    text = str(value or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if _COMPACT_DATE.match(text) else text


async def _normalize_trade_dates(db) -> int:
    """凑位日期改写成 YYYY-MM-DD；与已有同键文档冲突时跳过（保留规范形）。"""
    collection = db[_QUOTE_COLLECTION]
    cursor = collection.find(
        {"trade_date": {"$regex": "^[0-9]{8}$"}},
        {"symbol": 1, "trade_date": 1, "data_source": 1, "period": 1},
    )
    migrated = 0
    conflicts = 0
    async for doc in cursor:
        normalized = _to_dash(doc.get("trade_date"))
        if not normalized:
            continue
        identity = {
            "symbol": doc.get("symbol"),
            "trade_date": normalized,
            "data_source": doc.get("data_source"),
            "period": doc.get("period"),
        }
        if await collection.find_one(identity, {"_id": 1}):
            conflicts += 1
            continue
        await collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"trade_date": normalized}},
        )
        migrated += 1

    if conflicts:
        logger.info(f"ℹ️ {conflicts} 条凑位日期与已有规范形日期重复，保留规范形")
    return migrated


async def _rebuild_financial_periods(db) -> int:
    """按当前展开规则从快照重建报告期集合。按文档分批提交，避免一次性堆积几十万操作。"""
    from pymongo import ReplaceOne

    from core.skill_runtime.data_access import expand_financial_document_to_periods

    snapshot_collection = db[_FINANCIAL_SNAPSHOT]
    period_collection = db[_FINANCIAL_PERIODS]

    written = 0
    rebuilt_symbols = set()
    pending = []

    async def _flush() -> int:
        if not pending:
            return 0
        batch = list(pending)
        pending.clear()
        await period_collection.bulk_write(batch, ordered=False)
        return len(batch)

    async for document in snapshot_collection.find({}):
        records = expand_financial_document_to_periods(document)
        if not records:
            continue
        symbol = document.get("symbol") or document.get("code")
        if symbol:
            rebuilt_symbols.add(symbol)

        for record in records:
            period = record.get("report_period") or record.get("report_date")
            source = record.get("source") or record.get("data_source")
            if not period or not source:
                continue
            pending.append(
                ReplaceOne(
                    filter={
                        "symbol": record.get("symbol") or record.get("code"),
                        "source": source,
                        "report_period": period,
                        "ann_date": record.get("ann_date") or "",
                    },
                    replacement=record,
                    upsert=True,
                )
            )

        if len(pending) >= _WRITE_BATCH_SIZE:
            written += await _flush()

    written += await _flush()

    logger.info(f"✅ 重建 {written} 条报告期记录，覆盖 {len(rebuilt_symbols)} 只股票")
    return written


async def upgrade(db):
    """执行迁移"""
    normalized = await _normalize_trade_dates(db)
    logger.info(f"✅ 归一 {normalized} 条日线日期格式")

    periods = await _rebuild_financial_periods(db)
    logger.info(f"✅ 报告期集合重建完成: {periods} 条")


async def downgrade(db):
    """无可回滚：日期归一与报告期重建都是修复性覆盖。"""
    logger.warning("ℹ️ v3.2.1 为修复性迁移，不支持回滚")
