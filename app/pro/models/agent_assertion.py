"""Agent 断言模型（P2：反思 lesson 升级为可执行断言）

**社区版重建说明**
原 Pro 版包含完整的断言体系（agent_assertions 集合 + 管理界面）。Pro 开源分离时
`app/models/agent_assertion.py` 与 `app/services/agent_assertion_service.py` 被整体剔除，
但消费方保留了下来：`core/agents/assertion_executor.py`、`core/agents/manager.py`、
`core/agents/adapters/trader_v2.py` 全部走 try-import，导入失败即静默降级
（`AGENT_ASSERTION_ENABLED` 默认 True 却永远不生效）。

本文件按消费方契约重建，字段名与 `assertion_executor.py` 的读取/构造语句一一对应：

| 消费方用法 | 本模型提供 |
|---|---|
| `assertion.assertion_id` / `.assertion_type` / `.target_agent` | 同名字段 |
| `assertion.severity` / `.description` / `.target_field` | 同名字段 |
| `assertion.constraint.model_dump().get(key)` | AssertionConstraint(extra="allow") |
| `AssertionViolation(assertion_id=..., violation_detail=...)` | 同名字段 |
| `AssertionRunResult(ran=..., violations=..., has_error_violation=..., error_count=..., warning_count=...)` | 同名字段 |
| `v.severity == AssertionSeverity.ERROR` | 枚举成员 |

七种断言类型对应 `AssertionExecutor._HANDLERS` 的七个 handler，不能增减名字。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==================== 枚举 ====================

class AssertionType(str, Enum):
    """断言类型（与 AssertionExecutor._HANDLERS 的键严格一致）"""
    TERM_FORBIDDEN = "term_forbidden"        # 禁用术语扫描（原始文本）
    FIELD_REQUIRED = "field_required"        # 必填字段（parsed dict）
    FIELD_ENUM = "field_enum"                # 字段枚举约束
    FIELD_RANGE = "field_range"              # 数值范围约束
    SCHEMA_MATCH = "schema_match"            # 整份输出匹配 schema
    REGEX_MATCH = "regex_match"              # 字段正则匹配
    SECTION_REQUIRED = "section_required"    # 必备章节（原始文本）


class AssertionSeverity(str, Enum):
    """断言严重级别

    ERROR：违规会把 validation_status 降级为 failed（影响 agent 产出判定）
    WARNING：只记录，不影响 validation_status
    """
    ERROR = "error"
    WARNING = "warning"


# ==================== 约束 ====================

class AssertionConstraint(BaseModel):
    """断言约束参数（按 assertion_type 使用不同键）

    - term_forbidden      → terms: List[str]
    - field_required      → field: str
    - field_enum          → field: str, allowed: List[Any]
    - field_range         → field: str, min: float, max: float
    - schema_match        → schema_name: str（RiskAssessmentOutput / ResearchManagerOutput）
    - regex_match         → field: str（"*" 表示作用于原文）, pattern: str
    - section_required    → sections: List[str]

    extra="allow"：消费方以 `constraint.model_dump().get(key)` 通用读取，
    新增约束键不应需要改模型（与原 Pro 版 extra 语义保持一致）。
    """
    model_config = ConfigDict(extra="allow")

    terms: Optional[List[str]] = None
    field: Optional[str] = None
    allowed: Optional[List[Any]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    schema_name: Optional[str] = None
    pattern: Optional[str] = None
    sections: Optional[List[str]] = None


# ==================== 断言 ====================

class AgentAssertion(BaseModel):
    """单条断言定义（agent_assertions 集合文档）

    target_agent 支持 "*"（对所有会跑断言的 Agent 生效），
    实际取值：risk_manager(_v2) / research_manager(_v2) / trader(_v2)。
    """
    assertion_id: str
    assertion_type: AssertionType
    target_agent: str = "*"
    severity: AssertionSeverity = AssertionSeverity.WARNING
    description: str = ""
    target_field: str = "*"
    constraint: AssertionConstraint = Field(default_factory=AssertionConstraint)

    enabled: bool = True
    source: str = ""                    # reflection_lesson / manual / seed
    source_lesson_id: Optional[str] = None
    hit_count: int = 0
    last_hit_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(use_enum_values=True)


# ==================== 执行结果 ====================

class AssertionViolation(BaseModel):
    """单条断言违规记录（model_dump() 后写入执行轨迹）"""
    assertion_id: str
    assertion_type: Any = ""
    target_agent: str = ""
    severity: Any = AssertionSeverity.WARNING
    description: str = ""
    violation_detail: str = ""
    detected_at: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if self.detected_at is None:
            self.detected_at = datetime.utcnow().isoformat()


class AssertionRunResult(BaseModel):
    """一次断言执行的整体结果（消费方按属性读，也用 model_dump 落库）"""
    ran: bool = False
    violations: List[AssertionViolation] = Field(default_factory=list)
    has_error_violation: bool = False
    error_count: int = 0
    warning_count: int = 0

    model_config = ConfigDict(use_enum_values=True)
