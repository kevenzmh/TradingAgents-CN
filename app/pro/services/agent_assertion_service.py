"""Agent 断言服务（P2：反思 lesson 升级为可执行断言）

**社区版重建说明**
Pro 开源分离时随模型一并剔除，消费方保留：

- `app/core/database.py`       → `create_indexes()`（启动时建索引）
- `core/agents/assertion_executor.py` → `load_enabled_assertions_sync(agent_id)`
                                        `increment_hit_count_sync(assertion_id)`

两条都是同步接口：断言在 agent 执行线程（worker 线程）内同步调用，不经过 event loop。
因此本服务**以同步 pymongo 为主**（`get_mongo_db_sync`），只有建索引提供 async 版本
（启动期在 FastAPI 生命周期内调用）。

缓存语义（对应 `AGENT_ASSERTION_CACHE_TTL_SECONDS`，默认 60s）：
按 agent_id 缓存已启用断言列表，避免每次 agent 执行都查 MongoDB。
`increment_hit_count_sync` 不失效缓存（命中计数不影响断言内容）。
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.pro.models.agent_assertion import AgentAssertion

logger = logging.getLogger(__name__)

ASSERTION_COLLECTION = "agent_assertions"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class AgentAssertionService:
    """断言读写服务（单例使用，模块底部导出 agent_assertion_service）"""

    def __init__(self) -> None:
        # agent_id -> (expire_at_epoch, assertions)
        self._cache: Dict[str, tuple] = {}
        self._cache_lock = threading.Lock()

    # ==================== 索引（启动期，async） ====================

    async def create_indexes(self) -> None:
        """建立断言集合索引（幂等；失败不影响应用启动）"""
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            if db is None:
                return
            collection = db[ASSERTION_COLLECTION]
            await collection.create_index([("target_agent", 1), ("enabled", 1)])
            await collection.create_index([("assertion_id", 1)], unique=True)
            await collection.create_index([("source", 1)])
            logger.info(f"✅ 断言集合索引就绪（{ASSERTION_COLLECTION}）")
        except Exception as e:
            logger.warning(f"⚠️ 断言集合索引创建失败（不影响应用启动）: {e}")

    # ==================== 读取（同步，worker 线程） ====================

    def _get_collection_sync(self):
        from app.core.database import get_mongo_db_sync
        db = get_mongo_db_sync()
        if db is None:
            return None
        return db[ASSERTION_COLLECTION]

    def _cache_ttl(self) -> int:
        try:
            from app.core.config import settings
            return int(getattr(settings, "AGENT_ASSERTION_CACHE_TTL_SECONDS", 60) or 60)
        except Exception:
            return 60

    def load_enabled_assertions_sync(self, agent_id: str) -> List[AgentAssertion]:
        """加载某 Agent 的启用断言（含 "*" 通配断言），带进程内 TTL 缓存。

        返回空列表表示该 Agent 没有断言——断言执行器据此返回 ran=False。
        """
        clean_agent_id = str(agent_id or "").strip()
        if not clean_agent_id:
            return []

        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(clean_agent_id)
            if cached and cached[0] > now:
                return list(cached[1])

        assertions = self._query_assertions_sync(clean_agent_id)

        with self._cache_lock:
            self._cache[clean_agent_id] = (now + self._cache_ttl(), assertions)
        return list(assertions)

    def _query_assertions_sync(self, agent_id: str) -> List[AgentAssertion]:
        """实际查库（未命中缓存时调用）"""
        try:
            collection = self._get_collection_sync()
            if collection is None:
                return []
            docs = list(collection.find({
                "enabled": {"$ne": False},
                "target_agent": {"$in": [agent_id, "*"]},
            }))
        except Exception as e:
            logger.warning(f"⚠️ 断言加载失败（不影响 agent）: agent_id={agent_id}, error={e}")
            return []

        assertions: List[AgentAssertion] = []
        for doc in docs:
            doc = dict(doc)
            doc.pop("_id", None)
            try:
                assertions.append(AgentAssertion(**doc))
            except Exception as e:
                # 单条脏数据隔离：不因为一条断言格式问题丢掉全部断言
                logger.warning(
                    f"⚠️ 断言文档无效（跳过）: assertion_id={doc.get('assertion_id')}, error={e}"
                )
        # ERROR 级排前，同级别按创建时间稳定排序，便于日志定位
        assertions.sort(key=lambda a: (
            str(getattr(a, "severity", "warning")) != "error",
            str(getattr(a, "created_at", "") or ""),
        ))
        logger.debug(
            f"[AssertionService] 已加载 {len(assertions)} 条断言: agent_id={agent_id}"
        )
        return assertions

    def invalidate_cache(self, agent_id: Optional[str] = None) -> None:
        """失效缓存（agent_id 为空则全清）。断言增删改后由写侧调用。"""
        with self._cache_lock:
            if agent_id:
                self._cache.pop(str(agent_id).strip(), None)
            else:
                self._cache.clear()

    # ==================== 写入（同步） ====================

    def increment_hit_count_sync(self, assertion_id: str) -> None:
        """命中计数自增（fire-and-forget，失败静默）"""
        clean = str(assertion_id or "").strip()
        if not clean:
            return
        try:
            collection = self._get_collection_sync()
            if collection is None:
                return
            collection.update_one(
                {"assertion_id": clean},
                {"$inc": {"hit_count": 1}, "$set": {"last_hit_at": _now_iso()}},
            )
        except Exception as e:
            logger.debug(f"断言命中计数自增失败（不影响主流程）: {clean}: {e}")

    def upsert_assertion_sync(self, assertion: AgentAssertion) -> bool:
        """新增或更新一条断言（写侧统一入口；成功后失效相关缓存）"""
        try:
            collection = self._get_collection_sync()
            if collection is None:
                return False
            doc = assertion.model_dump()
            doc["updated_at"] = _now_iso()
            # created_at 交给 $setOnInsert 独占：同一路径若同时出现在 $set 与
            # $setOnInsert 中，MongoDB 会报 "would create a conflict"
            doc.pop("created_at", None)
            collection.update_one(
                {"assertion_id": assertion.assertion_id},
                {
                    "$set": doc,
                    "$setOnInsert": {"created_at": _now_iso()},
                },
                upsert=True,
            )
            self.invalidate_cache(assertion.target_agent)
            if assertion.target_agent != "*":
                self.invalidate_cache("*")
            return True
        except Exception as e:
            logger.warning(f"⚠️ 断言写入失败: {assertion.assertion_id}: {e}")
            return False

    def disable_assertion_sync(self, assertion_id: str) -> bool:
        """停用一条断言（软删除：保留历史与命中计数）"""
        clean = str(assertion_id or "").strip()
        if not clean:
            return False
        try:
            collection = self._get_collection_sync()
            if collection is None:
                return False
            result = collection.update_one(
                {"assertion_id": clean},
                {"$set": {"enabled": False, "updated_at": _now_iso()}},
            )
            self.invalidate_cache()  # 不知道 target_agent，全清更安全
            return bool(result.modified_count)
        except Exception as e:
            logger.warning(f"⚠️ 断言停用失败: {clean}: {e}")
            return False

    # ==================== 管理侧（async，供路由/脚本使用） ====================

    async def list_assertions(
        self,
        target_agent: Optional[str] = None,
        enabled_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """列出断言（管理/排查用）"""
        try:
            from app.core.database import get_mongo_db
            db = get_mongo_db()
            if db is None:
                return []
            query: Dict[str, Any] = {}
            if target_agent:
                query["target_agent"] = target_agent
            if enabled_only:
                query["enabled"] = {"$ne": False}
            cursor = db[ASSERTION_COLLECTION].find(query, {"_id": 0})
            return await cursor.to_list(length=500)
        except Exception as e:
            logger.warning(f"⚠️ 断言列表查询失败: {e}")
            return []


# 全局单例（消费方 `from ... import agent_assertion_service`）
agent_assertion_service = AgentAssertionService()
