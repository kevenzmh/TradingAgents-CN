"""Agent 执行轨迹服务（P1: 机器可读执行轨迹）

采集侧：同步（agent 在 worker 线程执行，recorder.finish 调 save_trace_sync）
查询侧：异步（FastAPI 路由，motor）
集合：agent_execution_traces（名字出处 app/core/config.py AGENT_EXECUTION_TRACE_ENABLED 描述）
保留策略：7 天（文档承诺，查询时懒清理）
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.pro.models.execution_trace import AgentExecutionTrace
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)

TRACE_COLLECTION = "agent_execution_traces"
TRACE_RETENTION_DAYS = 7


class ExecutionTraceService:
    """执行轨迹读写服务（单例使用，模块底部导出 execution_trace_service）"""

    # ==================== 采集侧（同步，worker 线程） ====================

    def save_trace_sync(self, trace: AgentExecutionTrace) -> Optional[str]:
        """写入一条轨迹（非阻塞：异常内部捕获返回 None）"""
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            if db is None:
                return None
            doc = trace.model_dump()
            result = db[TRACE_COLLECTION].insert_one(doc)
            return str(result.inserted_id)
        except Exception as e:
            logger.warning(f"⚠️ 执行轨迹写入失败（不影响 agent）: {e}")
            return None

    def update_validation_sync(
        self,
        trace_id: str,
        validation_status: str,
        validation_error: Optional[str] = None,
        validation_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """回填校验结果（非阻塞）"""
        try:
            from app.core.database import get_mongo_db_sync
            db = get_mongo_db_sync()
            if db is None:
                return
            db[TRACE_COLLECTION].update_one(
                {"trace_id": trace_id},
                {"$set": {
                    "validation": {
                        "validation_status": validation_status,
                        "validation_error": validation_error,
                        "validation_details": validation_details,
                    }
                }},
            )
        except Exception as e:
            logger.warning(f"⚠️ 执行轨迹回填校验失败（不影响 agent）: {e}")

    # ==================== 查询侧（异步，路由） ====================

    async def list_by_task(
        self,
        task_id: str,
        agent_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """按任务 ID 查轨迹摘要列表（排除全文大字段），按开始时间排序"""
        query: Dict[str, Any] = {"task_id": task_id}
        if agent_type:
            query["agent_type"] = agent_type
        projection = {
            "_id": 0,
            "prompts": 0,
            "inputs": 0,
            "output.output_raw_full": 0,
        }
        docs: List[Dict[str, Any]] = []
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            cursor = db[TRACE_COLLECTION].find(query, projection).sort("started_at", 1).limit(limit)
            docs = await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"查询执行轨迹失败: {e}")
        return docs

    async def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """查单条完整轨迹（含 Prompt/输出全文）"""
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            doc = await db[TRACE_COLLECTION].find_one({"trace_id": trace_id}, {"_id": 0})
            return doc
        except Exception as e:
            logger.error(f"查询执行轨迹详情失败: {e}")
            return None

    async def create_indexes(self) -> None:
        """创建集合索引（应用启动时调用，失败不影响启动）"""
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            coll = db[TRACE_COLLECTION]
            await coll.create_index([("task_id", 1), ("started_at", 1)])
            await coll.create_index([("trace_id", 1)], unique=True)
            await coll.create_index([("agent_type", 1)])
            await coll.create_index([("started_at", -1)])
        except Exception as e:
            logger.warning(f"⚠️ 执行轨迹索引创建失败: {e}")

    async def cleanup_expired(self) -> int:
        """懒清理过期轨迹（默认 7 天），返回删除条数"""
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            cutoff = (now_tz() - timedelta(days=TRACE_RETENTION_DAYS))
            result = await db[TRACE_COLLECTION].delete_many(
                {"started_at": {"$lt": cutoff}}
            )
            return result.deleted_count or 0
        except Exception as e:
            logger.warning(f"⚠️ 执行轨迹清理失败: {e}")
            return 0


execution_trace_service = ExecutionTraceService()
