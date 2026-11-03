"""Agent 工坊服务（社区版重建）

原 Pro 版为 AgentSpec 契约治理的完整实现（版本治理/评估工作流/多轮对话编排）。
社区版按消费方契约重建 AgentWorkshopService 的全部 16 个被引用方法，
采用"最小可用但真实落库"语义：

数据落点（与消费方读取一一对应）：
- agent_specs            AgentSpec 文档（spec_id 唯一）
- agent_workshop_sessions 会话文档（session_id 唯一；builder_contracts/
                          gap_report/current_spec_snapshot 字段供阶段解析）
- agent_versions         版本文档（version_id 唯一；status 状态机
                          draft→testing→evaluation→publish_ready→published）
- agent_configs          运行时配置（AgentFactory._try_create_universal_agent
                          据此实例化 UniversalAgent——这是自定义 Agent 真正可执行的关键）
- prompt_templates       提示词模板（template_client 三层优先级查找）
- agent_evaluations      评估记录（decision: pass/fail/needs_iteration）

执行链路：debug_version 通过 AgentFactory 创建 UniversalAgent 并在
线程池里跑一次真实 execute（工具绑定 + 提示词模板 + 轨迹采集全走
既有运行时），发布时写 agent_configs（enabled + version_status=active），
工作流编排器即可把它当普通 agent 节点使用。
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.pro.models.agent_workshop import (
    AgentSpec,
    AgentSpecIOField,
    AgentSpecStatus,
    AgentWorkshopGapAnalysis,
    GapItem,
    WorkshopSession,
)

logger = logging.getLogger(__name__)

SPEC_COLLECTION = "agent_specs"
SESSION_COLLECTION = "agent_workshop_sessions"
VERSION_COLLECTION = "agent_versions"
CONFIG_COLLECTION = "agent_configs"
TEMPLATE_COLLECTION = "prompt_templates"
EVALUATION_COLLECTION = "agent_evaluations"

# 版本状态机（消费方 _compute_workshop_phase 依赖这些字面量）
VERSION_DRAFT = "draft"
VERSION_TESTING = "testing"
VERSION_EVALUATION = "evaluation"
VERSION_PUBLISH_READY = "publish_ready"
VERSION_PUBLISHED = "published"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AgentWorkshopService:
    """Agent 工坊服务（社区版契约重建）"""

    def __init__(self, db: Any = None):
        self.db = db

    # ==================== 会话与规格 ====================

    async def _load_session(self, session_id: str) -> Optional[WorkshopSession]:
        """按 session_id 加载会话（消费方在 4 处调用，None 表示不存在）"""
        clean = str(session_id or "").strip()
        if not clean or self.db is None:
            return None
        try:
            doc = await self.db[SESSION_COLLECTION].find_one({"session_id": clean})
            if not doc:
                return None
            gap_raw = doc.get("gap_report")
            gap = AgentWorkshopGapAnalysis(**gap_raw) if isinstance(gap_raw, dict) else None
            doc.pop("gap_report", None)
            doc.pop("_id", None)
            return WorkshopSession(gap_report=gap, **doc)
        except Exception as exc:
            logger.warning("[AgentWorkshop] 会话加载失败 session_id=%s err=%s", clean, exc)
            return None

    async def _load_or_create_spec(self, spec_id: str, agent_name: str, fallback: AgentSpec) -> AgentSpec:
        """读取或创建 AgentSpec（create_nanobot_draft 的幂等基础）"""
        doc = await self.db[SPEC_COLLECTION].find_one({"spec_id": spec_id})
        if doc:
            doc.pop("_id", None)
            return AgentSpec(**doc)
        spec = fallback
        spec.created_at = _now_iso()
        await self.db[SPEC_COLLECTION].insert_one(spec.model_dump())
        return spec

    async def create_nanobot_draft(
        self,
        *,
        agent_id: str,
        agent_name: str,
        target_responsibility: str,
        output_field: str = "analysis_report",
        tool_ids: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
        preferred_methods: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        runtime_contract: Optional[Dict[str, Any]] = None,
        prompt_contract: Optional[Dict[str, Any]] = None,
        tooling_contract: Optional[Dict[str, Any]] = None,
        owner_user_id: str = "",
        source_thread_id: str = "",
    ) -> Dict[str, Any]:
        """创建（或复用）工坊会话与 AgentSpec 草稿。

        返回契约：{status, spec_id, session_id, spec}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}

        clean_agent_id = str(agent_id or "").strip()
        clean_name = str(agent_name or "").strip() or clean_agent_id
        responsibility = str(target_responsibility or "").strip()

        # 幂等：同一 agent 已有非终态会话则复用
        existing = await self.db[SESSION_COLLECTION].find_one(
            {"agent_id": clean_agent_id, "status": {"$nin": ["published", "abandoned"]}},
            sort=[("created_at", -1)],
        )
        if existing:
            spec = await self._load_or_create_spec(clean_agent_id, clean_name, AgentSpec(
                spec_id=clean_agent_id, name=clean_name,
                primary_goal=responsibility or f"{clean_name} 的职责",
            ))
            return {
                "status": "ok",
                "spec_id": clean_agent_id,
                "session_id": existing.get("session_id", ""),
                "spec": spec,
                "message": f"复用 Agent「{clean_name}」的既有工坊会话",
            }

        # AgentSpec 草稿
        capability_requirements = []
        for idx, text in enumerate([responsibility], start=1):
            if not str(text or "").strip():
                continue
            capability_requirements.append({
                "capability_key": f"requirement_{idx}",
                "capability_name": str(text)[:40],
                "capability_type": "orchestration",
                "description": str(text),
                "delivery_requirement": "tool-or-prompt",
                "acceptance_criteria": ["按确认稿边界交付，不超出单一职责"],
            })
        spec = AgentSpec(
            spec_id=clean_agent_id,
            name=clean_name,
            domain="stock_analysis" if any(k in responsibility for k in ("股", "分析", "行情", "财报")) else "general",
            primary_goal=responsibility or f"{clean_name} 的职责",
            responsibilities=[responsibility] if responsibility else [],
            non_goals=non_goals or [],
            inputs=[AgentSpecIOField(name="symbol", type="string", required=False, description="股票代码或用户输入标的")],
            outputs=[AgentSpecIOField(name=str(output_field or "analysis_report").strip() or "analysis_report", type="string", description="分析报告")],
            constraints=constraints or [],
            preferred_methods=preferred_methods or [],
            capability_requirements=capability_requirements,
            status=AgentSpecStatus.DRAFT,
            source="nanobot_prepare",
        )
        spec = await self._load_or_create_spec(clean_agent_id, clean_name, spec)

        session_id = _new_id("ws")
        session_doc = {
            "session_id": session_id,
            "spec_id": clean_agent_id,
            "agent_id": clean_agent_id,
            "agent_name": clean_name,
            "user_id": str(owner_user_id or "").strip() or "nanobot",
            "status": "draft_preparing",
            "current_spec_snapshot": spec.model_dump(),
            "gap_report": None,
            "feasibility_status": "feasible",
            "completeness_score": 0.0,
            "builder_contracts": {
                "runtime_contract": runtime_contract or {},
                "prompt_contract": prompt_contract or {},
                "tooling_contract": tooling_contract or {
                    "required_tools": tool_ids or [],
                },
                "output_field": output_field or "analysis_report",
            },
            "source_thread_id": str(source_thread_id or "").strip(),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        await self.db[SESSION_COLLECTION].insert_one(session_doc)

        logger.info("[AgentWorkshop] 创建草稿 session=%s agent=%s", session_id, clean_agent_id)
        return {
            "status": "ok",
            "spec_id": clean_agent_id,
            "session_id": session_id,
            "spec": spec,
        }

    # ==================== 缺口分析 ====================

    async def _builtin_tool_evidence_for_gap(self, gap_description: str) -> Optional[Dict[str, Any]]:
        """按能力索引检索内置工具证据（命中即可复用，免生成 Skill）"""
        clean = str(gap_description or "").strip()
        if not clean or self.db is None:
            return None
        try:
            from app.services.capability_index_service import CapabilityIndexService
            hits = await CapabilityIndexService(self.db).search_capabilities(
                query=clean, top_k=3, bindable_only=True,
                source_types=["builtin_tool", "skill", "external_skill"],
            )
            for hit in hits or []:
                tool_id = str(getattr(hit, "tool_id", "") or "").strip()
                if not tool_id:
                    continue
                return {
                    "skill_tool_id": tool_id,
                    "skill_display_name": str(getattr(hit, "display_name", "") or tool_id),
                    "reason": f"能力索引命中：{getattr(hit, 'description', '')[:80]}",
                    "additional_tool_ids": [],
                }
        except Exception as exc:
            logger.debug("[AgentWorkshop] 内置证据检索失败: %s", exc)
        return None

    async def analyze_gaps(self, session_id: str) -> Dict[str, Any]:
        """对会话当前规格做能力缺口分析。

        返回契约：{spec_id, session_id, gap_report: AgentWorkshopGapAnalysis}
        gap_report 为模型对象（消费方带 model_dump 兜底转换）。
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        session = await self._load_session(session_id)
        if session is None:
            return {"status": "error", "message": f"会话 {session_id} 不存在"}

        snapshot = session.current_spec_snapshot or {}
        requirements = [
            str(item.get("description") or "").strip()
            for item in (snapshot.get("capability_requirements") or [])
            if str(item.get("description") or "").strip()
        ]
        required_tools = list(((session.builder_contracts or {}).get("tooling_contract") or {}).get("required_tools") or [])

        blocking: List[GapItem] = []
        non_blocking: List[GapItem] = []
        available_tools: List[str] = list(required_tools)

        # 每条能力需求做能力索引检索：有命中 → available；无命中 → blocking gap
        for idx, req in enumerate(requirements):
            evidence = await self._builtin_tool_evidence_for_gap(req)
            if evidence:
                tool_id = evidence.get("skill_tool_id") or ""
                if tool_id and tool_id not in available_tools:
                    available_tools.append(tool_id)
                non_blocking.append(GapItem(
                    gap_id=f"gap_{idx + 1}",
                    description=req,
                    blocking=False,
                    suggested_resolution="bind_existing",
                ))
            else:
                blocking.append(GapItem(
                    gap_id=f"gap_{idx + 1}",
                    description=req,
                    blocking=True,
                    suggested_resolution="create_skill",
                ))

        total = max(1, len(requirements))
        resolved = total - len(blocking)
        completeness = round(resolved / total, 2)
        feasibility = "feasible" if len(blocking) <= 2 else "needs_user_input"

        gap_report = AgentWorkshopGapAnalysis(
            blocking_gaps=blocking,
            non_blocking_gaps=non_blocking,
            available_tools=available_tools,
            available_skills=[],
            missing_capabilities=[g.description for g in blocking],
            completeness_score=completeness,
            feasibility_status=feasibility,
            analyzed_at=_now_iso(),
        )

        await self.db[SESSION_COLLECTION].update_one(
            {"session_id": session.session_id},
            {"$set": {
                "gap_report": gap_report.model_dump(),
                "completeness_score": completeness,
                "feasibility_status": feasibility,
                "updated_at": _now_iso(),
            }},
        )
        return {
            "status": "ok",
            "spec_id": session.spec_id,
            "session_id": session.session_id,
            "gap_report": gap_report,
        }

    async def generate_tooling_plan(self, session_id: str) -> Dict[str, Any]:
        """基于缺口分析产出工具计划。

        返回契约：{spec_id, session_id, tooling_plan}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        session = await self._load_session(session_id)
        if session is None:
            return {"status": "error", "message": f"会话 {session_id} 不存在"}

        gap_report = session.gap_report or AgentWorkshopGapAnalysis()
        tooling_plan = {
            "required_tools": list(gap_report.available_tools or []),
            "missing_capabilities": list(gap_report.missing_capabilities or []),
            "blocking_gaps": [g.description for g in (gap_report.blocking_gaps or [])],
            "plan": "prompt_only" if not gap_report.available_tools else "tools_and_prompt",
        }
        await self.db[SESSION_COLLECTION].update_one(
            {"session_id": session.session_id},
            {"$set": {"builder_contracts.tooling_plan": tooling_plan, "updated_at": _now_iso()}},
        )
        return {
            "status": "ok",
            "spec_id": session.spec_id,
            "session_id": session.session_id,
            "tooling_plan": tooling_plan,
        }

    async def _collect_auto_resolved_gaps_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """收集会话内已自动解决（复用内置工具）的缺口（工具同步用）"""
        session = await self._load_session(session_id)
        if session is None or session.gap_report is None:
            return []
        resolved = []
        for gap in (session.gap_report.non_blocking_gaps or []):
            evidence = await self._builtin_tool_evidence_for_gap(gap.description)
            if evidence:
                resolved.append({
                    "gap_id": gap.gap_id,
                    "description": gap.description,
                    "skill_tool_id": evidence.get("skill_tool_id") or "",
                    "source": "builtin_evidence",
                })
        return resolved

    # ==================== 版本 ====================

    async def build_version(self, session_id: str) -> Dict[str, Any]:
        """从会话构建 Draft 版本（写 agent_versions + agent_configs 运行时配置）。

        返回契约：{spec_id, session_id, version(模型或 dict), version_id}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        session = await self._load_session(session_id)
        if session is None:
            return {"status": "error", "message": f"会话 {session_id} 不存在"}

        contracts = session.builder_contracts or {}
        tooling = contracts.get("tooling_contract") or {}
        runtime = contracts.get("runtime_contract") or {}
        required_tools = list(tooling.get("required_tools") or [])
        output_field = str(contracts.get("output_field") or runtime.get("output_field") or "analysis_report").strip()
        snapshot = session.current_spec_snapshot or {}

        # 校验阻断缺口：未确认的 blocking gap 禁止构建（消费方依赖该错误语义做缺口补齐引导）
        gap_report = session.gap_report
        if gap_report is not None:
            acknowledged = set(
                str(g).strip()
                for g in ((contracts.get("acknowledged_gaps") or []))
            )
            unresolved = [
                g for g in (gap_report.blocking_gaps or [])
                if g.description not in acknowledged
                and not (await self._builtin_tool_evidence_for_gap(g.description))
            ]
            if unresolved:
                return {
                    "status": "error",
                    "message": (
                        "存在 " + str(len(unresolved)) + " 条未解决的能力缺口，"
                        "请先调用 resolve_agent_workshop_blocking_gaps 补齐或在方案中确认接受："
                        + "；".join(g.description for g in unresolved[:3])
                    ),
                    "blocking_gaps": [g.description for g in unresolved],
                }

        # 校验工具注册
        if required_tools:
            try:
                from core.tools import get_tool_registry
                registry = get_tool_registry()
                missing = [t for t in required_tools if registry.get(t) is None]
                if missing:
                    return {
                        "status": "error",
                        "message": f"以下工具未注册: {', '.join(missing)}",
                        "missing_tools": missing,
                    }
            except Exception as exc:
                logger.warning("[AgentWorkshop] 工具注册表检查失败（跳过校验）: %s", exc)

        version_id = _new_id("ver")
        version_doc = {
            "version_id": version_id,
            "spec_id": session.spec_id,
            "session_id": session.session_id,
            "version_number": 1,
            "status": VERSION_DRAFT,
            "required_tools": required_tools,
            "required_capabilities": [
                str(item.get("description") or "")
                for item in (snapshot.get("capability_requirements") or [])
            ],
            "runtime_contract": runtime,
            "tooling_contract": tooling,
            "output_field": output_field,
            "published_from_session_id": session.session_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        await self.db[VERSION_COLLECTION].insert_one(version_doc)

        # agent_configs：AgentFactory 据此实例化（draft 态先落结构，enabled 等发布时开）
        spec_doc = await self.db[SPEC_COLLECTION].find_one({"spec_id": session.spec_id}) or {}
        await self.db[CONFIG_COLLECTION].update_one(
            {"agent_id": session.spec_id},
            {"$set": {
                "agent_id": session.spec_id,
                "name": session.agent_name or spec_doc.get("name") or session.spec_id,
                "description": snapshot.get("primary_goal") or "",
                "output_field": output_field,
                "tools": required_tools,
                "default_tools": required_tools[:4],
                "max_tool_calls": max(6, len(required_tools)),
                "prompt_template_type": "universal",
                "prompt_template_name": session.spec_id,
                "enabled": False,
                "version_status": VERSION_DRAFT,
                "runtime_status": "inactive",
                "metadata": {
                    "source": "agent_workshop",
                    "spec_id": session.spec_id,
                    "version_id": version_id,
                    "version_status": VERSION_DRAFT,
                    "latest_workshop_version_id": version_id,
                    "output_field": output_field,
                },
                "updated_at": _now_iso(),
            }},
            upsert=True,
        )

        await self.db[SPEC_COLLECTION].update_one(
            {"spec_id": session.spec_id},
            {"$set": {"current_version_id": version_id, "updated_at": _now_iso()}},
        )
        await self.db[SESSION_COLLECTION].update_one(
            {"session_id": session.session_id},
            {"$set": {"current_version_id": version_id, "status": "building", "updated_at": _now_iso()}},
        )

        version_dict = dict(version_doc)
        logger.info("[AgentWorkshop] 构建版本 %s (agent=%s tools=%d)", version_id, session.spec_id, len(required_tools))
        return {
            "status": "ok",
            "spec_id": session.spec_id,
            "session_id": session.session_id,
            "version_id": version_id,
            "version": version_dict,
        }

    async def sync_version_required_tools(
        self,
        version_id: str,
        required_tools: List[str],
        default_tools: Optional[List[str]] = None,
        max_tool_calls: int = 6,
    ) -> Dict[str, Any]:
        """同步版本与运行时配置的工具绑定"""
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"status": "error", "message": f"版本 {version_id} 不存在"}

        await self.db[VERSION_COLLECTION].update_one(
            {"version_id": version_id},
            {"$set": {"required_tools": required_tools, "updated_at": _now_iso()}},
        )
        await self.db[CONFIG_COLLECTION].update_one(
            {"agent_id": version_doc.get("spec_id")},
            {"$set": {
                "tools": required_tools,
                "default_tools": default_tools or required_tools[:4],
                "max_tool_calls": max_tool_calls,
                "updated_at": _now_iso(),
            }},
        )
        return {"status": "ok", "version_id": version_id, "required_tools": required_tools}

    async def delete_version(self, version_id: str, force: bool = False) -> Dict[str, Any]:
        """删除版本（仅 draft/testing 可删；force 时跳过状态检查）"""
        if self.db is None:
            return {"deleted": False, "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"deleted": False, "message": f"版本 {version_id} 不存在"}
        status = str(version_doc.get("status") or "").strip().lower()
        if not force and status in (VERSION_PUBLISH_READY, VERSION_PUBLISHED):
            return {"deleted": False, "message": f"版本处于 {status} 状态，不可删除（可 force）"}
        await self.db[VERSION_COLLECTION].delete_one({"version_id": version_id})
        return {"deleted": True, "version_id": version_id}

    # ==================== 测试 / 评估 / 发布 ====================

    async def debug_version(
        self,
        version_id: str,
        llm: Dict[str, Any],
        stock: Dict[str, Any],
        user_goal: str = "",
        prompt_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """用真实运行时跑一次单股测试（UniversalAgent 全链路）。

        返回契约：{version_id, spec_id, symbol, report, report_length, success}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"status": "error", "message": f"版本 {version_id} 不存在"}

        # 进入 testing 态（AgentFactory 查询条件之一）
        await self.db[VERSION_COLLECTION].update_one(
            {"version_id": version_id},
            {"$set": {"status": VERSION_TESTING, "updated_at": _now_iso()}},
        )
        await self.db[CONFIG_COLLECTION].update_one(
            {"agent_id": version_doc.get("spec_id")},
            {"$set": {"version_status": VERSION_TESTING, "runtime_status": "active", "enabled": True, "updated_at": _now_iso()}},
        )

        symbol = str(stock.get("symbol") or "").strip()
        agent_id = str(version_doc.get("spec_id") or "").strip()

        def _run() -> Dict[str, Any]:
            """线程池内同步执行（Agent.execute 是同步方法）"""
            from langchain_openai import ChatOpenAI
            from core.agents.factory import AgentFactory

            llm_instance = ChatOpenAI(
                model=str(llm.get("model") or ""),
                temperature=float(llm.get("temperature") or 0.2),
                max_tokens=int(llm.get("max_tokens") or 4000),
                api_key=str(llm.get("api_key") or "EMPTY"),
                base_url=str(llm.get("backend_url") or None) or None,
            )
            agent = AgentFactory().create(agent_id=agent_id, llm=llm_instance)
            if agent is None:
                return {"error": f"无法实例化 Agent {agent_id}（检查 agent_configs）"}

            state: Dict[str, Any] = {
                "ticker": symbol,
                "symbol": symbol,
                "stock_symbol": symbol,
                "company_of_interest": symbol,
                "market_type": str(stock.get("market_type") or "A股"),
            }
            if stock.get("analysis_date"):
                state["analysis_date"] = stock["analysis_date"]
                state["trade_date"] = stock["analysis_date"]
            if user_goal:
                state["user_goal"] = user_goal
            if prompt_overrides:
                state["prompt_overrides"] = prompt_overrides
            result = agent.execute(state)
            output_field = str(version_doc.get("output_field") or "analysis_report")
            report = str(result.get(output_field) or "")
            return {"report": report}

        try:
            outcome = await asyncio.to_thread(_run)
        except Exception as exc:
            logger.error("[AgentWorkshop] debug_version 执行失败: %s", exc, exc_info=True)
            return {"status": "error", "version_id": version_id, "message": f"测试执行失败: {exc}"}

        if "error" in outcome:
            return {"status": "error", "version_id": version_id, "message": outcome["error"], "spec_id": agent_id, "symbol": symbol}

        report = outcome.get("report") or ""
        await self.db[VERSION_COLLECTION].update_one(
            {"version_id": version_id},
            {"$set": {"status": VERSION_EVALUATION, "last_debug_report": report[:2000], "updated_at": _now_iso()}},
        )
        return {
            "status": "ok",
            "version_id": version_id,
            "spec_id": agent_id,
            "symbol": symbol,
            "report": report,
            "report_length": len(report),
            "success": bool(report.strip()),
        }

    async def evaluate_version(
        self,
        version_id: str,
        evaluation_type: str = "auto",
        sample_set_id: Optional[str] = None,
        user_feedback: str = "",
        benchmark_outputs: Optional[List[Any]] = None,
        runtime_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """评估版本（社区版：基于 runtime_result 的启发式判定 + 落库）。

        返回契约：{version_id, spec_id, evaluation, decision}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"status": "error", "message": f"版本 {version_id} 不存在"}

        report_ok = bool(((runtime_result or {}).get("report") or "").strip())
        if report_ok:
            decision = "pass"
        elif user_feedback and any(k in user_feedback for k in ("差", "不对", "失败", "不行")):
            decision = "fail"
        else:
            decision = "needs_iteration"

        evaluation_doc = {
            "evaluation_id": _new_id("eval"),
            "version_id": version_id,
            "spec_id": version_doc.get("spec_id"),
            "evaluation_type": str(evaluation_type or "auto"),
            "decision": decision,
            "user_feedback": user_feedback,
            "runtime_result": (runtime_result or {}),
            "scores": {
                "completeness": 0.8 if report_ok else 0.3,
                "relevance": 0.8 if report_ok else 0.3,
            },
            "created_at": _now_iso(),
        }
        await self.db[EVALUATION_COLLECTION].insert_one(evaluation_doc)

        next_status = VERSION_PUBLISH_READY if decision == "pass" else VERSION_EVALUATION
        await self.db[VERSION_COLLECTION].update_one(
            {"version_id": version_id},
            {"$set": {"status": next_status, "updated_at": _now_iso()}},
        )
        return {
            "status": "ok",
            "version_id": version_id,
            "spec_id": version_doc.get("spec_id"),
            "evaluation": dict(evaluation_doc),
            "decision": decision,
        }

    async def publish_version(self, version_id: str) -> Dict[str, Any]:
        """发布版本：激活 agent_configs，AgentFactory/工作流编排器立即可用。

        返回契约：{status(新状态), version_id, spec_id, agent_id}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"status": "error", "message": f"版本 {version_id} 不存在"}

        # 发布门禁：需要至少一次 pass 评估
        latest_eval = await self.db[EVALUATION_COLLECTION].find_one(
            {"version_id": version_id}, sort=[("created_at", -1)]
        )
        if not latest_eval or str(latest_eval.get("decision")) != "pass":
            return {
                "status": "error",
                "message": "发布前需要至少一次评估通过（decision=pass）；请先运行真数据测试并评估",
                "version_id": version_id,
            }

        spec_id = str(version_doc.get("spec_id") or "")
        await self.db[VERSION_COLLECTION].update_one(
            {"version_id": version_id},
            {"$set": {"status": VERSION_PUBLISHED, "published_at": _now_iso(), "updated_at": _now_iso()}},
        )
        await self.db[CONFIG_COLLECTION].update_one(
            {"agent_id": spec_id},
            {"$set": {
                "enabled": True,
                "version_status": "active",
                "runtime_status": "active",
                "updated_at": _now_iso(),
                "metadata.latest_workshop_version_id": version_id,
            }},
        )
        await self.db[SPEC_COLLECTION].update_one(
            {"spec_id": spec_id},
            {"$set": {"status": "active", "current_version_id": version_id, "updated_at": _now_iso()}},
        )
        session_id = str(version_doc.get("published_from_session_id") or "")
        if session_id:
            await self.db[SESSION_COLLECTION].update_one(
                {"session_id": session_id},
                {"$set": {"status": "published", "updated_at": _now_iso()}},
            )
        return {"status": VERSION_PUBLISHED, "version_id": version_id, "spec_id": spec_id, "agent_id": spec_id}

    async def iterate_version(self, version_id: str, feedback: str = "") -> Dict[str, Any]:
        """基于反馈开下一轮迭代（新会话 + 新 draft 版本，版本号+1）。

        返回契约：{source_version_id, session_id, spec_id, version_id, version_status}
        """
        if self.db is None:
            return {"status": "error", "message": "数据库不可用"}
        version_doc = await self.db[VERSION_COLLECTION].find_one({"version_id": version_id})
        if not version_doc:
            return {"status": "error", "message": f"版本 {version_id} 不存在"}
        status = str(version_doc.get("status") or "").lower()
        if status not in (VERSION_EVALUATION, VERSION_PUBLISH_READY, VERSION_PUBLISHED):
            return {"status": "error", "message": f"版本处于 {status} 阶段，需先完成测试评估再迭代"}

        spec_id = str(version_doc.get("spec_id") or "")
        source_session_id = str(version_doc.get("published_from_session_id") or "")

        # 新会话（带反馈与上一版快照）
        new_session_id = _new_id("ws")
        await self.db[SESSION_COLLECTION].insert_one({
            "session_id": new_session_id,
            "spec_id": spec_id,
            "agent_id": spec_id,
            "agent_name": version_doc.get("agent_name") or spec_id,
            "user_id": "nanobot",
            "status": "draft_preparing",
            "current_spec_snapshot": {},
            "gap_report": None,
            "feasibility_status": "feasible",
            "completeness_score": 0.0,
            "builder_contracts": {
                "iteration_of": version_id,
                "iteration_feedback": feedback,
                "tooling_contract": {"required_tools": list(version_doc.get("required_tools") or [])},
                "output_field": version_doc.get("output_field") or "analysis_report",
            },
            "source_session_id": source_session_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        })

        # 新 draft 版本（继承上一版工具）
        new_version_id = _new_id("ver")
        await self.db[VERSION_COLLECTION].insert_one({
            "version_id": new_version_id,
            "spec_id": spec_id,
            "session_id": new_session_id,
            "version_number": int(version_doc.get("version_number") or 1) + 1,
            "status": VERSION_DRAFT,
            "required_tools": list(version_doc.get("required_tools") or []),
            "required_capabilities": list(version_doc.get("required_capabilities") or []),
            "runtime_contract": dict(version_doc.get("runtime_contract") or {}),
            "tooling_contract": dict(version_doc.get("tooling_contract") or {}),
            "output_field": version_doc.get("output_field") or "analysis_report",
            "published_from_session_id": new_session_id,
            "iteration_of": version_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        })
        await self.db[SPEC_COLLECTION].update_one(
            {"spec_id": spec_id},
            {"$set": {"current_version_id": new_version_id, "updated_at": _now_iso()}},
        )
        return {
            "status": "ok",
            "source_version_id": version_id,
            "session_id": new_session_id,
            "spec_id": spec_id,
            "version_id": new_version_id,
            "version_status": VERSION_DRAFT,
        }

    # ==================== 检索（SkillCenter 绑定下拉同源） ====================

    async def search_capabilities(self, query: str, top_k: int = 8) -> List[Any]:
        """能力检索（转发 CapabilityIndexService）"""
        if self.db is None:
            return []
        try:
            from app.services.capability_index_service import CapabilityIndexService
            return await CapabilityIndexService(self.db).search_capabilities(
                query=query, top_k=top_k, bindable_only=True,
            )
        except Exception as exc:
            logger.warning("[AgentWorkshop] 能力检索失败: %s", exc)
            return []
