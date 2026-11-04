"""Agent 工作坊 API 路由（Pro）

README 对照表承诺「Agent 工作坊（创建、发布、管理自定义 Agent）」。
社区版重建的 `AgentWorkshopService` 已具备完整生命周期能力（草稿→缺口分析→
构建版本→真数据测试→评估→发布→迭代），但此前只有服务层，没有 HTTP 接口与页面，
用户无法从界面触达。本路由把这些能力暴露为 REST 接口，供 `views/AgentWorkshop` 消费。

生命周期（与 nanobot 工具链共用同一套集合与状态机）：
    agent_specs → agent_workshop_sessions → agent_versions → agent_configs
版本状态：draft → testing → (evaluation) → active（发布）/ archived
阶段判定复用 nanobot 的 `_compute_workshop_phase` 语义：active 即已发布。

设计取舍：不重建 Pro 的多轮对话式需求澄清编排（那是 nanobot 助手的职责），
本路由提供「表单式」直接建草稿 → 分析 → 构建 → 测试 → 发布的完整闭环，
两条入口写同一批集合，可互换。
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.core.permissions import require_pro
from app.pro.services.agent_workshop_service import (
    CONFIG_COLLECTION,
    EVALUATION_COLLECTION,
    SPEC_COLLECTION,
    VERSION_COLLECTION,
    AgentWorkshopService,
)
from app.routers.auth_db import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent-workshop", tags=["agent-workshop"])

# 真数据测试会真实调用 LLM：同一版本两次测试之间的最小间隔（秒）
TEST_COOLDOWN_SECONDS = 20
_last_test_at: Dict[str, float] = {}
_AGENT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,47}$")


def _uid(user: Any) -> str:
    """get_current_user 返回 dict（不是对象），不能用 getattr 取 id。"""
    if isinstance(user, dict):
        return str(user.get("id") or user.get("_id") or "")
    return str(getattr(user, "id", "") or "")


async def _ensure_agent_id_free(db, agent_id: str) -> None:
    """拒绝与内置/非工坊 Agent 同名的 ID。

    构建版本会按 agent_id upsert agent_configs 并写 enabled=False、覆盖 tools/name；
    若 ID 与内置 Agent（如 market_analyst_v2）相同，一次构建就会把内置 Agent 禁用并改写。
    只有“已有工坊规格”的 ID（即工坊自己创建的）才允许复用。
    """
    if await db[SPEC_COLLECTION].find_one({"spec_id": agent_id}, {"_id": 1}):
        return
    if await db[CONFIG_COLLECTION].find_one({"agent_id": agent_id}, {"_id": 1}):
        raise HTTPException(
            status_code=409,
            detail=f"Agent ID「{agent_id}」已被系统内置或其他 Agent 占用，请换一个唯一的 ID（如加上你的前缀）",
        )

SESSION_COLLECTION = "agent_workshop_sessions"


# ==================== 请求模型 ====================

class CreateDraftRequest(BaseModel):
    agent_id: str = Field(..., description="Agent 唯一 ID（英文/数字/下划线）")
    agent_name: str = Field(..., description="Agent 显示名称")
    target_responsibility: str = Field(..., description="单一职责描述")
    output_field: str = Field("analysis_report", description="输出字段名")
    tool_ids: List[str] = Field(default_factory=list, description="候选工具 ID")
    non_goals: List[str] = Field(default_factory=list, description="非目标（不做什么）")
    preferred_methods: List[str] = Field(default_factory=list, description="方法范围/偏好")
    constraints: List[str] = Field(default_factory=list, description="额外约束")


class IterateRequest(BaseModel):
    feedback: str = Field("", description="本轮迭代反馈")


class PublishRequest(BaseModel):
    pass


class TestVersionRequest(BaseModel):
    symbol: str = Field(..., description="测试股票代码，如 600519")
    market_type: str = Field("A股", description="市场类型")
    analysis_date: Optional[str] = Field(None, description="分析日期 YYYY-MM-DD")
    user_goal: str = Field("", description="本次测试目标")


class SyncToolsRequest(BaseModel):
    required_tools: List[str] = Field(default_factory=list)
    default_tools: Optional[List[str]] = None
    max_tool_calls: int = 6


# ==================== 辅助 ====================

def _svc(db) -> AgentWorkshopService:
    return AgentWorkshopService(db=db)


async def _resolve_test_llm(db) -> Dict[str, Any]:
    """解析系统默认推理模型（与 nanobot 真数据测试同一来源）"""
    from app.services.intelligent_assistant_service import get_reasoning_llm_config

    config = await get_reasoning_llm_config(db)
    if not config:
        raise HTTPException(
            status_code=400,
            detail="系统中未找到可用的默认推理模型配置，请先在系统设置中配置大模型。",
        )
    model = str(getattr(config, "model", "") or getattr(config, "model_name", "") or "").strip()
    base_url = str(getattr(config, "base_url", "") or getattr(config, "api_base", "") or "").strip()
    api_key = str(getattr(config, "api_key", "") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="系统默认推理模型缺少 model 配置")
    return {
        "model": model,
        "temperature": float(getattr(config, "temperature", 0.2) or 0.2),
        "max_tokens": int(getattr(config, "max_tokens", 4000) or 4000),
        "backend_url": base_url or None,
        "api_key": api_key or "EMPTY",
    }


def _version_phase(status: str, latest_decision: str) -> Dict[str, str]:
    """版本状态 → 可读阶段。

    评估通过时 evaluate_version 把状态置为 publish_ready；若之后又跑了新的测试，状态回到
    evaluation（需要重新评估），此时即使历史上有过 pass 也不能算“待发布”。
    """
    s = str(status or "").strip().lower().replace("-", "_")
    d = str(latest_decision or "").strip().lower()
    if s == "active" or s == "published":
        phase = "published"
    elif s == "archived":
        phase = "archived"
    elif s in ("publish_ready", "testing") and d == "pass":
        phase = "publish-ready"
    elif d:
        phase = "evaluation"
    elif s == "testing":
        phase = "testing"
    else:
        phase = "draft"
    labels = {
        "published": "已发布",
        "archived": "已归档",
        "publish-ready": "待发布",
        "evaluation": "待修正",
        "testing": "测试中",
        "draft": "草稿",
    }
    return {"phase": phase, "phase_label": labels.get(phase, phase)}


# ==================== 列表：Agent 规格与版本 ====================

@router.get("/specs")
async def list_specs(
    keyword: str = Query("", description="按 ID/名称/职责搜索"),
    limit: int = Query(100, ge=1, le=300),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """列出所有 Agent 规格（含各自版本与运行状态）。"""
    try:
        query: Dict[str, Any] = {}
        if keyword.strip():
            kw = re.escape(keyword.strip()[:100])
            query = {"$or": [
                {"spec_id": {"$regex": kw, "$options": "i"}},
                {"name": {"$regex": kw, "$options": "i"}},
                {"primary_goal": {"$regex": kw, "$options": "i"}},
            ]}
        specs = await db[SPEC_COLLECTION].find(query, {"_id": 0}).sort("updated_at", -1).to_list(length=limit)

        # 批量补齐版本与运行配置（避免 N+1 查询）
        spec_ids = [s.get("spec_id") for s in specs if s.get("spec_id")]
        versions_by_spec: Dict[str, List[Dict[str, Any]]] = {}
        if spec_ids:
            versions = await db[VERSION_COLLECTION].find(
                {"spec_id": {"$in": spec_ids}},
                {"_id": 0, "version_id": 1, "spec_id": 1, "status": 1, "version_number": 1,
                 "required_tools": 1, "output_field": 1, "created_at": 1, "updated_at": 1},
            ).sort("version_number", -1).to_list(length=len(spec_ids) * 20)
            for v in versions:
                versions_by_spec.setdefault(v.get("spec_id") or "", []).append(v)

        latest_decisions: Dict[str, str] = {}
        version_ids = [v["version_id"] for vs in versions_by_spec.values() for v in vs if v.get("version_id")]
        if version_ids:
            evals = await db[EVALUATION_COLLECTION].find(
                {"version_id": {"$in": version_ids}},
                {"_id": 0, "version_id": 1, "decision": 1, "created_at": 1},
            ).sort("created_at", -1).to_list(length=len(version_ids) * 3)
            for e in evals:
                vid = e.get("version_id") or ""
                if vid and vid not in latest_decisions:
                    latest_decisions[vid] = str(e.get("decision") or "")

        configs = {}
        if spec_ids:
            for c in await db[CONFIG_COLLECTION].find(
                {"agent_id": {"$in": spec_ids}},
                {"_id": 0, "agent_id": 1, "enabled": 1, "version_status": 1, "runtime_status": 1},
            ).to_list(length=len(spec_ids)):
                configs[c.get("agent_id")] = c

        results = []
        for s in specs:
            sid = s.get("spec_id") or ""
            vs = versions_by_spec.get(sid, [])
            for v in vs:
                v.update(_version_phase(v.get("status"), latest_decisions.get(v.get("version_id"), "")))
            cfg = configs.get(sid) or {}
            results.append({
                **s,
                "versions": vs,
                "version_count": len(vs),
                "latest_version": vs[0] if vs else None,
                "runtime": {
                    "enabled": bool(cfg.get("enabled")),
                    "version_status": cfg.get("version_status"),
                    "runtime_status": cfg.get("runtime_status"),
                },
            })
        return {"success": True, "data": results, "total": len(results)}
    except Exception as e:
        logger.error(f"Agent 规格列表查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


@router.get("/specs/{spec_id}")
async def get_spec(
    spec_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """单个 Agent 规格详情（含全部版本、会话与最新评估）。"""
    spec = await db[SPEC_COLLECTION].find_one({"spec_id": spec_id}, {"_id": 0})
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent {spec_id} 不存在")

    versions = await db[VERSION_COLLECTION].find(
        {"spec_id": spec_id}, {"_id": 0}
    ).sort("version_number", -1).to_list(length=100)
    for v in versions:
        latest = await db[EVALUATION_COLLECTION].find_one(
            {"version_id": v.get("version_id")}, {"_id": 0}, sort=[("created_at", -1)]
        )
        v["latest_evaluation"] = latest
        v.update(_version_phase(v.get("status"), (latest or {}).get("decision", "")))

    sessions = await db[SESSION_COLLECTION].find(
        {"spec_id": spec_id}, {"_id": 0, "session_id": 1, "status": 1, "completeness_score": 1,
                               "feasibility_status": 1, "created_at": 1}
    ).sort("created_at", -1).to_list(length=20)
    config = await db[CONFIG_COLLECTION].find_one({"agent_id": spec_id}, {"_id": 0})
    return {
        "success": True,
        "data": {
            **spec,
            "versions": versions,
            "sessions": sessions,
            "runtime": config,
        },
    }


# ==================== 草稿与缺口分析 ====================

@router.post("/drafts", dependencies=[Depends(require_pro)])
async def create_draft(
    req: CreateDraftRequest,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """创建 Agent 草稿（同 agent_id 已有未完成会话时幂等复用）。"""
    clean_id = req.agent_id.strip()
    if not clean_id or not req.agent_name.strip() or not req.target_responsibility.strip():
        raise HTTPException(status_code=400, detail="agent_id、agent_name、target_responsibility 不能为空")
    if not _AGENT_ID_RE.match(clean_id):
        raise HTTPException(status_code=400, detail="agent_id 需以字母开头，只能包含字母、数字、下划线和短横线，长度 3-48")
    if len(req.agent_name.strip()) > 50:
        raise HTTPException(status_code=400, detail="Agent 名称不能超过 50 字符")
    if len(req.target_responsibility.strip()) > 1000:
        raise HTTPException(status_code=400, detail="职责描述不能超过 1000 字符")
    await _ensure_agent_id_free(db, clean_id)

    result = await _svc(db).create_nanobot_draft(
        agent_id=clean_id,
        agent_name=req.agent_name.strip(),
        target_responsibility=req.target_responsibility.strip(),
        output_field=req.output_field or "analysis_report",
        tool_ids=req.tool_ids,
        non_goals=req.non_goals,
        preferred_methods=req.preferred_methods,
        constraints=req.constraints,
        tooling_contract={"required_tools": req.tool_ids},
        owner_user_id=_uid(user),
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "创建失败"))
    spec = result.get("spec")
    if spec is not None and hasattr(spec, "model_dump"):
        result["spec"] = spec.model_dump()
    return {"success": True, "data": result}


@router.post("/sessions/{session_id}/analyze", dependencies=[Depends(require_pro)])
async def analyze_gaps(
    session_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """执行能力缺口分析（检索能力索引，区分阻塞/非阻塞缺口）。"""
    result = await _svc(db).analyze_gaps(session_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "会话不存在"))
    report = result.get("gap_report")
    if report is not None and hasattr(report, "model_dump"):
        result["gap_report"] = report.model_dump()
    return {"success": True, "data": result}


@router.post("/sessions/{session_id}/tooling-plan", dependencies=[Depends(require_pro)])
async def tooling_plan(
    session_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """生成工具需求计划。"""
    result = await _svc(db).generate_tooling_plan(session_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "会话不存在"))
    return {"success": True, "data": result}


@router.post("/sessions/{session_id}/acknowledge-gaps", dependencies=[Depends(require_pro)])
async def acknowledge_gaps(
    session_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """接受当前所有阻塞缺口：这些能力没有现成工具，改由提示词（模型自身能力）承担。

    构建门禁只放行“已确认接受”的缺口；表单式界面没有 nanobot 的多轮补齐对话，
    不提供这个出口，职责没有工具覆盖的 Agent 就永远无法构建。
    """
    session = await db[SESSION_COLLECTION].find_one({"session_id": session_id}, {"_id": 0, "gap_report": 1})
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    report = session.get("gap_report")
    if not report:
        raise HTTPException(status_code=400, detail="请先运行缺口分析")
    gaps = [str(g.get("description") or "").strip() for g in (report.get("blocking_gaps") or [])]
    gaps = [g for g in gaps if g]
    await db[SESSION_COLLECTION].update_one(
        {"session_id": session_id},
        {"$set": {"builder_contracts.acknowledged_gaps": gaps}},
    )
    logger.info("[AgentWorkshop] 接受缺口 session=%s count=%d by=%s", session_id, len(gaps), _uid(user))
    return {"success": True, "data": {"acknowledged": gaps}}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """会话详情（含规格快照与缺口报告）。"""
    doc = await db[SESSION_COLLECTION].find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    return {"success": True, "data": doc}


# ==================== 版本构建与测试 ====================

@router.post("/sessions/{session_id}/build", dependencies=[Depends(require_pro)])
async def build_version(
    session_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """构建 Draft 版本（存在未确认的阻塞缺口时返回 409 + 缺口明细）。"""
    result = await _svc(db).build_version(session_id)
    if result.get("status") == "error":
        blocking = result.get("blocking_gaps")
        if blocking:
            raise HTTPException(status_code=409, detail={
                "message": result.get("message", "存在未解决的能力缺口"),
                "blocking_gaps": blocking,
            })
        raise HTTPException(status_code=400, detail=result.get("message", "构建失败"))
    return {"success": True, "data": result}


@router.post("/versions/{version_id}/test", dependencies=[Depends(require_pro)])
async def test_version(
    version_id: str,
    req: TestVersionRequest,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """真数据测试（用系统默认推理模型真实跑一次 UniversalAgent）。"""
    now = time.monotonic()
    if now - _last_test_at.get(version_id, 0) < TEST_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail=f"测试会真实调用大模型，请 {TEST_COOLDOWN_SECONDS} 秒后再试")
    _last_test_at[version_id] = now
    llm = await _resolve_test_llm(db)
    result = await _svc(db).debug_version(
        version_id=version_id,
        llm=llm,
        stock={
            "symbol": req.symbol.strip(),
            "market_type": (req.market_type or "A股").strip(),
            "analysis_date": (req.analysis_date or "").strip() or None,
        },
        user_goal=req.user_goal or "",
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "测试失败"))
    return {"success": True, "data": result}


@router.post("/versions/{version_id}/evaluate", dependencies=[Depends(require_pro)])
async def evaluate_version(
    version_id: str,
    req: Optional[Dict[str, Any]] = None,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """评估版本：只依据服务端保存的最近一次真数据测试输出判定 pass / needs_iteration。

    不接受客户端传入的 runtime_result：评估通过是发布门禁，若可由调用方自报输出，
    就能不经测试直接放行发布。
    """
    version = await db[VERSION_COLLECTION].find_one({"version_id": version_id}, {"_id": 0})
    if not version:
        raise HTTPException(status_code=404, detail=f"版本 {version_id} 不存在")
    report = str(version.get("last_debug_report") or "")
    if not report.strip():
        raise HTTPException(status_code=400, detail="该版本还没有测试输出，请先运行真数据测试")
    feedback = str((req or {}).get("user_feedback") or "")[:1000] if isinstance(req, dict) else ""
    result = await _svc(db).evaluate_version(
        version_id,
        evaluation_type="production-review",
        user_feedback=feedback,
        runtime_result={"report": report},
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "评估失败"))
    return {"success": True, "data": result}


@router.post("/versions/{version_id}/publish", dependencies=[Depends(require_pro)])
async def publish_version(
    version_id: str,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """发布版本（最新评估需为 pass；发布后 Agent 立即可在工作流中使用）。"""
    result = await _svc(db).publish_version(version_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result.get("message", "发布被拒绝"))
    return {"success": True, "data": result}


@router.post("/versions/{version_id}/iterate", dependencies=[Depends(require_pro)])
async def iterate_version(
    version_id: str,
    req: IterateRequest = IterateRequest(),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """基于反馈开启下一轮迭代（新会话 + 新 draft 版本）。"""
    result = await _svc(db).iterate_version(version_id, feedback=req.feedback)
    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result.get("message", "迭代失败"))
    return {"success": True, "data": result}


@router.delete("/versions/{version_id}", dependencies=[Depends(require_pro)])
async def delete_version(
    version_id: str,
    force: bool = Query(False, description="强制删除已发布版本"),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """删除版本（已发布/待发布版本需 force=True）。"""
    result = await _svc(db).delete_version(version_id, force=force)
    if not result.get("deleted"):
        raise HTTPException(status_code=409, detail=result.get("message", "删除失败"))
    return {"success": True, "data": result}


@router.put("/versions/{version_id}/tools", dependencies=[Depends(require_pro)])
async def sync_tools(
    version_id: str,
    req: SyncToolsRequest,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """同步版本的工具绑定（写入 agent_versions 与 agent_configs）。"""
    result = await _svc(db).sync_version_required_tools(
        version_id,
        required_tools=req.required_tools,
        default_tools=req.default_tools,
        max_tool_calls=req.max_tool_calls,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "版本不存在"))
    return {"success": True, "data": result}


# ==================== 能力检索 ====================

@router.get("/capabilities")
async def search_capabilities(
    q: str = Query(..., description="能力检索关键词"),
    top_k: int = Query(8, ge=1, le=30),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """检索可绑定的内置工具/Skill（缺口补齐与工具选择的数据源）。"""
    hits = await _svc(db).search_capabilities(q, top_k=top_k)
    results = []
    for hit in hits or []:
        cap = getattr(hit, "capability", None)
        results.append({
            "capability_id": str(getattr(cap, "capability_id", "") or ""),
            "registry_tool_id": str(getattr(cap, "registry_tool_id", "") or ""),
            "name": str(getattr(cap, "name", "") or ""),
            "display_name": str(getattr(cap, "display_name", "") or ""),
            "description": str(getattr(cap, "description", "") or "")[:200],
            "source_type": str(getattr(cap, "source_type", "") or ""),
            "score": float(getattr(hit, "score", 0.0) or 0.0),
        })
    return {"success": True, "data": results}


@router.get("/available-tools")
async def list_available_tools(
    keyword: str = Query("", description="按 ID/名称过滤"),
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """列出工具注册表中可绑定的工具（供建草稿时选择）。"""
    try:
        from core.tools import get_tool_registry
        registry = get_tool_registry()
        tools = registry.list_all()
    except Exception as e:
        logger.warning(f"工具注册表不可用: {e}")
        return {"success": True, "data": []}
    kw = keyword.strip().lower()
    out = []
    for t in tools:
        tid = str(getattr(t, "id", "") or "")
        name = str(getattr(t, "name", "") or "")
        if kw and kw not in tid.lower() and kw not in name.lower():
            continue
        out.append({
            "tool_id": tid,
            "name": name,
            "description": str(getattr(t, "description", "") or "")[:160],
            "category": str(getattr(t, "category", "") or ""),
            "source": str(getattr(t, "source", "") or ""),
        })
    out.sort(key=lambda x: x["tool_id"])
    return {"success": True, "data": out[:500], "total": len(out)}
