#!/usr/bin/env python3
"""
通达信（TDX）本地数据提供器

把 app/services/data_sources/tdx_adapter.TDXAdapter 包装成 provider 接口，
供 JIT 数据准备（analysis_worker._get_provider）按数据源优先级调用。

只提供行情类能力：历史行情、实时快照、股票列表。
新闻与财务由其他数据源负责。
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..base_provider import BaseStockDataProvider

logger = logging.getLogger(__name__)


class TDXProvider(BaseStockDataProvider):
    """通达信本地行情文件提供器"""

    def __init__(self):
        super().__init__("tdx")
        self._adapter = None
        self.connected = False

    def _get_adapter(self):
        if self._adapter is None:
            from app.services.data_sources.tdx_adapter import TDXAdapter

            self._adapter = TDXAdapter()
        return self._adapter

    async def connect(self) -> bool:
        try:
            adapter = self._get_adapter()
            self.connected = await asyncio.to_thread(adapter.is_available)
            if self.connected:
                logger.info("✅ TDX 提供器就绪（本地 vipdoc）")
            return self.connected
        except Exception as exc:
            logger.warning(f"⚠️ TDX 提供器初始化失败: {exc}")
            self.connected = False
            return False

    def is_available(self) -> bool:
        if self.connected:
            return True
        try:
            self.connected = self._get_adapter().is_available()
        except Exception:
            self.connected = False
        return self.connected

    # ==================== 核心数据接口 ====================

    async def get_historical_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date] = None,
        period: str = "daily",
    ) -> Optional[pd.DataFrame]:
        if not self.is_available():
            return None

        period_value = {"daily": "day", "weekly": "week", "monthly": "month"}.get(
            str(period or "daily").lower(), "day"
        )
        start_digits = _to_digits(start_date)
        end_digits = _to_digits(end_date) if end_date else None

        span_days = 365
        if start_digits and end_digits:
            try:
                span_days = (
                    datetime.strptime(end_digits, "%Y%m%d")
                    - datetime.strptime(start_digits, "%Y%m%d")
                ).days
            except ValueError:
                span_days = 365
        limit = max(60, int(span_days * 250 / 365 * 1.2) + 30)

        try:
            # 与 AKShare provider 口径一致：落库价格必须是前复权。
            # 传 None 会把不复权原始价写进 stock_daily_quotes，除权日前后收益全错。
            items = await asyncio.to_thread(
                self._get_adapter().get_kline, symbol, period_value, limit, "qfq"
            )
        except Exception as exc:
            logger.warning(f"TDX: 获取 {symbol} 行情失败: {exc}")
            return None
        if not items:
            return None

        frame = pd.DataFrame(items)
        frame = frame.rename(columns={"time": "date"})
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")

        if start_digits:
            frame = frame[frame["date"] >= pd.to_datetime(start_digits)]
        if end_digits:
            frame = frame[frame["date"] <= pd.to_datetime(end_digits)]
        if frame.empty:
            return None

        # 前复权序列的 close 比值等于真实收益率，可直接由相邻收盘价推出 pre_close，
        # 供入库时计算 pct_chg（HistoricalDataService 只给 HK/US 自动补这一列）。
        frame["pre_close"] = frame["close"].shift(1)
        frame["code"] = str(symbol)[:6].zfill(6)
        return frame.reset_index(drop=True)

    async def get_stock_basic_info(self, symbol: str = None):
        if not self.is_available():
            return None

        adapter = self._get_adapter()
        if symbol is None:
            listing = await asyncio.to_thread(adapter.get_stock_list)
            if listing is None or listing.empty:
                return []
            return listing.to_dict("records")

        listing = await asyncio.to_thread(adapter.get_stock_list)
        if listing is None or listing.empty:
            return None
        code6 = str(symbol).strip().split(".")[0].zfill(6)
        matched = listing[listing["symbol"] == code6]
        if matched.empty:
            return None
        row = matched.iloc[0]
        return {
            "symbol": code6,
            "code": code6,
            "name": row.get("name"),
            "full_symbol": row.get("full_symbol"),
            "market": row.get("market"),
            "data_source": "tdx",
        }

    async def get_stock_quotes(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None
        try:
            quotes = await asyncio.to_thread(self._get_adapter().get_realtime_quotes)
        except Exception as exc:
            logger.warning(f"TDX: 获取快照失败: {exc}")
            return None
        code6 = str(symbol).strip().split(".")[0].zfill(6)
        data = (quotes or {}).get(code6)
        if not data:
            return None
        return {**data, "code": code6, "data_source": "tdx"}

    # ==================== 不支持的能力 ====================

    async def get_financial_data(self, symbol: str, report_type: str = "annual"):
        return None


def _to_digits(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


_provider: Optional[TDXProvider] = None


def get_tdx_provider() -> TDXProvider:
    global _provider
    if _provider is None:
        _provider = TDXProvider()
    return _provider
