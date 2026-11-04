"""提示词模板服务单测。

纯函数部分直接测；落库部分用真实 MongoDB 的一次性库（用完即删），Mongo 不可用时自动跳过——
$and/$or/$regex 这类查询算子用手写 fake 容易与真实行为不一致，不值得 mock。
"""

import uuid

import pytest
import pytest_asyncio
from bson import ObjectId

from app.pro.services import prompt_template_service as svc

CONTENT = {"system_prompt": "你是{market_type}分析师，分析 {{trade.code}}", "output_format": "Markdown"}


# ---------- 纯函数 ----------

class TestNormalize:
    def test_content_trimmed_and_filled(self):
        out = svc.normalize_content({"system_prompt": "  hi  "})
        assert out["system_prompt"] == "hi"
        assert set(out) == set(svc.CONTENT_FIELDS)
        assert out["user_prompt"] == ""

    def test_requires_a_prompt(self):
        with pytest.raises(svc.TemplateError, match="至少填写一个"):
            svc.normalize_content({"output_format": "x"})

    def test_unknown_field_rejected(self):
        with pytest.raises(svc.TemplateError, match="不支持"):
            svc.normalize_content({"system_prompt": "x", "evil": "y"})

    def test_non_string_rejected(self):
        with pytest.raises(svc.TemplateError, match="必须是字符串"):
            svc.normalize_content({"system_prompt": 5})

    def test_too_long_rejected(self):
        with pytest.raises(svc.TemplateError, match="上限"):
            svc.normalize_content({"system_prompt": "x" * (svc.MAX_FIELD_LEN + 1)})

    def test_name_rules(self):
        assert svc.normalize_name("  好模板 ") == "好模板"
        for bad in ("", "   ", None, "x" * (svc.MAX_NAME_LEN + 1)):
            with pytest.raises(svc.TemplateError):
                svc.normalize_name(bad)

    def test_bad_object_id(self):
        with pytest.raises(svc.TemplateError) as ei:
            svc.to_object_id("not-an-id")
        assert ei.value.status_code == 400


class TestVariables:
    def test_extract_both_syntaxes_dedup_ordered(self):
        content = {"system_prompt": "{a} {{b.c}} {a} {{ d }}", "user_prompt": "{e} {{b.c}}"}
        got = svc.extract_variables(content)
        assert set(got) == {"a", "b.c", "d", "e"} and len(got) == 4

    def test_json_braces_not_variables(self):
        # 含空格/引号的花括号（如 JSON 示例）不是变量，运行时也不会替换
        assert svc.extract_variables({"system_prompt": '输出 {"a": 1} 与 { x }'}) == []

    def test_double_brace_inner_not_double_counted(self):
        assert svc.extract_variables({"system_prompt": "{{abc}}"}) == ["abc"]

    def test_render_matches_runtime_format_template(self):
        """预览必须与运行时 TemplateClient.format_template 逐字一致。"""
        from tradingagents.utils.template_client import TemplateClient

        runtime = object.__new__(TemplateClient)  # 不触发 __init__（会连接 Mongo）
        variables = {"market_type": "A股", "trade": {"code": "600519"}, "ticker": None}
        content = {**{f: "" for f in svc.CONTENT_FIELDS},
                   "system_prompt": "市场 {market_type} 代码 {{trade.code}} 空 {ticker} 缺 {nope} 嵌套 {{trade.missing}} {analysis_context_block}",
                   "user_prompt": "{{ trade.code }}"}
        expected = runtime.format_template(dict(content), variables)
        got = svc.render_preview(content, variables)["rendered"]
        for field in svc.CONTENT_FIELDS:
            assert got[field] == expected[field], field

    def test_render_reports_empty_variables(self):
        res = svc.render_preview({"system_prompt": "{a}{b}{task_block}"}, {"a": "x"})
        assert res["rendered"]["system_prompt"] == "x"
        assert res["empty_variables"] == ["b"]  # 内置分层变量允许为空，不提示


# ---------- 落库（真实 Mongo 一次性库） ----------

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
    name = f"ta_pt_test_{uuid.uuid4().hex[:8]}"
    database = client[name]
    try:
        yield database
    finally:
        await client.drop_database(name)
        client.close()


@pytest_asyncio.fixture
async def seeded(db):
    """一个系统模板 + 两个用户"""
    sys_id = (await db[svc.TEMPLATE_COLLECTION].insert_one({
        "agent_type": "analysts_v2", "agent_name": "market_analyst_v2", "template_name": "系统默认-市场分析师",
        "content": {**{f: "" for f in svc.CONTENT_FIELDS}, "system_prompt": "系统提示词 {market_type}"},
        "status": "active", "is_system": True, "workflow_id": None, "preference_type": None, "version": 3,
    })).inserted_id
    # 流程专属模板：不应出现在用户可见列表
    await db[svc.TEMPLATE_COLLECTION].insert_one({
        "agent_type": "analysts_v2", "agent_name": "market_analyst_v2", "template_name": "流程专属",
        "content": {"system_prompt": "wf"}, "status": "active", "is_system": False,
        "workflow_id": "wf1", "created_by": ObjectId(),
    })
    return {"db": db, "sys": sys_id, "u1": str(ObjectId()), "u2": str(ObjectId())}


def _mine(seeded, **kw):
    return dict(agent_type="analysts_v2", agent_name="market_analyst_v2",
                template_name=kw.pop("template_name", "我的模板"), content=kw.pop("content", CONTENT), **kw)


@pytest.mark.asyncio
async def test_system_template_is_readonly_and_hidden_workflow_templates(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    res = await svc.list_templates(db, u1)
    assert res["total"] == 1 and res["items"][0]["is_system"]
    with pytest.raises(svc.TemplateError) as ei:
        await svc.update_template(db, u1, seeded["sys"], template_name="改名")
    assert ei.value.status_code == 403
    with pytest.raises(svc.TemplateError) as ei:
        await svc.delete_template(db, u1, seeded["sys"])
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_create_requires_unique_name_and_valid_base(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    created = await svc.create_template(db, u1, **_mine(seeded), base_template_id=str(seeded["sys"]))
    assert created["status"] == "draft" and not created["is_system"]
    assert created["base_template_id"] == str(seeded["sys"]) and created["base_version"] == 3
    with pytest.raises(svc.TemplateError) as ei:
        await svc.create_template(db, u1, **_mine(seeded))
    assert ei.value.status_code == 409
    # 同名在另一用户下允许
    await svc.create_template(db, seeded["u2"], **_mine(seeded))
    # 基础模板的 Agent 不一致
    with pytest.raises(svc.TemplateError, match="不一致"):
        await svc.create_template(db, u1, agent_type="analysts_v2", agent_name="other",
                                  template_name="x", content=CONTENT, base_template_id=str(seeded["sys"]))


@pytest.mark.asyncio
async def test_users_cannot_see_or_touch_each_others_templates(seeded):
    db, u1, u2 = seeded["db"], seeded["u1"], seeded["u2"]
    t = await svc.create_template(db, u1, **_mine(seeded))
    for action in (
        lambda: svc.get_template(db, u2, t["id"]),
        lambda: svc.update_template(db, u2, t["id"], remark="x"),
        lambda: svc.delete_template(db, u2, t["id"]),
        lambda: svc.duplicate_template(db, u2, t["id"]),
        lambda: svc.activate_template(db, u2, t["id"]),
    ):
        with pytest.raises(svc.TemplateError) as ei:
            await action()
        assert ei.value.status_code == 404
    assert (await svc.list_templates(db, u2, scope="mine"))["total"] == 0


@pytest.mark.asyncio
async def test_update_bumps_version_only_on_change_and_detects_conflict(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    t = await svc.create_template(db, u1, **_mine(seeded))
    same = await svc.update_template(db, u1, t["id"], template_name=t["template_name"])
    assert same["version"] == 1
    changed = await svc.update_template(db, u1, t["id"], remark="改了备注")
    assert changed["version"] == 2 and changed["remark"] == "改了备注"
    with pytest.raises(svc.TemplateError, match="没有需要更新"):
        await svc.update_template(db, u1, t["id"])
    # 重命名冲突
    await svc.create_template(db, u1, **_mine(seeded, template_name="另一个"))
    with pytest.raises(svc.TemplateError) as ei:
        await svc.update_template(db, u1, t["id"], template_name="另一个")
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_activate_requires_published_and_takes_effect_in_runtime_schema(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    t = await svc.create_template(db, u1, **_mine(seeded))
    with pytest.raises(svc.TemplateError, match="先发布"):
        await svc.activate_template(db, u1, t["id"])
    await svc.set_status(db, u1, t["id"], "active")
    activated = await svc.activate_template(db, u1, t["id"])
    assert activated["is_active_for_me"] is True

    # 与 template_client 读取的结构逐项一致：user_id/template_id 为 ObjectId，is_active=True
    cfg = await db[svc.CONFIG_COLLECTION].find_one(
        {"user_id": ObjectId(u1), "agent_type": "analysts_v2", "agent_name": "market_analyst_v2", "is_active": True})
    assert cfg and cfg["template_id"] == ObjectId(t["id"])

    # 换一个模板生效：同一 Agent 只保留一条配置
    t2 = await svc.create_template(db, u1, **_mine(seeded, template_name="第二个"))
    await svc.set_status(db, u1, t2["id"], "active")
    await svc.activate_template(db, u1, t2["id"])
    assert await db[svc.CONFIG_COLLECTION].count_documents({"user_id": ObjectId(u1)}) == 1
    agents = await svc.list_agents(db, u1)
    assert agents[0]["using"] == "custom" and agents[0]["active_template_id"] == t2["id"]


@pytest.mark.asyncio
async def test_unpublish_and_delete_clear_active_config(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    t = await svc.create_template(db, u1, **_mine(seeded))
    await svc.set_status(db, u1, t["id"], "active")
    await svc.activate_template(db, u1, t["id"])
    back = await svc.set_status(db, u1, t["id"], "draft")
    assert back["is_active_for_me"] is False
    assert await db[svc.CONFIG_COLLECTION].count_documents({}) == 0

    await svc.set_status(db, u1, t["id"], "active")
    await svc.activate_template(db, u1, t["id"])
    await svc.delete_template(db, u1, t["id"])
    assert await db[svc.CONFIG_COLLECTION].count_documents({}) == 0
    assert (await svc.list_agents(db, u1))[0]["using"] == "system"


@pytest.mark.asyncio
async def test_deactivate_agent_restores_system(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    t = await svc.create_template(db, u1, **_mine(seeded))
    await svc.set_status(db, u1, t["id"], "active")
    await svc.activate_template(db, u1, t["id"])
    assert await svc.deactivate_agent(db, u1, "analysts_v2", "market_analyst_v2") == 1
    assert await svc.deactivate_agent(db, u1, "analysts_v2", "market_analyst_v2") == 0
    # 模板本身保留
    assert (await svc.get_template(db, u1, t["id"]))["status"] == "active"


@pytest.mark.asyncio
async def test_duplicate_system_template_dedups_names(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    a = await svc.duplicate_template(db, u1, seeded["sys"])
    b = await svc.duplicate_template(db, u1, seeded["sys"])
    assert a["template_name"] == "系统默认-市场分析师（副本）"
    assert b["template_name"] == "系统默认-市场分析师（副本） 2"
    assert a["base_template_id"] == str(seeded["sys"]) and a["status"] == "draft"
    assert a["content"]["system_prompt"].startswith("系统提示词")


@pytest.mark.asyncio
async def test_list_filters_and_keyword_is_escaped(seeded):
    db, u1 = seeded["db"], seeded["u1"]
    await svc.create_template(db, u1, **_mine(seeded, template_name="估值(保守)"))
    assert (await svc.list_templates(db, u1, scope="mine"))["total"] == 1
    assert (await svc.list_templates(db, u1, scope="system"))["total"] == 1
    assert (await svc.list_templates(db, u1, keyword="估值("))["total"] == 1  # 正则元字符被转义，不抛错
    assert (await svc.list_templates(db, u1, keyword=".*"))["total"] == 0
    assert (await svc.list_templates(db, u1, status="draft"))["total"] == 1
    with pytest.raises(svc.TemplateError):
        await svc.list_templates(db, u1, scope="bogus")
