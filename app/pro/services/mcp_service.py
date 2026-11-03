"""MCP 服务器配置服务（社区版重建：最小可用契约）

原 Pro 版为 MCP 服务器注册/发现/工具恢复的完整实现。社区版按调用方契约
重建最小功能：
- main.py 启动时 restore_enabled_servers()：从 mcp_server_configs 集合
  恢复已启用服务器（社区版集合恒空 → 恢复 0 个，消除启动 ERROR 日志）
- 消费方（analysis_profile_service / prompt_assembly_service）直接读
  mcp_server_configs 集合，不经过本服务，无需改动

MCP 服务器的注册 UI（/api/mcp* Pro 路由）不在社区版；前端 SkillCenter
的 mcphub 端点由 app/routers/skills.py 提供，与本服务无关。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

MCP_CONFIG_COLLECTION = "mcp_server_configs"


class MCPService:
    """MCP 服务器配置服务（社区版：集合空转）"""

    def __init__(self, db: Any = None):
        self.db = db

    async def restore_enabled_servers(self) -> int:
        """恢复已启用的 MCP 服务器工具到注册表。

        社区版无 MCP 注册管道，mcp_server_configs 恒空——保持查询语义
        （若用户手工插入了配置，至少不报错），返回恢复数量。
        """
        if self.db is None:
            return 0
        try:
            count = await self.db[MCP_CONFIG_COLLECTION].count_documents({"is_enabled": True})
            if count:
                logger.info(f"[MCP] 发现 {count} 个已启用服务器配置（社区版无注册管道，仅计数）")
            return count
        except Exception as e:
            logger.warning(f"[MCP] 恢复已启用服务器失败: {e}")
            return 0
