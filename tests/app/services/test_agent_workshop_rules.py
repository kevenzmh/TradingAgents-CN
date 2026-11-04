"""Agent 工坊：阶段判定与发布门禁。"""

import pytest

from app.pro.routers.agent_workshop import _AGENT_ID_RE, _version_phase
from app.pro.services import agent_workshop_service as svc


@pytest.mark.parametrize("status,decision,phase", [
    ("draft", "", "draft"),
    ("testing", "", "testing"),
    ("evaluation", "", "draft"),                 # 无评估记录时按 draft（还没评估）
    ("evaluation", "needs_iteration", "evaluation"),
    ("evaluation", "fail", "evaluation"),
    ("publish_ready", "pass", "publish-ready"),   # evaluate_version 通过后写入的真实状态
    ("publish-ready", "pass", "publish-ready"),
    ("testing", "pass", "publish-ready"),         # nanobot 流程兼容
    ("evaluation", "pass", "evaluation"),         # 评估通过后又测试过：需要重新评估
    ("active", "pass", "published"),
    ("published", "", "published"),
    ("archived", "pass", "archived"),
])
def test_phase(status, decision, phase):
    assert _version_phase(status, decision)["phase"] == phase


@pytest.mark.parametrize("agent_id,ok", [
    ("my_agent", True), ("A1b", True), ("a-b_c", True), ("x" * 48, True),
    ("ab", False), ("1abc", False), ("_abc", False), ("a b c", False), ("a.b.c", False), ("x" * 49, False), ("中文名字", False),
])
def test_agent_id_pattern(agent_id, ok):
    assert bool(_AGENT_ID_RE.match(agent_id)) is ok


class _Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, query, projection=None, sort=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    async def update_one(self, *a, **k):
        self.updated = True


class _DB(dict):
    pass


def _db(version_status, decision="pass"):
    return _DB({
        svc.VERSION_COLLECTION: _Coll([{"version_id": "v1", "spec_id": "s1", "status": version_status}]),
        svc.EVALUATION_COLLECTION: _Coll([{"version_id": "v1", "decision": decision}]),
        svc.CONFIG_COLLECTION: _Coll(), svc.SPEC_COLLECTION: _Coll(), svc.SESSION_COLLECTION: _Coll(),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("status,ok", [
    (svc.VERSION_PUBLISH_READY, True), (svc.VERSION_TESTING, True),
    (svc.VERSION_EVALUATION, False), (svc.VERSION_DRAFT, False),
])
async def test_publish_requires_fresh_evaluation(status, ok):
    result = await svc.AgentWorkshopService(db=_db(status)).publish_version("v1")
    assert (result["status"] == svc.VERSION_PUBLISHED) is ok
    if not ok:
        assert "重新评估" in result["message"]


@pytest.mark.asyncio
async def test_publish_requires_passing_evaluation():
    result = await svc.AgentWorkshopService(db=_db(svc.VERSION_PUBLISH_READY, "needs_iteration")).publish_version("v1")
    assert result["status"] == "error" and "评估通过" in result["message"]
