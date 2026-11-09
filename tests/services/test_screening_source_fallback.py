"""筛选取数的数据源回退：首选源在库里没有数据时不能返回空结果。

背景：通达信本地源只提供行情（代码/名称/K线），不提供估值与财务口径，
基础信息同步写不出可筛选的文档。数据源优先级说"想用 tdx"，但库里没有
tdx 文档，硬按 source=tdx 查询会让所有筛选条件返回 0。
"""

import asyncio
import sys
import types

# 本机测试环境未安装 motor；被测模块在 import 期就会用到它。
try:  # pragma: no cover - 装了 motor 的环境直接跳过
    import motor.motor_asyncio  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _motor = types.ModuleType("motor")
    _asyncio_mod = types.ModuleType("motor.motor_asyncio")

    class _Stub:  # minimal placeholders for type annotations
        pass

    _asyncio_mod.AsyncIOMotorDatabase = _Stub
    _asyncio_mod.AsyncIOMotorClient = _Stub
    _asyncio_mod.AsyncIOMotorCollection = _Stub
    _motor.motor_asyncio = _asyncio_mod
    sys.modules["motor"] = _motor
    sys.modules["motor.motor_asyncio"] = _asyncio_mod


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def __aiter__(self):
        for d in self._docs:
            yield d


class _FakeCollection:
    """用 (source -> docs) 的内存表模拟 stock_basic_info。"""

    def __init__(self, docs_by_source):
        self._docs_by_source = docs_by_source

    def _match(self, query):
        source = query.get("source")
        docs = self._docs_by_source.get(source, [])
        for field, cond in query.items():
            if field == "source":
                continue
            if not isinstance(cond, dict):
                continue
            filtered = []
            for doc in docs:
                value = doc.get(field)
                if value is None:
                    continue
                if "$in" in cond and value not in cond["$in"]:
                    continue
                if "$nin" in cond and value in cond["$nin"]:
                    continue
                if "$gt" in cond and not value > cond["$gt"]:
                    continue
                if "$gte" in cond and not value >= cond["$gte"]:
                    continue
                if "$lt" in cond and not value < cond["$lt"]:
                    continue
                if "$lte" in cond and not value <= cond["$lte"]:
                    continue
                filtered.append(doc)
            docs = filtered
        return docs

    async def find_one(self, query, *_a, **_k):
        docs = self._match(query)
        return docs[0] if docs else None

    async def count_documents(self, query):
        return len(self._match(query))

    def find(self, query, *_a, **_k):
        return _FakeCursor(self._match(query))

    async def distinct(self, field, query=None):
        if field != "source":
            return []
        if not query:
            return list(self._docs_by_source)
        return [s for s, docs in self._docs_by_source.items() if self._match({**query, "source": s})]


class _FakeDb:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]


def _patch_available_sources(monkeypatch, sources):
    import app.core.data_source_priority as mod

    async def _fake(market_category="a_shares"):
        return list(sources)

    monkeypatch.setattr(mod, "get_available_data_sources_async", _fake)


def test_preferred_source_without_docs_falls_back(monkeypatch):
    """首选 tdx 在 selected 表里没有匹配文档 → 回退到 akshare。"""
    from app.services.database_screening_service import DatabaseScreeningService

    _patch_available_sources(monkeypatch, ["tdx", "akshare", "baostock"])
    db = _FakeDb({
        "stock_basic_info": _FakeCollection({
            "akshare": [{"code": "600519", "pe": 25.0}, {"code": "000001", "pe": 5.2}],
            "baostock": [{"code": "600000", "pe": 6.3}],
        })
    })
    svc = DatabaseScreeningService()

    picked = asyncio.run(svc._resolve_effective_source(db, {"pe": {"$gt": 0, "$lt": 30}}))
    assert picked == "akshare"


def test_picks_highest_coverage_source(monkeypatch):
    """首选源有少量数据、其他源覆盖更广时，应选覆盖最广的，而不是第一个非空的。

    tdx 曾写出 5578 条只有代码名称的文档，其中 2 条凑巧带 total_mv；
    按"第一个非空"选源会只筛出 2 只，而 akshare 有 7 只。
    """
    from app.services.database_screening_service import DatabaseScreeningService

    _patch_available_sources(monkeypatch, ["tdx", "akshare", "baostock"])
    db = _FakeDb({
        "stock_basic_info": _FakeCollection({
            "tdx": [{"code": "300118", "total_mv": 117.0}, {"code": "301697", "total_mv": 96.0}],
            "akshare": [{"code": f"60000{i}", "total_mv": 100.0 + i} for i in range(7)],
        })
    })
    svc = DatabaseScreeningService()

    picked = asyncio.run(svc._resolve_effective_source(db, {"total_mv": {"$gt": 50}}))
    assert picked == "akshare"


def test_ties_fall_back_to_priority_order(monkeypatch):
    """命中数相同时保留优先级更高的源，不因遍历顺序漂移。"""
    from app.services.database_screening_service import DatabaseScreeningService

    _patch_available_sources(monkeypatch, ["tdx", "akshare"])
    db = _FakeDb({
        "stock_basic_info": _FakeCollection({
            "tdx": [{"code": "600519", "pe": 25.0}],
            "akshare": [{"code": "000001", "pe": 5.2}],
        })
    })
    svc = DatabaseScreeningService()

    picked = asyncio.run(svc._resolve_effective_source(db, {"pe": {"$gt": 0}}))
    assert picked == "tdx"


def test_preferred_source_with_docs_is_kept(monkeypatch):
    """首选源覆盖最广时不应改变选择。"""
    from app.services.database_screening_service import DatabaseScreeningService

    _patch_available_sources(monkeypatch, ["tdx", "akshare"])
    db = _FakeDb({
        "stock_basic_info": _FakeCollection({
            "tdx": [{"code": "600519", "pe": 25.0}, {"code": "600000", "pe": 6.3}],
            "akshare": [{"code": "000001", "pe": 5.2}],
        })
    })
    svc = DatabaseScreeningService()

    picked = asyncio.run(svc._resolve_effective_source(db, {"pe": {"$gt": 0}}))
    assert picked == "tdx"


def test_conditions_exclude_sources_lacking_the_field(monkeypatch):
    """tdx 有文档但缺 pe 字段时，仍要回退——否则筛选条件语义被悄悄忽略。"""
    from app.services.database_screening_service import DatabaseScreeningService

    _patch_available_sources(monkeypatch, ["tdx", "akshare"])
    db = _FakeDb({
        "stock_basic_info": _FakeCollection({
            "tdx": [{"code": "600519"}],
            "akshare": [{"code": "000001", "pe": 5.2}],
        })
    })
    svc = DatabaseScreeningService()

    picked = asyncio.run(svc._resolve_effective_source(db, {"pe": {"$gt": 0}}))
    assert picked == "akshare"


def test_pick_source_with_docs_prefers_source_covering_the_codes(monkeypatch):
    """富集阶段按实际代码挑源：首选源在这些代码下没有文档就换源。"""
    from app.services.database_screening_service import DatabaseScreeningService

    coll = _FakeCollection({
        "akshare": [{"code": "600519", "pe": 25.0}],
        "baostock": [{"code": "600000", "pe": 6.3}],
    })
    svc = DatabaseScreeningService()

    assert asyncio.run(svc._pick_source_with_docs(coll, ["600519"], "tdx")) == "akshare"
    assert asyncio.run(svc._pick_source_with_docs(coll, ["600000"], "tdx")) == "baostock"
    # 首选源确实覆盖这批代码时保留首选
    assert asyncio.run(svc._pick_source_with_docs(coll, ["600519"], "akshare")) == "akshare"


def test_local_offline_source_moves_to_front():
    """股票列表获取应先试离线本地源，避免在线源超时耗尽共享预算。"""
    from app.services.multi_source_basics_sync_service import MultiSourceBasicsSyncService

    assert MultiSourceBasicsSyncService._local_first(
        ["tushare", "akshare", "baostock", "tdx"]
    ) == ["tdx", "tushare", "akshare", "baostock"]
    # 没有本地源时顺序不变
    assert MultiSourceBasicsSyncService._local_first(["akshare", "baostock"]) == [
        "akshare",
        "baostock",
    ]
    assert MultiSourceBasicsSyncService._local_first(None) is None
