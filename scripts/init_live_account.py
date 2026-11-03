"""实盘账户初始化/校准工具（第三阶段）：从 QMT 同步持仓与资金到 MongoDB。

解决「实盘账户没有初始状态」的问题。当前系统只有 paper_accounts（模拟盘），
live 模式下订单能下但资金/持仓状态不在库中，资金校验与持仓对账无从谈起。

用法（命令行工具，不对外暴露 HTTP 接口）::

    # 预览即将写入的内容（不落库）
    python -m scripts.init_live_account --user admin --dry-run

    # 正式同步
    python -m scripts.init_live_account --user admin

行为：
- 通过 QMTAdapter.query_account_snapshot() 只读查询券商账户资金与持仓
- 写入 paper_accounts / paper_positions，user_id 加 `_live` 后缀与模拟盘隔离
- 幂等：账户 upsert；持仓全量替换（先删后插），避免残留已清仓标的
- **只读 QMT，绝不修改券商账户**

使用场景：首次启用实盘前初始化、每日收盘后校准、疑似不一致时人工核对。
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

# 支持 `python -m scripts.init_live_account` 与直接运行两种方式
if __package__ in (None, ""):
    sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_mongo_db_sync  # noqa: E402
from app.services.data_sources.qmt_adapter import QMTAdapter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("init_live_account")


def _position_to_doc(pos: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """QMT 持仓行 → paper_positions 文档。"""
    qty = int(pos.get("quantity") or 0)
    can_use = pos.get("can_use_quantity")
    return {
        "user_id": user_id,
        "code": str(pos.get("code") or ""),
        "market": str(pos.get("market") or "CN"),
        "currency": str(pos.get("currency") or "CNY"),
        "quantity": qty,
        "available_qty": int(can_use) if can_use is not None else qty,
        "avg_cost": float(pos.get("cost_price") or 0.0),
        "market_value": float(pos.get("market_value") or 0.0),
        "notes": str(pos.get("notes") or ""),
        "source": "qmt_live_sync",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_snapshot() -> Dict[str, Any]:
    """查询 QMT 账户快照（同步，只读）。"""
    settings = get_settings()
    account_id = (settings.QMT_ACCOUNT_ID or "").strip()
    if not account_id:
        raise RuntimeError("QMT_ACCOUNT_ID 未配置，无法同步实盘账户")

    account_type = (settings.QMT_ACCOUNT_TYPE or "STOCK").strip() or "STOCK"
    snapshot = QMTAdapter().query_account_snapshot(
        account_id=account_id, account_type=account_type
    )
    if not snapshot:
        raise RuntimeError(
            "QMT 账户查询失败：终端未运行 / 账号未登录 / xtquant 不可用"
        )
    return snapshot


def sync_live_account(user_id: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """执行同步。返回结果摘要（供 CLI 打印与测试断言）。"""
    snapshot = fetch_snapshot()
    live_user_id = f"{user_id}_live"
    now_iso = datetime.now(timezone.utc).isoformat()

    asset = snapshot.get("asset") or {}
    cash = float(asset.get("cash") or 0.0)
    total_asset = float(asset.get("total_asset") or 0.0)
    market_value = float(asset.get("market_value") or 0.0)

    settings = get_settings()
    account_doc = {
        "user_id": live_user_id,
        "cash": {"CNY": cash, "HKD": 0.0, "USD": 0.0},
        "realized_pnl": {"CNY": 0.0, "HKD": 0.0, "USD": 0.0},
        "settings": {"auto_currency_conversion": False, "default_market": "CN"},
        "qmt_account_id": settings.QMT_ACCOUNT_ID,
        "qmt_market_value": market_value,
        "qmt_total_asset": total_asset,
        "synced_at": now_iso,
        "updated_at": now_iso,
    }

    positions_df = snapshot.get("positions_df")
    rows: List[Dict[str, Any]] = (
        positions_df.to_dict("records")
        if positions_df is not None and not getattr(positions_df, "empty", True)
        else []
    )
    position_docs = [
        _position_to_doc(p, live_user_id) for p in rows if int(p.get("quantity") or 0) > 0
    ]

    summary = {
        "live_user_id": live_user_id,
        "cash": cash,
        "market_value": market_value,
        "total_asset": total_asset,
        "positions_count": len(position_docs),
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info("[DRY-RUN] 账户: %s", account_doc)
        logger.info("[DRY-RUN] 持仓 %d 条:", len(position_docs))
        for d in position_docs:
            logger.info(
                "   %s x%d 成本 %.3f 市值 %.2f",
                d["code"], d["quantity"], d["avg_cost"], d["market_value"],
            )
        summary["written"] = False
        return summary

    db = get_mongo_db_sync()
    res = db["paper_accounts"].update_one(
        {"user_id": live_user_id}, {"$set": account_doc}, upsert=True
    )
    logger.info(
        "账户已%s: %s | 现金 %.2f | 市值 %.2f | 总资产 %.2f",
        "更新" if res.matched_count else "创建",
        live_user_id, cash, market_value, total_asset,
    )

    # 持仓全量替换：避免已清仓标的残留在库
    deleted = db["paper_positions"].delete_many({"user_id": live_user_id})
    if position_docs:
        db["paper_positions"].insert_many(position_docs)
    logger.info(
        "持仓已同步: 写入 %d 条（清理旧记录 %d 条）",
        len(position_docs), getattr(deleted, "deleted_count", 0),
    )

    summary["written"] = True
    summary["deleted_old"] = getattr(deleted, "deleted_count", 0)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 QMT 同步实盘账户到 MongoDB（只读券商，仅写本地库）"
    )
    parser.add_argument("--user", default="admin", help="用户标识（写入 <user>_live）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写库")
    args = parser.parse_args()

    try:
        summary = sync_live_account(args.user, dry_run=args.dry_run)
    except Exception as exc:
        logger.error("❌ 同步失败: %s", exc)
        return 1

    if summary.get("written"):
        logger.info(
            "✅ 同步完成 | 总资产 %.2f | 持仓 %d 只",
            summary["total_asset"], summary["positions_count"],
        )
    else:
        logger.info("ℹ️  dry-run 完成，未写入数据库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
