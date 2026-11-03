"""Agent 列表 API（社区版重建）

SkillCenter 的 Skill 绑定下拉（/api/agents）此前指向 Pro 路由，社区版
返回 404 → 下拉恒空。本路由从 agent_configs 集合列出可绑定 Agent：
- 内置 Agent：AgentRegistry 注册的 v2 智能体（分析流节点可选的全部角色）
- 工坊 Agent：agent_configs 中 enabled 的数据库驱动 Agent（UniversalAgent）
"""

import logging
from typing import List

from fastapi import APIRouter, Depends

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user
from app.core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])

# 内置 Agent 白名单（与工作流编辑器 AGENT_CATALOG 一致，v2 体系）
_BUILTIN_AGENTS = [
    ("index_analyst_v2", "大盘分析师"),
    ("sector_analyst_v2", "板块分析师"),
    ("market_analyst_v2", "市场分析师"),
    ("fundamentals_analyst_v2", "基本面分析师"),
    ("news_analyst_v2", "新闻分析师"),
    ("social_analyst_v2", "社媒分析师"),
    ("bull_researcher_v2", "乐观研究员"),
    ("bear_researcher_v2", "审慎研究员"),
    ("risky_analyst_v2", "高弹性情景研究员"),
    ("safe_analyst_v2", "防御情景研究员"),
    ("neutral_analyst_v2", "基准情景研究员"),
    ("research_manager_v2", "研究经理"),
    ("risk_manager_v2", "风险评估师"),
    ("trader_v2", "研究整合员"),
]


@router.get("")
async def list_agents(db=Depends(get_mongo_db), user: dict = Depends(get_current_user)):
    """列出可绑定 Skill 的 Agent（内置 + 工坊自定义）"""
    agents: List[dict] = [
        {"id": agent_id, "name": name, "source": "builtin"}
        for agent_id, name in _BUILTIN_AGENTS
    ]

    # 工坊 Agent（enabled 且运行时激活）
    try:
        cursor = db["agent_configs"].find(
            {"enabled": True},
            {"agent_id": 1, "name": 1, "agent_name": 1, "metadata.source": 1, "_id": 0},
        )
        async for doc in cursor:
            agent_id = str(doc.get("agent_id") or "").strip()
            if not agent_id:
                continue
            # 内置已列出的跳过（agent_configs 里也有内置记录）
            if any(a["id"] == agent_id for a in agents):
                continue
            agents.append({
                "id": agent_id,
                "name": str(doc.get("name") or doc.get("agent_name") or agent_id),
                "source": "agent_workshop",
            })
    except Exception as exc:
        logger.warning(f"读取工坊 Agent 列表失败（仅返回内置）: {exc}")

    return ok(agents)
