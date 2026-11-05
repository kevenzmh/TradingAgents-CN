"""通达信（TDX）本地数据文件读取。

通达信客户端把行情下载到安装目录的 vipdoc/ 下，每种周期一个 .day/.lc1 文件，
文件名形如 sh600519.day / sz000001.day / bj920000.day。

本模块只做二进制解析，不依赖 mootdx/pytdx，便于测试与离线部署：

- 日线记录固定 32 字节：date(u32) open/high/low/close(u32, 单位分) amount(f32) volume(u32) reserved(u32)
- 证券名称表 T0002/hq_cache/{sh,sz,bj}s.tnf 固定 360 字节/条，代码在 [0:6]，名称 GBK 在 [31:48]
- 除权除息表 T0002/hq_cache/gbbq 经 RAND 混淆，用内置密钥表还原，用于前复权

成交量口径：TDX 存的是股，系统内 AKShare 同步链路存的是手，因此对外统一 ÷100。
"""

import base64
import logging
import os
import struct
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

DAY_RECORD_SIZE = 32
DAY_RECORD_FORMAT = "<IIIIIfII"

TNF_HEADER_SIZE = 50
TNF_RECORD_SIZE = 360
TNF_CODE_SLICE = slice(0, 6)
TNF_NAME_SLICE = slice(31, 48)

GBBQ_RECORD_SIZE = 29
GBBQ_HEADER_SIZE = 4

# 复权事件类别：1=除权除息（分红送配），11=ETF 折算。其余为股本变动/增发等，不影响价格连续性。
GBBQ_CATEGORY_XDXR = 1

VOLUME_LOTS_DIVISOR = 100.0
PRICE_DIVISOR = 100.0

MARKETS = ("sh", "sz", "bj")

# 沪深北 A 股股票代码前缀（排除指数 999xxx/000xxx(沪指)、ETF 5xxxxx/1xxxxx、债券 11/12xxxx 等）
_STOCK_PREFIXES = {
    "sh": ("600", "601", "603", "605", "688", "689"),
    "sz": ("000", "001", "002", "003", "300", "301"),
    "bj": ("430", "830", "831", "832", "833", "834", "835", "836", "837", "838",
           "839", "870", "871", "872", "873", "874", "875", "876", "877", "878",
           "920"),
}

# 复权用的 RAND 密钥表（来自通达信客户端协商结果，公开资料整理）
_GBBQ_KEYS_B64 = """
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
e+XW1W8wECO/////8AAAAA
"""


def _gbbq_keys() -> bytes:
    return base64.b64decode("".join(_GBBQ_KEYS_B64.split()))


def is_stock_code(market: str, code: str) -> bool:
    prefixes = _STOCK_PREFIXES.get(market)
    if not prefixes:
        return False
    return str(code).startswith(prefixes)


def day_file_path(tdx_dir: str, market: str, code: str, suffix: str = "lday") -> str:
    return os.path.join(tdx_dir, "vipdoc", market, suffix, f"{market}{code}.{_suffix_ext(suffix)}")


def _suffix_ext(subdir: str) -> str:
    return {"lday": "day", "fzline": "lc5", "minline": "lc1"}.get(subdir, "day")


def read_day_frame(path: str) -> pd.DataFrame:
    """读取一个 .day 文件为 DataFrame，索引为 YYYYMMDD 整数日期，价格已换算为元。"""
    if not os.path.exists(path):
        return pd.DataFrame(columns=["open", "high", "low", "close", "amount", "volume"])
    size = os.path.getsize(path)
    count = size // DAY_RECORD_SIZE
    if count <= 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "amount", "volume"])

    with open(path, "rb") as handle:
        raw = handle.read(count * DAY_RECORD_SIZE)

    dates: List[int] = []
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    amounts: List[float] = []
    volumes: List[float] = []

    for offset in range(0, len(raw), DAY_RECORD_SIZE):
        day, op, hi, lo, cl, amount, volume, _ = struct.unpack(
            DAY_RECORD_FORMAT, raw[offset:offset + DAY_RECORD_SIZE]
        )
        dates.append(day)
        opens.append(op / PRICE_DIVISOR)
        highs.append(hi / PRICE_DIVISOR)
        lows.append(lo / PRICE_DIVISOR)
        closes.append(cl / PRICE_DIVISOR)
        amounts.append(float(amount))
        volumes.append(volume / VOLUME_LOTS_DIVISOR)

    frame = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "amount": amounts,
        "volume": volumes,
    }, index=pd.Index(dates, name="date"))
    return frame.sort_index()


def read_day_tail(path: str, count: int = 1) -> List[Tuple[int, float, float, float, float, float, float]]:
    """只读文件末尾 count 条记录，用于全市场扫描时避免整文件读取。"""
    if not os.path.exists(path):
        return []
    size = os.path.getsize(path)
    total = size // DAY_RECORD_SIZE
    if total <= 0:
        return []

    take = min(count, total)
    with open(path, "rb") as handle:
        handle.seek((total - take) * DAY_RECORD_SIZE)
        raw = handle.read(take * DAY_RECORD_SIZE)

    rows = []
    for offset in range(0, len(raw), DAY_RECORD_SIZE):
        day, op, hi, lo, cl, amount, volume, _ = struct.unpack(
            DAY_RECORD_FORMAT, raw[offset:offset + DAY_RECORD_SIZE]
        )
        rows.append((
            day,
            op / PRICE_DIVISOR,
            hi / PRICE_DIVISOR,
            lo / PRICE_DIVISOR,
            cl / PRICE_DIVISOR,
            float(amount),
            volume / VOLUME_LOTS_DIVISOR,
        ))
    return rows


def parse_symbol_names(tdx_dir: str) -> Dict[Tuple[str, str], str]:
    """解析证券名称表，返回 {(market, code): name}。

    必须按市场区分：sh000001 是上证指数，sz000001 才是平安银行。
    """
    names: Dict[Tuple[str, str], str] = {}
    for market in MARKETS:
        path = os.path.join(tdx_dir, "T0002", "hq_cache", f"{market}s.tnf")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            logger.warning("TDX: 读取名称表失败 %s: %s", path, exc)
            continue

        count = (len(raw) - TNF_HEADER_SIZE) // TNF_RECORD_SIZE
        for index in range(count):
            start = TNF_HEADER_SIZE + index * TNF_RECORD_SIZE
            record = raw[start:start + TNF_RECORD_SIZE]
            code = record[TNF_CODE_SLICE].split(b"\x00")[0].decode("ascii", "ignore")
            if len(code) != 6 or not code.isdigit():
                continue
            name = record[TNF_NAME_SLICE].split(b"\x00")[0].decode("gbk", "ignore").strip()
            if name:
                names.setdefault((market, code), name)
    return names


def list_symbols(tdx_dir: str, stocks_only: bool = True) -> List[Tuple[str, str]]:
    """列出 vipdoc 中已有日线文件的证券，返回 [(market, code)]。"""
    found: List[Tuple[str, str]] = []
    for market in MARKETS:
        lday = os.path.join(tdx_dir, "vipdoc", market, "lday")
        if not os.path.isdir(lday):
            continue
        try:
            entries = os.listdir(lday)
        except OSError as exc:
            logger.warning("TDX: 无法读取目录 %s: %s", lday, exc)
            continue
        prefix = market
        for entry in entries:
            if not entry.lower().endswith(".day"):
                continue
            stem = entry[:-4]
            if not stem.lower().startswith(prefix):
                continue
            code = stem[len(prefix):]
            if len(code) != 6 or not code.isdigit():
                continue
            if stocks_only and not is_stock_code(market, code):
                continue
            found.append((market, code))
    return found


class GbbqReader:
    """除权除息文件（gbbq）解析，用于前复权。"""

    def __init__(self, tdx_dir: str):
        self.path = os.path.join(tdx_dir, "T0002", "hq_cache", "gbbq")

    def read(self) -> pd.DataFrame:
        """返回全部复权事件，列：market code datetime category fenhong peigu peigujia songzhuan。"""
        empty = pd.DataFrame(
            columns=["market", "code", "datetime", "category", "fenhong", "peigu", "peigujia", "songzhuan"]
        )
        if not os.path.exists(self.path):
            return empty
        try:
            with open(self.path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            logger.warning("TDX: 读取 gbbq 失败: %s", exc)
            return empty
        if len(raw) < GBBQ_HEADER_SIZE:
            return empty

        keys = _gbbq_keys()
        count = struct.unpack("<I", raw[:GBBQ_HEADER_SIZE])[0]
        offset = GBBQ_HEADER_SIZE
        rows = []

        for _ in range(count):
            if offset + 29 > len(raw):
                break
            clear = bytearray()
            for _ in range(3):
                num = struct.unpack("<I", keys[0x44:0x48])[0] ^ struct.unpack("<I", raw[offset:offset + 4])[0]
                num_old = struct.unpack("<I", raw[offset + 4:offset + 8])[0]
                for step in reversed(range(4, 0x44, 4)):
                    idx = (num & 0xFF0000) >> 16
                    acc = struct.unpack("<I", keys[idx * 4 + 0x448:idx * 4 + 0x44C])[0]
                    idx = num >> 24
                    acc = (acc + struct.unpack("<I", keys[idx * 4 + 0x48:idx * 4 + 0x4C])[0]) & 0xFFFFFFFF
                    idx = (num & 0xFF00) >> 8
                    acc ^= struct.unpack("<I", keys[idx * 4 + 0x848:idx * 4 + 0x84C])[0]
                    idx = num & 0xFF
                    acc = (acc + struct.unpack("<I", keys[idx * 4 + 0xC48:idx * 4 + 0xC4C])[0]) & 0xFFFFFFFF
                    acc ^= struct.unpack("<I", keys[step:step + 4])[0]
                    num, num_old = (num_old ^ acc) & 0xFFFFFFFF, num

                num_old ^= struct.unpack("<I", keys[0:4])[0]
                clear.extend(struct.pack("<II", num_old & 0xFFFFFFFF, num))
                offset += 8

            clear.extend(raw[offset:offset + 5])
            offset += 5

            market, code, day, category, fenhong, peigujia, songzhuan, peigu = struct.unpack(
                "<B7sIBffff", clear
            )
            rows.append({
                "market": market,
                "code": code.rstrip(b"\x00").decode("ascii", "ignore"),
                "datetime": day,
                "category": category,
                "fenhong": fenhong,
                "peigu": peigu,
                "peigujia": peigujia,
                "songzhuan": songzhuan,
            })

        if not rows:
            return empty
        return pd.DataFrame(rows)


def split_events(events: pd.DataFrame, code: str) -> pd.DataFrame:
    """筛出某只股票的除权除息事件，按日期升序。"""
    if events is None or events.empty:
        return pd.DataFrame(columns=["datetime", "fenhong", "peigu", "peigujia", "songzhuan"])
    subset = events[(events["code"] == code) & (events["category"] == GBBQ_CATEGORY_XDXR)].copy()
    return subset.sort_values("datetime")


def apply_qfq(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """按除权除息事件计算前复权因子，返回新的 OHLC 序列（成交量还原为原始单位以免重复缩放）。

    前复权口径：最新交易日的价格为基准（因子 1），历史价格按除权比例向前缩放。
    除权参考价 = (前收盘 × 10 − 每股分红 + 配股价 × 配股比例) / (10 + 配股比例 + 送转比例)。
    """
    if frame is None or frame.empty or events is None or events.empty:
        return frame

    dates = frame.index.to_numpy()
    factor = pd.Series(1.0, index=frame.index)

    for _, event in events.iterrows():
        day = int(event["datetime"])
        earlier = dates < day
        if not earlier.any():
            continue
        prev_index = dates[earlier][-1]
        prev_close = float(frame.at[prev_index, "close"])
        if prev_close <= 0:
            continue

        fenhong = float(event["fenhong"] or 0.0)
        peigu = float(event["peigu"] or 0.0)
        peigujia = float(event["peigujia"] or 0.0)
        songzhuan = float(event["songzhuan"] or 0.0)

        # fenhong 为每 10 股派现（元），peigu/songzhuan 为每 10 股配股/送转（股）
        denominator = 10.0 + peigu + songzhuan
        if denominator <= 0:
            continue
        theoretical = (prev_close * 10.0 - fenhong + peigu * peigujia) / denominator
        if theoretical <= 0:
            continue

        factor.loc[factor.index < day] *= theoretical / prev_close

    adjusted = frame.copy()
    for column in ("open", "high", "low", "close"):
        if column in adjusted.columns:
            adjusted[column] = adjusted[column] * factor
    return adjusted


def resample_period(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """把日线聚合为周线/月线。"""
    if frame is None or frame.empty:
        return frame
    if period in (None, "day", "daily"):
        return frame

    rule = {"week": "W-FRI", "weekly": "W-FRI", "month": "ME", "monthly": "ME"}.get(period)
    if not rule:
        return frame

    indexed = frame.copy()
    indexed.index = pd.to_datetime(indexed.index.astype(str), format="%Y%m%d", errors="coerce")
    indexed = indexed[indexed.index.notna()]
    if indexed.empty:
        return frame

    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in indexed.columns:
        aggregation["volume"] = "sum"
    if "amount" in indexed.columns:
        aggregation["amount"] = "sum"
    aggregated = indexed.resample(rule).agg(aggregation).dropna(subset=["close"])
    aggregated.index = aggregated.index.strftime("%Y%m%d").astype(int)
    aggregated.index.name = "date"
    return aggregated


def normalize_date(value) -> str:
    """把各种日期写法统一为 YYYY-MM-DD。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip().split(" ")[0].split("T")[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text