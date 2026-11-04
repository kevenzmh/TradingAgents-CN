"""社媒消息导入服务单测：规范化、规则分析、幂等落库计数。

重点锁住消费方的数据契约（stock_sentiment / mongodb_cache_adapter）：
- sentiment 只能是 positive/negative/neutral
- publish_time 必须是 UTC 无时区 datetime
- symbol 为规范化代码
"""

from datetime import datetime, timedelta

import pytest

from app.pro.services import social_media_service as svc


# ---------- 代码与时间 ----------

class TestSymbolAndTime:
    @pytest.mark.parametrize("raw,market,expected", [
        ("600519", "CN", "600519"),
        ("600519.SH", "CN", "600519"),
        ("000001.sz", "CN", "000001"),
        ("1", "CN", "000001"),
        ("0700.HK", "HK", "0700"),
        ("aapl", "US", "AAPL"),
        ("", "CN", ""),
    ])
    def test_normalize_symbol(self, raw, market, expected):
        assert svc.normalize_symbol(raw, market) == expected

    def test_naive_time_is_beijing(self):
        # 北京时间 20:00 == UTC 12:00
        dt = svc.parse_publish_time("2026-01-05 20:00:00")
        assert dt == datetime(2026, 1, 5, 12, 0, 0)
        assert dt.tzinfo is None

    def test_aware_time_converted_to_utc(self):
        assert svc.parse_publish_time("2026-01-05T12:00:00Z") == datetime(2026, 1, 5, 12, 0, 0)
        assert svc.parse_publish_time("2026-01-05T20:00:00+08:00") == datetime(2026, 1, 5, 12, 0, 0)

    def test_epoch_seconds_and_millis(self):
        assert svc.parse_publish_time(1767614400) == svc.parse_publish_time(1767614400000)

    @pytest.mark.parametrize("bad", [None, "", "not-a-time", 0, -5])
    def test_bad_time_rejected(self, bad):
        with pytest.raises(ValueError):
            svc.parse_publish_time(bad)

    def test_future_time_rejected(self):
        future = (datetime.utcnow() + timedelta(days=30)).isoformat()
        with pytest.raises(ValueError, match="未来"):
            svc.parse_publish_time(future + "Z")


# ---------- 规则情绪 ----------

class TestSentiment:
    def test_positive(self):
        label, score = svc.analyze_sentiment("业绩超预期，利好，看涨")
        assert label == "positive" and score > 0

    def test_negative(self):
        label, score = svc.analyze_sentiment("暴雷了，利空，大跌")
        assert label == "negative" and score < 0

    def test_neutral_when_no_hits(self):
        assert svc.analyze_sentiment("今天开会讨论了日程") == ("neutral", 0.0)

    def test_negation_flips(self):
        assert svc.analyze_sentiment("不看好后市")[0] == "negative"
        assert svc.analyze_sentiment("没有风险")[0] == "positive"

    @pytest.mark.parametrize("text", ["未来上涨空间大", "股价不断上涨", "无论如何都看涨"])
    def test_non_negating_compounds_not_flipped(self, text):
        assert svc.analyze_sentiment(text)[0] == "positive"

    def test_label_always_one_of_three(self):
        for text in ["", "abc", "涨停跌停", "不不不", "利好利空"]:
            assert svc.analyze_sentiment(text)[0] in ("positive", "negative", "neutral")

    @pytest.mark.parametrize("given,expected", [
        ("positive", "positive"), ("BULLISH", "positive"), ("利好", "positive"),
        ("negative", "negative"), ("看跌", "negative"),
        ("neutral", "neutral"), ("中性", "neutral"),
        ("weird", None), (None, None),
    ])
    def test_normalize_sentiment_aliases(self, given, expected):
        assert svc.normalize_sentiment(given) == expected


class TestKeywordsAndImportance:
    def test_hashtags_both_styles(self):
        tags = svc.extract_hashtags("看 #贵州茅台# 的 #业绩 走势")
        assert "贵州茅台" in tags and "业绩" in tags

    def test_keywords_hashtag_first_then_lexicon(self):
        kws = svc.extract_keywords("财报利好，业绩超预期", ["茅台"])
        assert kws[0] == "茅台"
        assert "财报" in kws and "业绩" in kws

    def test_importance_scales_with_reach(self):
        low = svc.compute_importance({}, {"views": 10, "likes": 0, "shares": 0, "comments": 0})
        high = svc.compute_importance(
            {"followers_count": 2_000_000, "verified": True},
            {"views": 500_000, "likes": 20_000, "shares": 5_000, "comments": 8_000},
        )
        assert low[0] == "low"
        assert high[0] == "high"
        assert high[1] > low[1]
        assert 0 <= low[1] <= 100 and 0 <= high[1] <= 100


# ---------- 整条规范化 ----------

def _raw(**over):
    base = {
        "platform": "XueQiu", "symbol": "600519.SH",
        "content": "贵州茅台业绩超预期，利好 #茅台#",
        "publish_time": "2026-01-05 10:30:00",
    }
    base.update(over)
    return base


class TestNormalizeMessage:
    def test_contract_fields(self):
        doc = svc.normalize_message(_raw(), "u1")
        assert doc["platform"] == "xueqiu"
        assert doc["symbol"] == "600519"
        assert isinstance(doc["publish_time"], datetime) and doc["publish_time"].tzinfo is None
        assert doc["sentiment"] in ("positive", "negative", "neutral")
        assert isinstance(doc["keywords"], list) and isinstance(doc["hashtags"], list)
        assert set(doc["engagement"]) >= {"views", "likes", "shares", "comments", "engagement_rate"}
        assert doc["imported_by"] == "u1"
        assert doc["sentiment_source"] == "auto"

    def test_provided_sentiment_wins_and_is_normalized(self):
        doc = svc.normalize_message(_raw(sentiment="利空"))
        assert doc["sentiment"] == "negative" and doc["sentiment_source"] == "provided"

    def test_unknown_sentiment_falls_back_to_auto(self):
        doc = svc.normalize_message(_raw(sentiment="???"))
        assert doc["sentiment"] in ("positive", "negative", "neutral")
        assert doc["sentiment_source"] == "auto"

    def test_engagement_rate_computed(self):
        doc = svc.normalize_message(_raw(engagement={"views": 1000, "likes": 30, "shares": 10, "comments": 10}))
        assert doc["engagement"]["engagement_rate"] == 5.0

    def test_message_id_stable_and_content_sensitive(self):
        a = svc.normalize_message(_raw())["message_id"]
        b = svc.normalize_message(_raw())["message_id"]
        c = svc.normalize_message(_raw(content="完全不同的内容"))["message_id"]
        assert a == b and a != c

    def test_provided_message_id_used(self):
        assert svc.normalize_message(_raw(message_id="abc-1"))["message_id"] == "abc-1"

    @pytest.mark.parametrize("field", ["platform", "symbol", "content", "publish_time"])
    def test_missing_required_rejected(self, field):
        with pytest.raises(ValueError):
            svc.normalize_message(_raw(**{field: ""}))

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError):
            svc.normalize_message("nope")  # type: ignore[arg-type]

    def test_overlong_content_rejected(self):
        with pytest.raises(ValueError, match="过长"):
            svc.normalize_message(_raw(content="a" * (svc.MAX_CONTENT_LEN + 1)))


class TestPrepareBatch:
    def test_errors_and_duplicates_separated(self):
        batch = [_raw(), _raw(), _raw(content=""), _raw(symbol="000001", content="另一条")]
        docs, errors, dups = svc.prepare_batch(batch)
        assert len(docs) == 2 and dups == 1
        assert len(errors) == 1 and errors[0]["index"] == 2


# ---------- 落库计数（FakeDB） ----------

class _FakeBulkResult:
    def __init__(self, upserted, modified):
        self.upserted_count = upserted
        self.modified_count = modified


class _FakeCollection:
    def __init__(self):
        self.store = {}
        self.indexes = []

    async def create_index(self, *a, **k):
        self.indexes.append((a, k))

    async def bulk_write(self, ops, ordered=False):
        upserted = modified = 0
        for op in ops:
            flt = op._filter
            key = (flt["platform"], flt["message_id"])
            update = op._doc
            if key in self.store:
                if "$set" in update:
                    self.store[key].update(update["$set"])
                    modified += 1
            else:
                doc = dict(update.get("$setOnInsert", {}))
                doc.update(update.get("$set", {}))
                self.store[key] = doc
                upserted += 1
        return _FakeBulkResult(upserted, modified)


class _FakeDB:
    def __init__(self):
        self.coll = _FakeCollection()

    def __getitem__(self, name):
        assert name == svc.COLLECTION
        return self.coll


@pytest.mark.asyncio
async def test_save_is_idempotent_without_overwrite():
    svc._indexes_ready = False
    db = _FakeDB()
    batch = [_raw(), _raw(symbol="000001", content="平安银行分红")]
    first = await svc.save_messages(db, batch, overwrite=False, user_id="u1")
    assert (first["saved"], first["updated"], first["skipped"], first["failed"]) == (2, 0, 0, 0)

    again = await svc.save_messages(db, batch, overwrite=False, user_id="u1")
    assert (again["saved"], again["updated"], again["skipped"]) == (0, 0, 2)
    assert len(db.coll.store) == 2
    assert db.coll.indexes, "应已建立索引"


@pytest.mark.asyncio
async def test_overwrite_updates_existing():
    svc._indexes_ready = False
    db = _FakeDB()
    await svc.save_messages(db, [_raw()], overwrite=False)
    res = await svc.save_messages(db, [_raw(sentiment="negative")], overwrite=True)
    assert res["updated"] == 1 and res["saved"] == 0
    assert next(iter(db.coll.store.values()))["sentiment"] == "negative"


@pytest.mark.asyncio
async def test_invalid_rows_reported_not_fatal():
    svc._indexes_ready = False
    db = _FakeDB()
    res = await svc.save_messages(db, [_raw(), {"platform": "x"}], overwrite=False)
    assert res["saved"] == 1 and res["failed"] == 1
    assert res["errors"][0]["index"] == 1
