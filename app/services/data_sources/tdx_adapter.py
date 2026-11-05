"""
通达信（TDX）本地数据源适配器

读取通达信客户端已下载到本机的行情文件，不联网、不依赖第三方行情协议：

    <TDX_DATA_DIR>/vipdoc/{sh,sz,bj}/lday/*.day     日线（32 字节/条）
    <TDX_DATA_DIR>/T0002/hq_cache/{sh,sz,bj}s.tnf   证券名称表
    <TDX_DATA_DIR>/T0002/hq_cache/gbbq              除权除息（前复权）

前置条件：
    1. 本机安装通达信客户端，且已下载日线数据（盘后「数据 → 盘后数据下载」）
    2. 通过 TDX_DATA_DIR 或数据源配置的 data_dir 指向客户端安装目录

能力边界：
    - 支持：股票列表、日线/周线/月线、前复权与后复权、最新交易日、全市场最新快照（由末条日线推导）
    - 不支持：新闻公告、财务数据、分钟线、板块；这些继续走 AKShare / Tushare
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import DataSourceAdapter
from . import tdx_reader

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300
_INDEX_MARKET_HINTS = {
    "000001.SH": ("sh", "000001"),
    "399001.SZ": ("sz", "399001"),
    "399006.SZ": ("sz", "399006"),
    "000300.SH": ("sh", "000300"),
    "000905.SH": ("sh", "000905"),
    "000016.SH": ("sh", "000016"),
}


def _split_code(code: Any) -> tuple[str, str]:
    """把代码拆成 (6 位数字, 交易所后缀)，后缀可能为空。"""
    raw = str(code or "").strip().upper()
    if raw.endswith((".SH", ".SZ", ".BJ")):
        suffix = raw[-3:]
        digits = "".join(ch for ch in raw[:-3] if ch.isdigit()).zfill(6)
        return digits, suffix
    digits = "".join(ch for ch in raw if ch.isdigit()).zfill(6)
    return digits, ""


def _guess_market(digits: str, suffix: str = "") -> str:
    if suffix == ".SH":
        return "sh"
    if suffix == ".SZ":
        return "sz"
    if suffix == ".BJ":
        return "bj"
    # 北交所 920xxx 以 9 开头，必须先于上证 9xxxxx 判断
    if digits.startswith(("920", "4", "8")):
        return "bj"
    if digits.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


class TDXAdapter(DataSourceAdapter):
    """通达信本地文件数据源。"""

    # 探测结果按「目录」缓存：用户在界面上改好目录后，新实例必须重新探测，
    # 否则 5 分钟内一直拿到上一次的 False，界面上的「测试连接」永远失败。
    _available_cache: Dict[str, bool] = {}
    _available_checked_at: Dict[str, float] = {}

    def __init__(self):
        super().__init__()
        self._tdx_dir: Optional[str] = None
        self._names: Optional[Dict[str, str]] = None
        self._events: Optional[pd.DataFrame] = None
        self._events_loaded_at: float = 0.0
        self._latest_date: Optional[int] = None
        self._latest_date_at: float = 0.0
        self._quotes_cache: Optional[Dict[str, Dict[str, Optional[float]]]] = None
        self._quotes_cache_at: float = 0.0

    # ---------- 基础属性 ----------

    @property
    def name(self) -> str:
        return "tdx"

    def _get_default_priority(self) -> int:
        # 本地文件，比在线源更稳更快，与 AKShare 同级
        return 2

    def _resolve_dir(self) -> Optional[str]:
        if self._tdx_dir is not None:
            return self._tdx_dir or None

        candidate = (os.getenv("TDX_DATA_DIR") or "").strip()
        if not candidate:
            try:
                from app.core.config import settings as app_settings

                candidate = str(getattr(app_settings, "TDX_DATA_DIR", "") or "").strip()
            except Exception:
                candidate = ""

        if not candidate:
            try:
                from app.core.unified_config import UnifiedConfigManager

                for item in UnifiedConfigManager().get_data_source_configs():
                    if str(getattr(item, "type", "")).lower() != "tdx":
                        continue
                    params = getattr(item, "config_params", None) or {}
                    candidate = str(params.get("data_dir") or "").strip()
                    if candidate:
                        break
            except Exception:
                candidate = ""

        self._tdx_dir = candidate
        return candidate or None

    def _has_vipdoc(self, root: str) -> bool:
        return any(
            os.path.isdir(os.path.join(root, "vipdoc", market, "lday"))
            for market in tdx_reader.MARKETS
        )

    def is_available(self) -> bool:
        root = self._resolve_dir()
        cache_key = root or ""
        now = time.monotonic()

        checked_at = TDXAdapter._available_checked_at.get(cache_key)
        if checked_at is not None and now - checked_at < _CACHE_TTL_SECONDS:
            return TDXAdapter._available_cache.get(cache_key, False)

        available = bool(root and self._has_vipdoc(root))
        if root and not available:
            logger.warning("TDX: %s 下未找到 vipdoc/*/lday 目录，数据源不可用", root)
        elif not root:
            logger.info("TDX: 未配置 TDX_DATA_DIR，数据源不可用")

        TDXAdapter._available_cache[cache_key] = available
        TDXAdapter._available_checked_at[cache_key] = now
        return available

    # ---------- 名称与事件缓存 ----------

    def _names_map(self) -> Dict[tuple, str]:
        if self._names is None:
            root = self._resolve_dir()
            self._names = tdx_reader.parse_symbol_names(root) if root else {}
        return self._names

    def _dividend_events(self) -> pd.DataFrame:
        now = time.monotonic()
        if self._events is not None and now - self._events_loaded_at < _CACHE_TTL_SECONDS * 12:
            return self._events
        root = self._resolve_dir()
        self._events = tdx_reader.GbbqReader(root).read() if root else pd.DataFrame()
        self._events_loaded_at = now
        return self._events

    # ---------- 股票列表 ----------

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        root = self._resolve_dir()
        if not root:
            return None
        pairs = tdx_reader.list_symbols(root)
        if not pairs:
            logger.warning("TDX: vipdoc 下未找到任何股票的日线文件")
            return None

        names = self._names_map()
        rows = []
        for market, code in pairs:
            suffix = {"sh": ".SH", "sz": ".SZ", "bj": ".BJ"}[market]
            rows.append({
                "symbol": code,
                "code": code,
                "ts_code": f"{code}{suffix}",
                "full_symbol": f"{code}{suffix}",
                "name": names.get((market, code), code),
                "market": market.upper(),
                "category": "stock_cn",
            })
        logger.info("TDX: 股票列表 %s 只（来自 vipdoc）", len(rows))
        return pd.DataFrame(rows)

    # ---------- 交易日 ----------

    def find_latest_trade_date(self) -> Optional[str]:
        """扫全市场日线文件的末条日期，取众数作为最新交易日。

        单只股票可能停牌，直接取 max 会拿到个别补录数据；众数更能代表全市场。
        """
        root = self._resolve_dir()
        if not root:
            return None

        now = time.monotonic()
        if self._latest_date is not None and now - self._latest_date_at < _CACHE_TTL_SECONDS:
            return str(self._latest_date)

        counts: Dict[int, int] = {}
        for market, code in tdx_reader.list_symbols(root, stocks_only=False):
            path = tdx_reader.day_file_path(root, market, code)
            tail = tdx_reader.read_day_tail(path, 1)
            if not tail:
                continue
            day = tail[-1][0]
            if day >= 19900101:
                counts[day] = counts.get(day, 0) + 1

        if not counts:
            return None
        latest = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
        self._latest_date = latest
        self._latest_date_at = now
        return str(latest)

    # ---------- K 线 ----------

    def _load_frame(self, market: str, digits: str, adj: Optional[str]) -> Optional[pd.DataFrame]:
        root = self._resolve_dir()
        if not root:
            return None
        path = tdx_reader.day_file_path(root, market, digits)
        frame = tdx_reader.read_day_frame(path)
        if frame is None or frame.empty:
            return None

        if adj in ("qfq", "hfq"):
            adjusted = self._adjust(frame, digits, adj)
            if adjusted is not None and not adjusted.empty:
                frame = adjusted
        return frame

    def _adjust(self, frame: pd.DataFrame, digits: str, adj: str) -> Optional[pd.DataFrame]:
        events = tdx_reader.split_events(self._dividend_events(), digits)
        if events.empty:
            return frame
        qfq = tdx_reader.apply_qfq(frame, events)
        if adj == "qfq":
            return qfq
        # 后复权 = 不复权 × (前复权最新因子 / 前复权因子)，等价于把基准挪到最早一根
        ratio = frame["close"] / qfq["close"].where(qfq["close"] != 0)
        factor = ratio.dropna()
        if factor.empty:
            return frame
        scale = float(factor.iloc[-1])
        if scale <= 0:
            return frame
        hfq = frame.copy()
        for column in ("open", "high", "low", "close"):
            hfq[column] = qfq[column] * scale
        return hfq

    def get_kline(
        self,
        code: str,
        period: str = "day",
        limit: int = 120,
        adj: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        digits, suffix = _split_code(code)
        if len(digits) != 6 or not digits.isdigit():
            return None

        hint = _INDEX_MARKET_HINTS.get(str(code).strip().upper())
        market = hint[0] if hint else _guess_market(digits, suffix)

        frame = self._load_frame(market, digits, adj)
        if frame is None or frame.empty:
            # 指数在 sh/sz 下与股票同目录，但代码相同会撞车（000001 既是平安银行也是上证指数），
            # 若指定后缀或命中指数映射，只用对应市场
            if not hint and not suffix:
                other = "sh" if market == "sz" else "sz"
                frame = self._load_frame(other, digits, adj)
        if frame is None or frame.empty:
            return None

        frame = tdx_reader.resample_period(frame, period)
        if frame is None or frame.empty:
            return None

        tail = frame.tail(max(int(limit or 0), 1))
        items: List[Dict[str, Any]] = []
        for day, row in tail.iterrows():
            items.append({
                "time": tdx_reader.normalize_date(day),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "close": _as_float(row.get("close")),
                "volume": _as_float(row.get("volume")),
                "amount": _as_float(row.get("amount")),
            })
        return items

    # ---------- 行情快照 ----------

    def get_realtime_quotes(self) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        """用全市场日线末条记录拼出快照（收盘后更新，盘中不是真实时价）。"""
        root = self._resolve_dir()
        if not root:
            return None

        names = self._names_map()
        quotes: Dict[str, Dict[str, Optional[float]]] = {}
        for market, code in tdx_reader.list_symbols(root):
            path = tdx_reader.day_file_path(root, market, code)
            tail = tdx_reader.read_day_tail(path, 2)
            if not tail:
                continue
            _, op, _hi, _lo, close, amount, volume = tail[-1]
            prev_close = tail[-2][4] if len(tail) >= 2 else None
            pct = None
            if prev_close not in (None, 0):
                pct = round((close / prev_close - 1) * 100, 4)
            quotes[code] = {
                "close": close,
                "open": op,
                "pre_close": prev_close,
                "pct_chg": pct,
                "volume": volume,
                "amount": amount,
                "name": names.get((market, code)),
            }
        if not quotes:
            return None
        logger.info("TDX: 全市场快照 %s 只（来自日线末条记录）", len(quotes))
        return quotes

    # ---------- 不支持的能力 ----------

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        """通达信本地文件不含估值指标，交由其他数据源。"""
        return None

    def get_news(self, code: str, days: int = 2, limit: int = 50, include_announcements: bool = True):
        """通达信本地文件不含新闻公告。"""
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        if result != result:
            return None
        return result
    except (TypeError, ValueError):
        return None


_adapter: Optional[TDXAdapter] = None


def get_tdx_adapter() -> TDXAdapter:
    global _adapter
    if _adapter is None:
        _adapter = TDXAdapter()
    return _adapter