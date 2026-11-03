"""工作流生成会话服务（社区版重建：最小可用契约）

原 Pro 版为 AI 引导式工作流优化会话的完整实现（对话式生成新流程版本）。
社区版重建调用方（app/services/workflow_growth_service.py:1139
_execute_suggested_action）引用的契约：构造 (db) + start_session(description,
user_id) 返回 {session_id, ai_message}。

会话本体（对话推进/生成/落库）依赖 Pro 工作流编辑管道，社区版调用方
对 ImportError 已有优雅降级提示；本实现提供最小可用会话记录与回应，
建议采纳入口不再因 ImportError 失败。
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WORKFLOW_GENERATION_SESSIONS_COLLECTION = "workflow_generation_sessions"


class WorkflowGenerationService:
    """AI 工作流优化会话（社区版：单轮引导）"""

    def __init__(self, db: Any = None):
        self.db = db

    async def start_session(
        self,
        description: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """启动优化会话，返回 {session_id, ai_message}"""
        session_id = str(uuid.uuid4())
        ai_message = (
            f"已收到工作流优化需求：{description}。\n"
            "社区版暂不支持 AI 引导式流程生成（可视化流程编辑为 Pro 能力）；"
            "你可以通过\"设置 → 分析偏好\"调整默认分析流，或在分析页的"
            "\"分析流程\"下拉中选择不同的预置流程。"
        )
        if self.db is not None:
            try:
                await self.db[WORKFLOW_GENERATION_SESSIONS_COLLECTION].insert_one({
                    "session_id": session_id,
                    "user_id": user_id,
                    "description": description,
                    "status": "community_limited",
                    "created_at": datetime.utcnow(),
                })
            except Exception as e:
                logger.warning(f"[WorkflowGeneration] 会话记录写入失败: {e}")
        return {"session_id": session_id, "ai_message": ai_message}
