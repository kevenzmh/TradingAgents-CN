"""历史K线导入校验（Pro）。

用户通过 API 批量导入的 K 线先在这里校验并整理成 DataFrame，再交给
`HistoricalDataService.save_historical_data` 写入 `stock_daily_quotes`——与同步任务
走同一个规范写入器，保证字段口径一致（trade_date 为 YYYY-MM-DD，自动计算涨跌幅等）。

数据源固定为 `user_import`：
- 唯一索引含 data_source，不会覆盖 tushare / akshare / baostock 同步来的数据
- 避免写入器对 tushare 数据做单位换算（千元→元、手→股）误伤用户数据
读取方（持仓、模拟盘、分析任务）按 symbol 取最新收盘价，不按 data_source 过滤，因此能读到导入数据。
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

MAX_RECORDS = 5000
DATA_SOURCE = "user_import"
PERIODS = ("daily", "weekly", "monthly")
MARKETS = ("CN", "HK", "US")


def parse_trade_date(value: Any) -> str:
    """解析交易日，返回 YYYY-MM-DD。无法解析或在未来抛 ValueError。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("缺少 date")
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        text = str(value).strip().split(" ")[0].split("T")[0]
        d = None
        if re.fullmatch(r"\d{8}", text):
            fmts = ("%Y%m%d",)
        else:
            fmts = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
        for fmt in fmts:
            try:
                d = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if d is None:
            raise ValueError(f"date 无法解析: {value!r}")
    if d > date.today() + timedelta(days=1):
        raise ValueError(f"date 在未来: {value!r}")
    return d.strftime("%Y-%m-%d")


def _number(row: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name in row and row[name] not in (None, ""):
            try:
                f = float(row[name])
            except (TypeError, ValueError):
                raise ValueError(f"{name} 不是数字: {row[name]!r}")
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError(f"{name} 非法: {row[name]!r}")
            return f
    return None


def validate_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规整单条 K 线。非法抛 ValueError（带可读原因）。"""
    if not isinstance(row, dict):
        raise ValueError("记录必须是对象")

    trade_date = parse_trade_date(row.get("date") if row.get("date") not in (None, "") else row.get("trade_date"))
    close = _number(row, "close")
    if close is None or close <= 0:
        raise ValueError("close 必须为正数")
    open_ = _number(row, "open")
    high = _number(row, "high")
    low = _number(row, "low")
    for label, v in (("open", open_), ("high", high), ("low", low)):
        if v is not None and v <= 0:
            raise ValueError(f"{label} 必须为正数")
    if high is not None and low is not None and high < low:
        raise ValueError(f"high({high}) 不能小于 low({low})")
    if high is not None and close > high * 1.0001:
        raise ValueError(f"close({close}) 超过 high({high})")
    if low is not None and close < low * 0.9999:
        raise ValueError(f"close({close}) 低于 low({low})")

    volume = _number(row, "volume", "vol")
    amount = _number(row, "amount", "turnover")
    for label, v in (("volume", volume), ("amount", amount)):
        if v is not None and v < 0:
            raise ValueError(f"{label} 不能为负")

    out: Dict[str, Any] = {
        "date": trade_date,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else max(close, open_ or close),
        "low": low if low is not None else min(close, open_ or close),
        "close": close,
        "volume": volume if volume is not None else 0.0,
        "amount": amount if amount is not None else 0.0,
    }
    pre_close = _number(row, "pre_close", "preclose")
    if pre_close is not None:
        if pre_close <= 0:
            raise ValueError("pre_close 必须为正数")
        out["pre_close"] = pre_close
    for name in ("turnover_rate", "pe", "pb", "ps", "volume_ratio"):
        v = _number(row, name)
        if v is not None:
            out[name] = v
    return out


def prepare_frame(records: List[Dict[str, Any]]) -> Tuple[Optional[pd.DataFrame], List[Dict[str, Any]], int]:
    """校验整批记录。返回 (DataFrame 或 None, 错误列表, 批内重复日期数)。

    同一批内同一交易日重复时保留最后一条（后者视为更正）。
    """
    valid: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    duplicates = 0
    for index, row in enumerate(records):
        try:
            rec = validate_record(row)
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
            continue
        if rec["date"] in valid:
            duplicates += 1
        valid[rec["date"]] = rec
    if not valid:
        return None, errors, duplicates
    frame = pd.DataFrame(sorted(valid.values(), key=lambda r: r["date"]))
    return frame, errors, duplicates


async def save_records(symbol: str, records: List[Dict[str, Any]], *, market: str = "CN",
                       period: str = "daily", overwrite: bool = True) -> Dict[str, Any]:
    """校验并写入。返回 {total, saved, skipped, failed, errors}。"""
    from app.pro.services.social_media_service import normalize_symbol
    from app.services.historical_data_service import get_historical_data_service

    code = normalize_symbol(symbol, market)
    if not code:
        raise ValueError("symbol 不能为空")
    if market not in MARKETS:
        raise ValueError(f"market 必须是 {MARKETS}")
    if period not in PERIODS:
        raise ValueError(f"period 必须是 {PERIODS}")
    if len(records) > MAX_RECORDS:
        raise ValueError(f"单次最多 {MAX_RECORDS} 条记录")

    frame, errors, duplicates = prepare_frame(records)
    result = {"total": len(records), "symbol": code, "market": market, "period": period,
              "saved": 0, "skipped": duplicates, "failed": len(errors), "errors": errors[:50]}
    if frame is None:
        return result

    service = await get_historical_data_service()
    saved = await service.save_historical_data(
        symbol=code, data=frame, data_source=DATA_SOURCE,
        market=market, period=period, overwrite=overwrite,
    )
    result["saved"] = int(saved)
    result["skipped"] += max(0, len(frame) - int(saved))
    return result
