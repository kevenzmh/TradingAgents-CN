"""Agent 执行轨迹数据模型（P1: 机器可读执行轨迹）

社区版重建说明：社区版开源分离时本模块随 Pro 剔除，但采集器
core/agents/execution_trace_recorder.py 的埋点契约（字段名、写入方式）保留完整。
本文件按该契约重建：字段名、类型、默认值与采集器的赋值语句一一对应。

写入方式约束（采集器代码反推）：
- 直接属性赋值：recorder 里 self._trace.ticker = ... → 模型必须可变（非 frozen）
- 列表 append：self._trace.tool_calls.append(ToolCallRecord(...)) → default_factory=list
- 整体替换：self._trace.token_usage = TokenUsage(...) → 各 snapshot 可独立构造
- trader_v2.py 直接 import OutputSnapshot 构造 → 构造签名必须兼容
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LLMConfigSnapshot(BaseModel):
    """LLM 配置快照"""
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class InputSnapshot(BaseModel):
    """输入快照（只记键名与长度，避免整个 state 落库）"""
    input_keys: List[str] = Field(default_factory=list)
    input_lengths: Dict[str, int] = Field(default_factory=dict)


class PromptSnapshot(BaseModel):
    """提示词快照：哈希/长度用于聚类扫描，全文用于前端查看与重放"""
    system_prompt_hash: Optional[str] = None
    system_prompt_length: int = 0
    user_prompt_hash: Optional[str] = None
    user_prompt_length: int = 0
    system_prompt_full: Optional[str] = None
    user_prompt_full: Optional[str] = None


class OutputSnapshot(BaseModel):
    """LLM 输出快照：全文保留最终报告；parsed_* 记录解析结果"""
    output_raw_length: int = 0
    output_raw_preview: Optional[str] = None
    output_raw_full: Optional[str] = None
    output_parsed_keys: List[str] = Field(default_factory=list)
    output_parsed_success: Optional[bool] = None
    output_parsed_type: Optional[str] = None


class ToolCallRecord(BaseModel):
    """单次工具调用记录"""
    tool_name: str
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None
    result_length: int = 0
    result_preview: Optional[str] = None
    duration_ms: Optional[int] = None


class TokenUsage(BaseModel):
    """Token 用量（采集器累加后整体替换）"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ValidationResult(BaseModel):
    """结构化输出校验结果（子类回填）"""
    validation_status: Optional[str] = None
    validation_error: Optional[str] = None
    validation_details: Optional[Dict[str, Any]] = None


class AgentExecutionTrace(BaseModel):
    """一次 agent 节点执行的完整轨迹"""
    trace_id: str
    agent_id: str
    agent_version: str
    agent_type: str
    manager_type: Optional[str] = None

    # 分析目标
    ticker: Optional[str] = None
    analysis_date: Optional[str] = None

    # 工作流上下文（state["_workflow_runtime"] 提取）
    workflow_id: Optional[str] = None
    execution_id: Optional[str] = None
    task_id: Optional[str] = None
    user_id: Optional[str] = None
    thread_id: Optional[str] = None

    # 时间与耗时
    started_at: datetime
    finished_at: Optional[datetime] = None
    execution_duration_ms: Optional[int] = None
    llm_duration_ms: Optional[int] = None
    llm_call_count: int = 0

    # 各段快照
    llm_config: LLMConfigSnapshot = Field(default_factory=LLMConfigSnapshot)
    inputs: InputSnapshot = Field(default_factory=InputSnapshot)
    prompts: PromptSnapshot = Field(default_factory=PromptSnapshot)
    output: OutputSnapshot = Field(default_factory=OutputSnapshot)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    validation: Optional[ValidationResult] = None

    # 结果
    success: bool = True
    error_message: Optional[str] = None
    error_type: Optional[str] = None
