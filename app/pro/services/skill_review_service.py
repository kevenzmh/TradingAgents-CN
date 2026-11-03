"""Skill 审核服务（社区版重建：最小可用契约）

原 Pro 版为 Agent 工坊缺口补齐的 HITL 审核队列完整实现。社区版已按
契约重建两个被引用的方法（app/main.py 定时清理 + nanobot_agent_builder_tools
写入待审核记录）；Agent 工坊本体（app/pro/services/agent_workshop_service）
不在社区版，写入方上游调用永不触发，本服务保持占位可用。
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SKILL_REVIEW_COLLECTION = "skill_review_queue"
REVIEW_STALE_HOURS = 72  # 3 天未处理视为过期


class SkillReviewService:
    """Skill 审核队列服务"""

    def __init__(self, db: Any = None):
        self.db = db

    async def create_pending_reviews_for_workshop(
        self,
        workshop_session_id: str,
        agent_spec_id: str,
        agent_name: str,
        user_id: str,
        successful_items: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """为 Agent 工坊会话的成功执行项写入待审核记录（HITL 路径B）"""
        if self.db is None:
            return []
        now = datetime.utcnow()
        expires_at = now + timedelta(hours=REVIEW_STALE_HOURS)
        docs = []
        for item in (successful_items or []):
            doc = {
                "workshop_session_id": workshop_session_id,
                "agent_spec_id": agent_spec_id,
                "agent_name": agent_name,
                "user_id": user_id,
                "item": item,
                "status": "pending",
                "created_at": now,
                "expires_at": expires_at,
            }
            docs.append(doc)
        if docs:
            await self.db[SKILL_REVIEW_COLLECTION].insert_many(docs)
            logger.info(f"[SkillReview] 写入 {len(docs)} 条待审核记录 (session={workshop_session_id})")
        return docs

    async def expire_stale_reviews(self) -> int:
        """过期未处理的待审核记录标记为 expired（定时任务每 30 分钟调用）"""
        if self.db is None:
            return 0
        cutoff = datetime.utcnow() - timedelta(hours=REVIEW_STALE_HOURS)
        try:
            result = await self.db[SKILL_REVIEW_COLLECTION].update_many(
                {"status": "pending", "created_at": {"$lt": cutoff}},
                {"$set": {"status": "expired", "expired_at": datetime.utcnow()}},
            )
            return result.modified_count or 0
        except Exception as e:
            logger.warning(f"[SkillReview] 过期清理失败: {e}")
            return 0
