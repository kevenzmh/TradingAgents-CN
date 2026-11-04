"""历史K线导入校验与写入衔接单测。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from app.pro.services import historical_import_service as svc


def _rec(**over):
    base = {"date": "2026-01-05", "open": 10, "high": 11, "low": 9.5, "close": 10.5, "volume": 1000, "amount": 10500}
    base.update(over)
    return base


class TestParseTradeDate:
    @pytest.mark.parametrize("raw", ["2026-01-05", "20260105", "2026/01/05", "2026.01.05",
                                     "2026-01-05 00:00:00", "2026-01-05T09:30:00"])
    def test_formats(self, raw):
        assert svc.parse_trade_date(raw) == "2026-01-05"

    def test_date_objects(self):
        assert svc.parse_trade_date(date(2026, 1, 5)) == "2026-01-05"

    @pytest.mark.parametrize("bad", [None, "", "abc", "2026-13-40"])
    def test_bad(self, bad):
        with pytest.raises(ValueError):
            svc.parse_trade_date(bad)

    def test_future_rejected(self):
        with pytest.raises(ValueError, match="未来"):
            svc.parse_trade_date((date.today() + timedelta(days=30)).isoformat())


class TestValidateRecord:
    def test_ok(self):
        out = svc.validate_record(_rec())
        assert out["date"] == "2026-01-05" and out["close"] == 10.5

    def test_trade_date_alias_and_vol_alias(self):
        out = svc.validate_record({"trade_date": "20260105", "close": 5, "vol": 7})
        assert out["date"] == "2026-01-05" and out["volume"] == 7

    def test_missing_ohl_filled_from_close(self):
        out = svc.validate_record({"date": "2026-01-05", "close": 8})
        assert out["open"] == out["high"] == out["low"] == 8

    @pytest.mark.parametrize("over,msg", [
        ({"close": 0}, "close"),
        ({"close": -1}, "close"),
        ({"close": "abc"}, "不是数字"),
        ({"high": 9, "low": 10}, "不能小于"),
        ({"close": 20}, "超过 high"),
        ({"close": 5}, "低于 low"),
        ({"volume": -1}, "不能为负"),
        ({"open": 0}, "open"),
        ({"close": float("nan")}, "非法"),
    ])
    def test_rejects(self, over, msg):
        with pytest.raises(ValueError, match=msg):
            svc.validate_record(_rec(**over))

    def test_non_dict(self):
        with pytest.raises(ValueError):
            svc.validate_record([1, 2])  # type: ignore[arg-type]


class TestPrepareFrame:
    def test_sorted_dedup_last_wins_and_errors(self):
        frame, errors, dups = svc.prepare_frame([
            _rec(date="2026-01-06", close=10.6),
            _rec(date="2026-01-05", close=10.0, open=10, high=10.5, low=9.8),
            _rec(date="2026-01-06", close=10.7),
            _rec(close=0),
        ])
        assert list(frame["date"]) == ["2026-01-05", "2026-01-06"]
        assert frame.loc[frame["date"] == "2026-01-06", "close"].iloc[0] == 10.7
        assert dups == 1 and len(errors) == 1 and errors[0]["index"] == 3

    def test_all_invalid_returns_none(self):
        frame, errors, _ = svc.prepare_frame([{"close": 0}, {}])
        assert frame is None and len(errors) == 2


class _FakeHistoricalService:
    def __init__(self):
        self.calls = []

    async def save_historical_data(self, **kwargs):
        self.calls.append(kwargs)
        return len(kwargs["data"])


@pytest.mark.asyncio
async def test_save_records_delegates_to_canonical_writer(monkeypatch):
    fake = _FakeHistoricalService()

    async def _get():
        return fake

    monkeypatch.setattr("app.services.historical_data_service.get_historical_data_service", _get)
    res = await svc.save_records("600519.SH", [_rec(), _rec(date="2026-01-06"), {"close": 0}],
                                 market="CN", period="daily", overwrite=True)
    assert res["saved"] == 2 and res["failed"] == 1 and res["symbol"] == "600519"
    call = fake.calls[0]
    assert call["data_source"] == "user_import"
    assert call["symbol"] == "600519" and call["period"] == "daily"
    assert isinstance(call["data"], pd.DataFrame)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,msg", [
    ({"symbol": ""}, "symbol"),
    ({"symbol": "600519", "market": "JP"}, "market"),
    ({"symbol": "600519", "period": "hourly"}, "period"),
])
async def test_save_records_param_validation(kwargs, msg):
    params = {"market": "CN", "period": "daily"}
    params.update(kwargs)
    symbol = params.pop("symbol")
    with pytest.raises(ValueError, match=msg):
        await svc.save_records(symbol, [_rec()], **params)


@pytest.mark.asyncio
async def test_all_invalid_never_touches_writer(monkeypatch):
    def boom():
        raise AssertionError("不应获取写入器")

    monkeypatch.setattr("app.services.historical_data_service.get_historical_data_service", boom)
    res = await svc.save_records("600519", [{"close": 0}])
    assert res["saved"] == 0 and res["failed"] == 1
