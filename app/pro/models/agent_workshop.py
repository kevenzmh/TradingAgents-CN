"""Agent 工坊数据模型（社区版重建）

原 Pro 版为 AgentSpec 契约体系的完整实现（含版本治理/评估工作流）。社区版按
消费方契约重建：字段名与 nanobot_agent_builder_tools.py 的赋值/读取语句一一对应。

重建范围（消费方实际用到的）：
- AgentSpec / AgentSpecIOField / AgentSpecStatus（nanobot 工具构造 + 集合读写）
- TemplateContent（提示词模板内容模型）
- 会话快照形状（gap_report / current_spec_snapshot 等以 dict 属性访问）

不重建：AgentVersion / Evaluation 的完整 Pydantic 模型——消费方对 version 采取
"dict 或对象皆可" 的宽容解析（model_dump / dict / __dict__ 三级兜底），社区版
直接返回 dict 即满足全部契约。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ==================== AgentSpec ====================

class AgentSpecStatus(str, Enum):
    """Agent 规格状态机"""
    DRAFT = "draft"              # 草稿（会话确认中）
    TESTING = "testing"          # 版本测试中
    ACTIVE = "active"            # 已发布生效
    DEPRECATED = "deprecated"    # 已废弃


class AgentSpecIOField(BaseModel):
    """Agent 输入/输出字段定义"""
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""


class CapabilityRequirement(BaseModel):
    """能力需求条目（来自职责/输出形状/方法范围）"""
    capability_key: str = ""
    capability_name: str = ""
    capability_type: str = ""          # orchestration / output
    description: str = ""
    delivery_requirement: str = "tool-or-prompt"
    acceptance_criteria: List[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    """Agent 规格（一个自定义 Agent 的契约定义）"""
    spec_id: str                       # 全局唯一，与 agent_id 一致
    name: str                          # 显示名
    domain: str = "general"            # stock_analysis / general
    primary_goal: str = ""
    responsibilities: List[str] = Field(default_factory=list)
    non_goals: List[str] = Field(default_factory=list)
    inputs: List[AgentSpecIOField] = Field(default_factory=list)
    outputs: List[AgentSpecIOField] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    preferred_methods: List[str] = Field(default_factory=list)
    capability_requirements: List[CapabilityRequirement] = Field(default_factory=list)
    status: AgentSpecStatus = AgentSpecStatus.DRAFT
    source: str = ""                   # nanobot_prepare / manual 等
    current_version_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        use_enum_values = True


# ==================== GapReport ====================

class GapItem(BaseModel):
    """单条能力缺口"""
    gap_id: str = ""
    description: str = ""
    capability_key: str = ""
    blocking: bool = False
    suggested_resolution: str = ""     # bind_existing / create_skill 等


class AgentWorkshopGapAnalysis(BaseModel):
    """缺口分析结果（analyze_gaps 返回）"""
    blocking_gaps: List[GapItem] = Field(default_factory=list)
    non_blocking_gaps: List[GapItem] = Field(default_factory=list)
    available_tools: List[str] = Field(default_factory=list)
    available_skills: List[str] = Field(default_factory=list)
    missing_capabilities: List[str] = Field(default_factory=list)
    completeness_score: float = 0.0
    feasibility_status: str = "feasible"
    analyzed_at: Optional[str] = None


# ==================== 提示词模板 ====================

class TemplateContent(BaseModel):
    """提示词模板内容（写入 prompt_templates.content）"""
    system_prompt: str = ""
    user_prompt: str = ""
    tool_guidance: str = ""
    analysis_requirements: str = ""
    output_format: str = ""
    constraints: str = ""


# ==================== 会话 ====================

class WorkshopSession(BaseModel):
    """Agent 工坊会话（构建一个 Agent 的交互上下文）

    消费方通过属性访问：spec_id / user_id / gap_report / current_spec_snapshot
    """
    session_id: str
    spec_id: str = ""
    agent_id: str = ""                 # 与 spec_id 一致（nanobot 传入）
    agent_name: str = ""
    user_id: str = ""
    status: str = "draft_preparing"    # 阶段机：requirement_confirmation_pending /
                                       # capability_check / plan_selection_pending /
                                       # draft_preparing / building / testing / published
    current_spec_snapshot: Dict[str, Any] = Field(default_factory=dict)
    gap_report: Optional[AgentWorkshopGapAnalysis] = None
    feasibility_status: str = "feasible"
    completeness_score: float = 0.0
    builder_contracts: Dict[str, Any] = Field(default_factory=dict)
    current_version_id: Optional[str] = None
    source_thread_id: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
