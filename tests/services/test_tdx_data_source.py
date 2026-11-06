"""通达信本地数据源：二进制解析与适配器行为。

用临时目录构造最小的 vipdoc 结构，不依赖本机是否安装通达信。
"""

import struct
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.services.data_sources import tdx_reader  # noqa: E402
from app.services.data_sources.tdx_adapter import TDXAdapter  # noqa: E402


def _write_day(path: Path, bars):
    """bars: [(yyyymmdd, open, high, low, close, amount, volume_lots)]"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b""
    for day, op, hi, lo, cl, amount, volume in bars:
        payload += struct.pack(
            "<IIIIIfII",
            day,
            int(round(op * 100)),
            int(round(hi * 100)),
            int(round(lo * 100)),
            int(round(cl * 100)),
            float(amount),
            int(round(volume * 100)),
            0,
        )
    path.write_bytes(payload)


def _write_tnf(path: Path, entries):
    """entries: [(code, name)]，按通达信 360 字节/条的真实布局。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"0" * tdx_reader.TNF_HEADER_SIZE
    for code, name in entries:
        record = bytearray(tdx_reader.TNF_RECORD_SIZE)
        record[0:6] = code.encode("ascii").ljust(6, b"\x00")
        encoded = name.encode("gbk")
        record[31:31 + len(encoded)] = encoded
        payload += bytes(record)
    path.write_bytes(payload)


@pytest.fixture
def tdx_root(tmp_path: Path) -> Path:
    _write_day(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh600519.day",
        [
            (20260105, 10.0, 10.5, 9.8, 10.2, 1.0e8, 1000),
            (20260106, 10.2, 11.0, 10.1, 10.9, 1.2e8, 1200),
            (20260107, 10.9, 11.2, 10.5, 10.6, 9.0e7, 800),
        ],
    )
    _write_day(
        tmp_path / "vipdoc" / "sz" / "lday" / "sz000001.day",
        [(20260106, 5.0, 5.2, 4.9, 5.1, 5.0e7, 500)],
    )
    # 指数与 ETF 不应出现在股票列表里
    _write_day(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh000001.day",
        [(20260106, 3000.0, 3010.0, 2990.0, 3005.0, 1.0e9, 10000)],
    )
    _write_day(
        tmp_path / "vipdoc" / "sh" / "lday" / "sh510050.day",
        [(20260106, 2.5, 2.6, 2.4, 2.55, 1.0e7, 200)],
    )
    _write_tnf(
        tmp_path / "T0002" / "hq_cache" / "shs.tnf",
        [("600519", "贵州茅台"), ("000001", "上证指数"), ("510050", "上证50ETF华夏")],
    )
    _write_tnf(
        tmp_path / "T0002" / "hq_cache" / "szs.tnf",
        [("000001", "平安银行")],
    )
    return tmp_path


def test_read_day_frame_converts_price_and_volume_units(tdx_root: Path):
    frame = tdx_reader.read_day_frame(
        tdx_reader.day_file_path(str(tdx_root), "sh", "600519")
    )

    assert list(frame.index) == [20260105, 20260106, 20260107]
    assert frame.at[20260106, "open"] == pytest.approx(10.2)
    assert frame.at[20260106, "close"] == pytest.approx(10.9)
    # 通达信存股数；系统内统一按「手」落库
    assert frame.at[20260106, "volume"] == pytest.approx(1200)
    assert frame.at[20260106, "amount"] == pytest.approx(1.2e8)


def test_parse_symbol_names_uses_gbk_name_table(tdx_root: Path):
    names = tdx_reader.parse_symbol_names(str(tdx_root))

    assert names[("sh", "600519")] == "贵州茅台"
    # 同一代码在不同交易所是两个证券，不能互相覆盖
    assert names[("sh", "000001")] == "上证指数"
    assert names[("sz", "000001")] == "平安银行"


def test_list_symbols_filters_index_and_etf(tdx_root: Path):
    symbols = tdx_reader.list_symbols(str(tdx_root))

    assert ("sh", "600519") in symbols
    assert ("sz", "000001") in symbols
    assert all(code != "510050" for _, code in symbols)
    assert not any(market == "sh" and code == "000001" for market, code in symbols)


def test_adapter_availability_requires_vipdoc(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TDX_DATA_DIR", str(tmp_path / "not-installed"))
    monkeypatch.setattr(TDXAdapter, "_available_cache", {}, raising=False)
    monkeypatch.setattr(TDXAdapter, "_available_checked_at", {}, raising=False)

    assert TDXAdapter().is_available() is False


def test_adapter_reports_stock_list_and_kline(tdx_root: Path, monkeypatch):
    monkeypatch.setenv("TDX_DATA_DIR", str(tdx_root))
    monkeypatch.setattr(TDXAdapter, "_available_cache", {}, raising=False)
    monkeypatch.setattr(TDXAdapter, "_available_checked_at", {}, raising=False)

    adapter = TDXAdapter()
    assert adapter.is_available() is True

    listing = adapter.get_stock_list()
    assert listing is not None
    assert set(listing["symbol"]) == {"600519", "000001"}
    assert "贵州茅台" in set(listing["name"])

    items = adapter.get_kline("600519", limit=2)
    assert [item["time"] for item in items] == ["2026-01-06", "2026-01-07"]
    assert items[-1]["close"] == pytest.approx(10.6)


def test_adapter_latest_trade_date_is_mode_across_market(tdx_root: Path, monkeypatch):
    monkeypatch.setenv("TDX_DATA_DIR", str(tdx_root))
    monkeypatch.setattr(TDXAdapter, "_available_cache", {}, raising=False)
    monkeypatch.setattr(TDXAdapter, "_available_checked_at", {}, raising=False)

    # 600519 到 0107，000001 到 0106：众数都在 0106，取 0106
    assert TDXAdapter().find_latest_trade_date() == "20260106"


def test_adapter_skips_adj_without_dividend_events(tdx_root: Path, monkeypatch):
    monkeypatch.setenv("TDX_DATA_DIR", str(tdx_root))
    monkeypatch.setattr(TDXAdapter, "_available_cache", {}, raising=False)
    monkeypatch.setattr(TDXAdapter, "_available_checked_at", {}, raising=False)

    plain = TDXAdapter().get_kline("600519", limit=3, adj="qfq")

    assert [item["close"] for item in plain] == pytest.approx([10.2, 10.9, 10.6])


def test_apply_qfq_scales_history_and_pins_latest():
    frame = pd.DataFrame(
        {"open": [10.0, 10.0, 10.0], "high": [10.0, 10.0, 10.0],
         "low": [10.0, 10.0, 10.0], "close": [10.0, 10.0, 10.0],
         "amount": [1.0, 1.0, 1.0], "volume": [1.0, 1.0, 1.0]},
        index=pd.Index([20260105, 20260106, 20260107], name="date"),
    )
    # 0106 每 10 股派 5 元 -> 除权参考价 9.5，因子 0.95
    events = pd.DataFrame([{
        "datetime": 20260106, "category": 1,
        "fenhong": 5.0, "peigu": 0.0, "peigujia": 0.0, "songzhuan": 0.0,
    }])

    adjusted = tdx_reader.apply_qfq(frame, events)

    assert adjusted.at[20260105, "close"] == pytest.approx(9.5)
    assert adjusted.at[20260106, "close"] == pytest.approx(10.0)
    assert adjusted.at[20260107, "close"] == pytest.approx(10.0)


def test_apply_qfq_handles_songzhuan():
    frame = pd.DataFrame(
        {"open": [10.0, 10.0], "high": [10.0, 10.0], "low": [10.0, 10.0],
         "close": [10.0, 10.0], "amount": [1.0, 1.0], "volume": [1.0, 1.0]},
        index=pd.Index([20260105, 20260106], name="date"),
    )
    # 每 10 股送 10 股 -> 理论除权价 5 元，因子 0.5
    events = pd.DataFrame([{
        "datetime": 20260106, "category": 1,
        "fenhong": 0.0, "peigu": 0.0, "peigujia": 0.0, "songzhuan": 10.0,
    }])

    adjusted = tdx_reader.apply_qfq(frame, events)

    assert adjusted.at[20260105, "close"] == pytest.approx(5.0)
    assert adjusted.at[20260106, "close"] == pytest.approx(10.0)


def test_resample_period_builds_weekly_bars(tdx_root: Path):
    frame = tdx_reader.read_day_frame(
        tdx_reader.day_file_path(str(tdx_root), "sh", "600519")
    )

    weekly = tdx_reader.resample_period(frame, "week")

    assert len(weekly) == 1
    assert weekly.iloc[0]["open"] == pytest.approx(10.0)
    assert weekly.iloc[0]["close"] == pytest.approx(10.6)
    assert weekly.iloc[0]["volume"] == pytest.approx(3000)


def test_normalize_date_accepts_multiple_formats():
    assert tdx_reader.normalize_date(20260105) == "2026-01-05"
    assert tdx_reader.normalize_date("20260105") == "2026-01-05"
    assert tdx_reader.normalize_date("2026-01-05 00:00:00") == "2026-01-05"


def test_availability_cache_is_keyed_by_directory(tmp_path: Path, monkeypatch):
    """换目录后必须重新探测，否则界面改好路径仍会拿到上一次的 False。"""
    TDXAdapter._available_cache = {}
    TDXAdapter._available_checked_at = {}

    missing = tmp_path / "not-installed"
    monkeypatch.setenv("TDX_DATA_DIR", str(missing))
    assert TDXAdapter().is_available() is False

    installed = tmp_path / "installed"
    (installed / "vipdoc" / "sh" / "lday").mkdir(parents=True)
    monkeypatch.setenv("TDX_DATA_DIR", str(installed))
    assert TDXAdapter().is_available() is True

    monkeypatch.setenv("TDX_DATA_DIR", str(missing))
    assert TDXAdapter().is_available() is False


def test_resolves_directory_from_datasource_config(tdx_root: Path, monkeypatch):
    """界面里配置的 data_dir 必须能读到。

    DataSourceConfig.type 是 DataSourceType 枚举，str() 出来是
    "DataSourceType.TDX"，直接和 "tdx" 比较会永远不相等——这正是
    「已经填了目录，页面仍显示不可用」的原因。
    """
    from app.core.unified_config import UnifiedConfigManager
    from app.models.config import DataSourceConfig, DataSourceType

    monkeypatch.delenv("TDX_DATA_DIR", raising=False)
    monkeypatch.setattr(
        "app.core.config.settings.TDX_DATA_DIR", "", raising=False
    )
    monkeypatch.setattr(
        UnifiedConfigManager,
        "get_data_source_configs",
        lambda self: [
            DataSourceConfig(
                name="tdx",
                type=DataSourceType.TDX,
                enabled=True,
                config_params={"data_dir": str(tdx_root)},
            )
        ],
    )
    TDXAdapter._available_cache = {}
    TDXAdapter._available_checked_at = {}

    adapter = TDXAdapter()

    assert adapter._resolve_dir() == str(tdx_root)
    assert adapter.is_available() is True


def test_provider_outputs_qfq_with_pre_close(tdx_root: Path, monkeypatch):
    """机构链路按前复权落库，且需要 pre_close 供入库时算 pct_chg。"""
    import asyncio

    monkeypatch.setenv("TDX_DATA_DIR", str(tdx_root))
    TDXAdapter._available_cache = {}
    TDXAdapter._available_checked_at = {}

    from tradingagents.dataflows.providers.china.tdx import get_tdx_provider

    provider = get_tdx_provider()
    frame = asyncio.run(
        provider.get_historical_data("600519", "20260105", "20260107", "daily")
    )

    assert frame is not None and len(frame) == 3
    assert "pre_close" in frame.columns
    # 第一根没有前收，其余等于上一根的收盘
    assert pd.isna(frame["pre_close"].iloc[0])
    assert frame["pre_close"].iloc[1] == pytest.approx(frame["close"].iloc[0])
    assert frame["pre_close"].iloc[2] == pytest.approx(frame["close"].iloc[1])


def test_analysis_chain_knows_tdx():
    """主分析降级链必须能认出 tdx，否则配置了也永远走不到。"""
    from tradingagents.dataflows.data_source_manager import ChinaDataSource

    assert "tdx" in {member.value for member in ChinaDataSource}
