"""自选股分组 + 定时分析 服务单测（真实 MongoDB 一次性库，不可用时跳过）。"""

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId

from app.pro.routers import scheduled_analysis as sched_router
from app.pro.services import scheduled_analysis_service as sch
from app.pro.services import watchlist_group_service as wg

TZ = "Asia/Shanghai"


@pytest_asyncio.fixture
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.core.config import settings

    client = AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("MongoDB 不可用")
    name = f"ta_wg_test_{uuid.uuid4().hex[:8]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


UID, OTHER = "u-1", "u-2"


# ---------- 代码规整 ----------

class TestCodes:
    @pytest.mark.parametrize("raw,expected", [
        ("600519", "600519"), (" 000001.sz ", "000001"), ("600519.SH", "600519"), ("aapl", "AAPL"),
        ("0700.HK", "0700.HK"), ("BRK-B", "BRK-B"),
    ])
    def test_ok(self, raw, expected):
        assert wg.normalize_code(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", None, 5, "a b", "$bad", "x" * 20, "../etc"])
    def test_bad(self, raw):
        assert wg.normalize_code(raw) is None

    def test_dedup_and_invalid_split(self):
        codes, invalid = wg.normalize_codes(["600519", "600519.SH", "000001", "!!"])
        assert codes == ["600519", "000001"] and invalid == ["!!"]


# ---------- 分组 ----------

@pytest.mark.asyncio
async def test_group_crud_and_isolation(db):
    g = await wg.create_group(db, UID, name="科技股", stock_codes=["600519", "000001.SZ", "600519"])
    assert g["stock_codes"] == ["600519", "000001"] and g["sort_order"] == 0
    # 落库字段与助理工具/执行器约定一致：user_id 是字符串
    raw = await db[wg.COLLECTION].find_one({"_id": ObjectId(g["id"])})
    assert raw["user_id"] == UID and isinstance(raw["stock_codes"], list) and raw["is_active"] is True

    with pytest.raises(wg.GroupError) as ei:
        await wg.create_group(db, UID, name="科技股")
    assert ei.value.status_code == 409
    await wg.create_group(db, OTHER, name="科技股")  # 不同用户同名允许

    # 他人访问 → 404
    for call in (lambda: wg.update_group(db, OTHER, g["id"], name="x"), lambda: wg.delete_group(db, OTHER, g["id"]),
                 lambda: wg.add_stocks(db, OTHER, g["id"], ["600000"])):
        with pytest.raises(wg.GroupError) as ei:
            await call()
        assert ei.value.status_code == 404

    upd = await wg.update_group(db, UID, g["id"], name="新名称", analysis_depth=4, is_active=False)
    assert upd["name"] == "新名称" and upd["analysis_depth"] == 4 and upd["is_active"] is False
    cleared = await wg.update_group(db, UID, g["id"], analysis_depth=None)
    assert cleared["analysis_depth"] is None
    with pytest.raises(wg.GroupError, match="没有需要更新"):
        await wg.update_group(db, UID, g["id"])
    with pytest.raises(wg.GroupError, match="1-5"):
        await wg.update_group(db, UID, g["id"], analysis_depth=9)
    with pytest.raises(wg.GroupError, match="颜色"):
        await wg.update_group(db, UID, g["id"], color="red")


@pytest.mark.asyncio
async def test_add_remove_stocks_and_limits(db):
    g = await wg.create_group(db, UID, name="g")
    r = await wg.add_stocks(db, UID, g["id"], ["600519", "000001.SZ", "bad code", "600519"])
    assert r["added"] == ["600519", "000001"] and r["invalid"] == ["bad code"]
    again = await wg.add_stocks(db, UID, g["id"], ["600519"])
    assert again["added"] == [] and again["skipped"] == ["600519"]
    with pytest.raises(wg.GroupError, match="格式"):
        await wg.add_stocks(db, UID, g["id"], ["!!"])
    rm = await wg.remove_stocks(db, UID, g["id"], ["600519", "999999"])
    assert rm["removed"] == ["600519"] and rm["group"]["stock_codes"] == ["000001"]

    many = [f"{i:06d}" for i in range(wg.MAX_STOCKS_PER_GROUP)]
    await wg.add_stocks(db, UID, g["id"], many)
    with pytest.raises(wg.GroupError, match="最多"):
        await wg.add_stocks(db, UID, g["id"], ["888888"])


@pytest.mark.asyncio
async def test_import_favorites_and_order(db):
    await db.user_favorites.insert_one({"user_id": UID, "favorites": [
        {"stock_code": "600519", "tags": ["白酒"]}, {"stock_code": "000001", "tags": []}]})
    a = await wg.create_group(db, UID, name="a")
    b = await wg.create_group(db, UID, name="b")
    r = await wg.import_from_favorites(db, UID, a["id"], tag="白酒")
    assert r["added"] == ["600519"]
    with pytest.raises(wg.GroupError) as ei:
        await wg.import_from_favorites(db, UID, a["id"], tag="不存在")
    assert ei.value.status_code == 404

    await wg.reorder(db, UID, [b["id"], a["id"]])
    assert [g["name"] for g in await wg.list_groups(db, UID)] == ["b", "a"]
    with pytest.raises(wg.GroupError):
        await wg.reorder(db, UID, [b["id"], b["id"]])
    with pytest.raises(wg.GroupError):
        await wg.reorder(db, UID, [b["id"], str(ObjectId())])


@pytest.mark.asyncio
async def test_delete_blocked_when_used_by_schedule(db):
    g = await wg.create_group(db, UID, name="被引用")
    await db[wg.CONFIG_COLLECTION].insert_one({
        "user_id": UID, "name": "每日分析", "default_group_ids": [],
        "time_slots": [{"name": "s", "group_ids": [g["id"]]}]})
    with pytest.raises(wg.GroupError, match="每日分析") as ei:
        await wg.delete_group(db, UID, g["id"])
    assert ei.value.status_code == 409
    await db[wg.CONFIG_COLLECTION].delete_many({})
    await wg.delete_group(db, UID, g["id"])
    assert await wg.list_groups(db, UID) == []


@pytest.mark.asyncio
async def test_resolve_names(db):
    await db.stock_basic_info.insert_one({"code": "600519", "name": "贵州茅台"})
    await db.etf_basic_info.insert_one({"code": "510300", "name": "沪深300ETF"})
    assert await wg.resolve_names(db, ["600519", "510300", "123456"]) == {"600519": "贵州茅台", "510300": "沪深300ETF"}


# ---------- CRON / 配置 ----------

class TestCron:
    def test_valid_and_next(self):
        assert sch.validate_cron("0  9 * * 1-5", TZ) == "0 9 * * 1-5"
        times = sch.next_fire_times("0 9 * * 1-5", TZ, 3)
        assert len(times) == 3 and all(t.hour == 9 and t.weekday() < 5 for t in times)

    @pytest.mark.parametrize("expr,msg", [
        ("", "填写"), ("0 9 * *", "5 段"), ("0 9 * * * *", "5 段"), ("61 9 * * *", "无效"), ("abc", "5 段"),
        ("*/5 * * * *", "最小间隔"), ("0,10 9 * * *", "最小间隔"),
    ])
    def test_invalid(self, expr, msg):
        with pytest.raises(sch.ScheduleError, match=msg):
            sch.validate_cron(expr, TZ)

    @pytest.mark.parametrize("expr,expected_weekdays", [
        ("0 9 * * 1-5", {0, 1, 2, 3, 4}),      # 周一到周五（Python weekday: 周一=0）
        ("0 9 * * 0", {6}), ("0 9 * * 7", {6}),   # 周日
        ("0 9 * * 6", {5}), ("0 9 * * 1,3,5", {0, 2, 4}), ("0 9 * * 0-6", set(range(7))),
        ("0 9 * * 5-7", {4, 5, 6}), ("0 9 * * */2", {6, 1, 3, 5}), ("0 9 * * mon-fri", {0, 1, 2, 3, 4}),
        ("0 9 * * *", set(range(7))),
    ])
    def test_weekday_semantics_are_standard_cron(self, expr, expected_weekdays):
        """回归：APScheduler 原生 from_crontab 把 1-5 当成周二到周六。"""
        times = sch.next_fire_times(expr, TZ, 14)
        assert {t.weekday() for t in times} == expected_weekdays

    @pytest.mark.parametrize("expr,msg", [
        ("0 9 1 * 1", "同时限定"), ("0 9 * * 8", "范围"), ("0 9 * * 5-1", "范围"), ("0 9 * * a-", "无效"),
        ("0 9 * * 1/0", "步长"),
    ])
    def test_weekday_rejects(self, expr, msg):
        with pytest.raises(sch.ScheduleError, match=msg):
            sch.validate_cron(expr, TZ)

    def test_min_interval_boundary(self):
        assert sch.validate_cron("*/30 * * * *", TZ) == "*/30 * * * *"


def _payload(group_id, **kw):
    base = {"name": "每日早盘", "time_slots": [{"name": "开盘前", "cron_expression": "30 8 * * 1-5",
                                              "group_ids": [group_id], "analysis_depth": 3}]}
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_normalize_config(db):
    g = await wg.create_group(db, UID, name="g")
    out = await sch.normalize_config(db, UID, _payload(g["id"]), TZ)
    assert out["time_slots"][0]["group_ids"] == [g["id"]] and out["send_email"] is False

    # 他人的分组 / 不存在的分组
    other = await wg.create_group(db, OTHER, name="x")
    with pytest.raises(sch.ScheduleError, match="不存在的分组"):
        await sch.normalize_config(db, UID, _payload(other["id"]), TZ)
    with pytest.raises(sch.ScheduleError, match="无效"):
        await sch.normalize_config(db, UID, _payload("nope"), TZ)
    # 没有任何分组
    with pytest.raises(sch.ScheduleError, match="没有选择分组"):
        await sch.normalize_config(db, UID, _payload(g["id"], time_slots=[
            {"name": "s", "cron_expression": "0 9 * * *", "group_ids": []}]), TZ)
    # 默认分组兜底即可
    ok = await sch.normalize_config(db, UID, _payload(g["id"], default_group_ids=[g["id"]], time_slots=[
        {"name": "s", "cron_expression": "0 9 * * *", "group_ids": []}]), TZ)
    assert ok["time_slots"][0]["group_ids"] == []
    # 时间段名重复、过多、CRON 错误带时间段名
    dup = [{"name": "a", "cron_expression": "0 9 * * *", "group_ids": [g["id"]]}] * 2
    with pytest.raises(sch.ScheduleError, match="重复"):
        await sch.normalize_config(db, UID, _payload(g["id"], time_slots=dup), TZ)
    with pytest.raises(sch.ScheduleError, match="时间段「开盘前」"):
        await sch.normalize_config(db, UID, _payload(g["id"], time_slots=[
            {"name": "开盘前", "cron_expression": "* * * * *", "group_ids": [g["id"]]}]), TZ)
    with pytest.raises(sch.ScheduleError, match="分析深度"):
        await sch.normalize_config(db, UID, _payload(g["id"], default_analysis_depth=7), TZ)


@pytest.mark.asyncio
async def test_workflow_slot_preserved_on_update(db):
    g = await wg.create_group(db, UID, name="g")
    old = [{"name": "研究流", "cron_expression": "0 9 * * *", "workflow_id": "wf1",
            "workflow_params": {"analysis_target": "600519"}}]
    payload = _payload(g["id"], time_slots=[{"name": "研究流", "cron_expression": "0 10 * * *", "group_ids": []}])
    out = await sch.normalize_config(db, UID, payload, TZ, existing_slots=old)
    slot = out["time_slots"][0]
    assert slot["workflow_id"] == "wf1" and slot["workflow_params"]["analysis_target"] == "600519"
    assert slot["cron_expression"] == "0 10 * * *"


# ---------- 调度注册 ----------

@pytest.mark.asyncio
async def test_register_and_unregister_jobs(monkeypatch):
    scheduler = AsyncIOScheduler(timezone=TZ)
    monkeypatch.setattr(sched_router, "_scheduler", lambda: scheduler)
    cfg = {"id": "c1", "user_id": "u-1", "name": "n", "enabled": True, "time_slots": [
        {"name": "a", "cron_expression": "0 9 * * *", "enabled": True},
        {"name": "b", "cron_expression": "0 15 * * *", "enabled": False},
        {"name": "c", "cron_expression": "bad cron", "enabled": True},
        {"name": "d", "cron_expression": "30 20 * * *", "enabled": True},
    ]}
    ids = await sched_router.register_config(cfg)
    assert ids == ["scheduled_analysis:c1:0", "scheduled_analysis:c1:3"]  # 下标即 slot_index，跳过停用/无效
    job = scheduler.get_job("scheduled_analysis:c1:3")
    assert list(job.args) == ["c1", "u-1", 3]
    assert job.func.__name__ == "run_scheduled_analysis_slot"

    # 重新注册会先清空旧任务（避免删掉的时间段残留）
    cfg["time_slots"] = cfg["time_slots"][:1]
    assert await sched_router.register_config(cfg) == ["scheduled_analysis:c1:0"]
    assert [j.id for j in scheduler.get_jobs()] == ["scheduled_analysis:c1:0"]

    # 另一个配置不受影响；停用即清空
    await sched_router.register_config({**cfg, "id": "c2"})
    cfg["enabled"] = False
    assert await sched_router.register_config(cfg) == []
    assert [j.id for j in scheduler.get_jobs()] == ["scheduled_analysis:c2:0"]
    assert await sched_router.unregister_config("c2") == 1
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_legacy_tool_entrypoints(monkeypatch):
    """AI 助理工具通过旧名称注册：带 _id 的文档与 id 字段都要能挂上。"""
    scheduler = AsyncIOScheduler(timezone=TZ)
    monkeypatch.setattr(sched_router, "_scheduler", lambda: scheduler)
    await sched_router._register_scheduled_tasks({"_id": ObjectId("65a000000000000000000001"), "user_id": "u",
                                                  "name": "t", "enabled": True,
                                                  "time_slots": [{"name": "x", "cron_expression": "0 9 * * 1-5"}]})
    assert len(scheduler.get_jobs()) == 1
    await sched_router._unregister_scheduled_tasks("65a000000000000000000001")
    assert scheduler.get_jobs() == []
