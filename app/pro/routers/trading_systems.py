"""交易计划 API 路由（重建：原 Pro 交易计划管理随社区版剔除）

前端消费方（均已存在、只差数据）：
- PositionReviewDialog / StockOperationHistoryDialog 的"关联交易计划"下拉
  （tradingSystemApi.getTradingSystems）
- 复盘引擎已支持 trading_system_id → trade_review_service._get_trading_system
  按 _id 从 trading_systems 集合读取——本路由只需按该集合形状写入

模板数据源：前端 config/tradingSystemTemplates.ts（3 套系统模板）的同构副本，
从模板一键创建，避免重建整套 Pro 可视化编辑器。
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading-systems", tags=["trading-systems"])

TRADING_SYSTEMS_COLLECTION = "trading_systems"


# ==================== 模板（与前端 tradingSystemTemplates.ts 同构） ====================

TRADING_SYSTEM_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "short_term_trend",
        "name": "短线趋势追踪系统",
        "description": "基于技术面分析，捕捉短期趋势机会，适合有时间盯盘的投资者",
        "style": "short_term",
        "risk_profile": "aggressive",
        "data": {
            "stock_selection": {
                "must_have": [
                    {"rule": "市值 > 50亿", "description": "避免小盘股流动性风险"},
                    {"rule": "流通盘 > 10亿", "description": "确保足够的流动性"},
                    {"rule": "近3日平均成交额 > 1亿", "description": "活跃度要求"},
                    {"rule": "股价在20日均线之上", "description": "趋势向上"},
                    {"rule": "近5日涨幅 > 5%", "description": "有上涨动能"},
                ],
                "exclude": [
                    {"rule": "ST股票", "description": "风险过高"},
                    {"rule": "次新股（上市不足1年）", "description": "波动过大"},
                    {"rule": "连续跌停", "description": "避免接飞刀"},
                    {"rule": "近期有重大利空消息", "description": "基本面恶化"},
                ],
            },
            "timing": {
                "entry_signals": [
                    {"signal": "大盘处于上升趋势（沪深300指数MA20 > MA60）", "description": "市场环境良好"},
                    {"signal": "股价突破前期高点", "description": "技术突破"},
                    {"signal": "MACD金叉", "description": "动能转强"},
                    {"signal": "成交量较前5日平均放大50%以上", "description": "资金关注"},
                    {"signal": "连续3日收阳", "description": "趋势确认"},
                ],
            },
            "position": {"max_per_stock": 0.15, "max_holdings": 8, "min_holdings": 3},
            "holding": {
                "review_frequency": "daily",
                "add_conditions": [
                    {"condition": "股价高于成本价5%以上", "description": "盈利加仓"},
                    {"condition": "上涨趋势未破坏", "description": "趋势完好"},
                ],
                "reduce_conditions": [
                    {"condition": "跌破5日均线", "description": "短期趋势转弱"},
                    {"condition": "成交量萎缩", "description": "动能衰竭"},
                ],
            },
            "risk_management": {
                "stop_loss": {"type": "percentage", "percentage": 0.07, "description": "固定7%止损"},
                "take_profit": {"type": "trailing", "description": "跌破5日均线止盈"},
            },
            "review": {"frequency": "每笔交易结束后立即复盘", "focus": "执行纪律与止损及时性"},
            "discipline": {
                "must_do": [
                    {"rule": "严格止损，不扛单", "description": "纪律第一"},
                    {"rule": "每日收盘后复盘持仓", "description": "及时发现问题"},
                ],
                "forbidden": [{"rule": "情绪化加仓摊平成本", "description": "严禁"}],
            },
        },
    },
    {
        "id": "medium_term_value",
        "name": "中长线价值投资系统",
        "description": "基于基本面分析，挖掘被低估的优质公司，适合没时间盯盘的投资者",
        "style": "medium_term",
        "risk_profile": "conservative",
        "data": {
            "stock_selection": {
                "must_have": [
                    {"rule": "ROE连续3年 > 15%", "description": "盈利能力稳定"},
                    {"rule": "资产负债率 < 60%", "description": "财务健康"},
                    {"rule": "经营性现金流为正", "description": "真赚钱"},
                    {"rule": "行业龙头或细分龙头", "description": "竞争壁垒"},
                ],
                "exclude": [
                    {"rule": "周期顶部的强周期股", "description": "盈利见顶风险"},
                    {"rule": "商誉占净资产比例 > 30%", "description": "减值风险"},
                ],
            },
            "timing": {
                "entry_signals": [
                    {"signal": "PE(TTM) 处于历史30%分位以下", "description": "估值低位"},
                    {"signal": "股价回调至年线附近", "description": "长期支撑"},
                ],
            },
            "position": {"max_per_stock": 0.25, "max_holdings": 5, "min_holdings": 2},
            "holding": {
                "review_frequency": "quarterly",
                "add_conditions": [
                    {"condition": "基本面逻辑强化（财报验证）", "description": "逻辑确认后加仓"},
                ],
                "reduce_conditions": [
                    {"condition": "估值分位回到70%以上", "description": "估值到位"},
                    {"condition": "基本面逻辑破坏", "description": "无条件离场"},
                ],
            },
            "risk_management": {
                "stop_loss": {"type": "logic", "description": "基本面逻辑证伪即止损"},
                "take_profit": {"type": "valuation", "description": "估值修复到位分批止盈"},
            },
            "review": {"frequency": "每季财报后", "focus": "持有逻辑是否仍成立"},
            "discipline": {
                "must_do": [{"rule": "每季财报后填写三栏对比", "description": "逻辑维护"}],
                "forbidden": [{"rule": "因短期波动改变仓位", "description": "噪音过滤"}],
            },
        },
    },
    {
        "id": "balanced_hybrid",
        "name": "均衡混合系统",
        "description": "结合趋势与基本面，仓位均衡分配，兼顾进攻与防守",
        "style": "long_term",
        "risk_profile": "balanced",
        "data": {
            "stock_selection": {
                "must_have": [
                    {"rule": "基本面无硬伤（无退市/造假风险）", "description": "底线筛查"},
                    {"rule": "行业景气度中性及以上", "description": "不逆水行舟"},
                ],
                "exclude": [{"rule": "纯题材无业绩支撑", "description": "投机标的"}],
            },
            "timing": {
                "entry_signals": [
                    {"signal": "分批建仓：首仓1/3，回调5%加仓", "description": "摊薄成本"},
                ],
            },
            "position": {"max_per_stock": 0.20, "max_holdings": 6, "min_holdings": 3},
            "holding": {"review_frequency": "monthly", "add_conditions": [], "reduce_conditions": []},
            "risk_management": {
                "stop_loss": {"type": "percentage", "percentage": 0.10, "description": "组合单票-10%止损"},
                "take_profit": {"type": "staged", "description": "分批止盈"},
            },
            "review": {"frequency": "每月一次", "focus": "组合风格漂移检查"},
            "discipline": {
                "must_do": [{"rule": "每月持仓体检", "description": "假设检验"}],
                "forbidden": [{"rule": "满仓单一板块", "description": "行业集中度控制"}],
            },
        },
    },
]


# ==================== 请求/响应模型 ====================

class CreateTradingSystemRequest(BaseModel):
    name: str = Field(..., description="系统名称")
    style: str = Field("medium_term", description="风格: short_term / medium_term / long_term")
    description: Optional[str] = None
    is_active: bool = False
    data: Optional[Dict[str, Any]] = None


class CreateFromTemplateRequest(BaseModel):
    template_id: str = Field(..., description="模板ID（short_term_trend / medium_term_value / balanced_hybrid）")


class UpdateTradingSystemRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    style: Optional[str] = None
    is_active: Optional[bool] = None
    data: Optional[Dict[str, Any]] = None


# ==================== 工具 ====================

async def _get_owned_system(db, system_id: str, user_id: str) -> Dict[str, Any]:
    from bson import ObjectId
    try:
        oid = ObjectId(system_id)
    except Exception:
        raise HTTPException(status_code=400, detail="交易计划ID无效")
    system = await db[TRADING_SYSTEMS_COLLECTION].find_one({"_id": oid, "user_id": user_id})
    if not system:
        raise HTTPException(status_code=404, detail="交易计划不存在")
    return system


# ==================== 端点 ====================

@router.get("/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    """列出可用的交易计划模板（精简版，含 data 供前端预览）"""
    return {
        "success": True,
        "data": {"templates": [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "style": t["style"],
                "risk_profile": t["risk_profile"],
                "data": t["data"],
            }
            for t in TRADING_SYSTEM_TEMPLATES
        ]},
        "message": "ok",
    }


@router.get("")
async def list_trading_systems(user: dict = Depends(get_current_user)):
    """列出当前用户的交易计划（按创建时间倒序）"""
    db = get_mongo_db()
    systems = []
    try:
        cursor = db[TRADING_SYSTEMS_COLLECTION].find({"user_id": user["id"]}).sort("created_at", -1)
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            systems.append({
                "id": doc["_id"],
                "name": doc.get("name", ""),
                "style": doc.get("style", ""),
                "description": doc.get("description"),
                "is_active": bool(doc.get("is_active")),
                "created_at": doc.get("created_at"),
            })
    except Exception as e:
        logger.error(f"查询交易计划失败: {e}")
    return {
        "success": True,
        "data": {"systems": systems},
        "message": "ok",
    }


@router.get("/{system_id}")
async def get_trading_system(system_id: str, user: dict = Depends(get_current_user)):
    """获取交易计划详情（含规则数据）"""
    db = get_mongo_db()
    system = await _get_owned_system(db, system_id, user["id"])
    system["_id"] = str(system["_id"])
    return {"success": True, "data": system, "message": "ok"}


@router.post("")
async def create_trading_system(request: CreateTradingSystemRequest, user: dict = Depends(get_current_user)):
    """创建交易计划（自定义）"""
    db = get_mongo_db()
    # is_active=True 时清除其他默认
    if request.is_active:
        await db[TRADING_SYSTEMS_COLLECTION].update_many(
            {"user_id": user["id"]}, {"$set": {"is_active": False}}
        )
    doc = {
        "user_id": user["id"],
        "name": request.name,
        "description": request.description,
        "style": request.style,
        "risk_profile": "balanced",
        "is_active": request.is_active,
        "data": request.data or {},
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await db[TRADING_SYSTEMS_COLLECTION].insert_one(doc)
    return {"success": True, "data": {"id": str(result.inserted_id)}, "message": "交易计划创建成功"}


@router.post("/from-template")
async def create_from_template(request: CreateFromTemplateRequest, user: dict = Depends(get_current_user)):
    """从模板一键创建交易计划"""
    template = next((t for t in TRADING_SYSTEM_TEMPLATES if t["id"] == request.template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"模板不存在: {request.template_id}")

    db = get_mongo_db()
    doc = {
        "user_id": user["id"],
        "name": template["name"],
        "description": template["description"],
        "style": template["style"],
        "risk_profile": template["risk_profile"],
        "is_active": False,
        "template_id": template["id"],
        "data": template["data"],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await db[TRADING_SYSTEMS_COLLECTION].insert_one(doc)
    return {"success": True, "data": {"id": str(result.inserted_id)}, "message": f"已从模板创建「{template['name']}」"}


@router.put("/{system_id}")
async def update_trading_system(
    system_id: str,
    request: UpdateTradingSystemRequest,
    user: dict = Depends(get_current_user),
):
    """更新交易计划"""
    db = get_mongo_db()
    await _get_owned_system(db, system_id, user["id"])
    updates: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.style is not None:
        updates["style"] = request.style
    if request.data is not None:
        updates["data"] = request.data
    if request.is_active is not None:
        if request.is_active:
            await db[TRADING_SYSTEMS_COLLECTION].update_many(
                {"user_id": user["id"]}, {"$set": {"is_active": False}}
            )
        updates["is_active"] = request.is_active
    from bson import ObjectId
    await db[TRADING_SYSTEMS_COLLECTION].update_one({"_id": ObjectId(system_id)}, {"$set": updates})
    return {"success": True, "message": "交易计划已更新"}


@router.delete("/{system_id}")
async def delete_trading_system(system_id: str, user: dict = Depends(get_current_user)):
    """删除交易计划"""
    db = get_mongo_db()
    await _get_owned_system(db, system_id, user["id"])
    from bson import ObjectId
    await db[TRADING_SYSTEMS_COLLECTION].delete_one({"_id": ObjectId(system_id)})
    return {"success": True, "message": "交易计划已删除"}


@router.post("/{system_id}/activate")
async def activate_trading_system(system_id: str, user: dict = Depends(get_current_user)):
    """设为默认交易计划（is_active）"""
    db = get_mongo_db()
    await _get_owned_system(db, system_id, user["id"])
    await db[TRADING_SYSTEMS_COLLECTION].update_many(
        {"user_id": user["id"]}, {"$set": {"is_active": False}}
    )
    from bson import ObjectId
    await db[TRADING_SYSTEMS_COLLECTION].update_one(
        {"_id": ObjectId(system_id)}, {"$set": {"is_active": True, "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "message": "已设为默认交易计划"}
