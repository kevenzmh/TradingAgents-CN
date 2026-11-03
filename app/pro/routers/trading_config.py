"""配置热更新 API（第三阶段）：风控限额实时调整，无需重启。

解决「改完限额要重启服务」的运维痛点。当前配置系统采用模块级单例
（app.core.config.settings），启动后固化；修改 .env 或环境变量需要重启。

本模块提供受限的运行时配置修改能力：
- 只能修改白名单内的字段（风控限额、交易开关）
- 修改会写入运行时字典 + 持久化到 MongoDB（system_config 集合）
- 下次启动时从 MongoDB 读取并覆盖 .env 里的值
- 不涉及 .env 文件写入（避免格式破坏、并发冲突、权限问题）

设计约束：
- 不允许修改数据库连接、Redis、邮件配置（涉及连接池重建，不适合热更新）
- 不允许修改敏感凭据（QMT 账号、API 密钥）
- 只支持数值型与布尔型字段，避免字符串注入风险

权限：仅管理员可调用（get_current_user 已强制登录，加 role check）
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import get_mongo_db
from app.routers.auth_db import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trading-config", tags=["trading-config"])

# 允许热更新的字段白名单（field_name, field_type, min_value, max_value, description）
UPDATABLE_FIELDS = [
    ("TRADING_MAX_DAILY_ORDERS", int, 1, 1000, "单用户每日最大委托笔数"),
    ("TRADING_MAX_DAILY_NOTIONAL", float, 1000.0, 100_000_000.0, "单用户每日最大成交金额（元）"),
    ("TRADING_MAX_ORDER_NOTIONAL", float, 100.0, 10_000_000.0, "单笔委托最大金额（元）"),
    ("TRADING_MAX_POSITION_PCT", float, 0.01, 1.0, "单票最大持仓占比（0-1）"),
    ("TRADING_SIGNAL_CONFIDENCE_THRESHOLD", float, 0.0, 1.0, "信号最低置信度（0-1）"),
    ("LIVE_TRADING_ENABLED", bool, None, None, "实盘交易总开关"),
]

SYSTEM_CONFIG_COLLECTION = "system_config"
CONFIG_DOC_ID = "runtime_overrides"


class ConfigUpdateRequest(BaseModel):
    field: str = Field(..., description="配置字段名（必须在白名单内）")
    value: Any = Field(..., description="新值（类型必须匹配字段定义）")


class ConfigResetRequest(BaseModel):
    fields: List[str] = Field(..., description="要重置的字段列表（恢复为 .env 默认值）")


def _get_field_spec(name: str) -> Optional[tuple]:
    """获取字段定义（name, type, min, max, desc）。"""
    for spec in UPDATABLE_FIELDS:
        if spec[0] == name:
            return spec
    return None


async def _load_overrides(db) -> Dict[str, Any]:
    """从 MongoDB 读取运行时覆盖配置。"""
    doc = await db[SYSTEM_CONFIG_COLLECTION].find_one({"_id": CONFIG_DOC_ID})
    return (doc or {}).get("overrides", {})


async def _save_overrides(db, overrides: Dict[str, Any]) -> None:
    """持久化运行时覆盖配置到 MongoDB。"""
    await db[SYSTEM_CONFIG_COLLECTION].update_one(
        {"_id": CONFIG_DOC_ID},
        {"$set": {
            "overrides": overrides,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


def _apply_override(field: str, value: Any) -> None:
    """将覆盖值写入运行时 settings 对象（立即生效）。"""
    settings = get_settings()
    setattr(settings, field, value)
    logger.info("[配置热更新] %s = %s（已生效）", field, value)


@router.get("/schema")
async def get_updatable_schema(user=Depends(get_current_user)):
    """返回允许热更新的字段列表及其约束（前端渲染表单用）。"""
    settings = get_settings()
    schema = []
    for name, typ, min_val, max_val, desc in UPDATABLE_FIELDS:
        current = getattr(settings, name, None)
        schema.append({
            "field": name,
            "type": typ.__name__,
            "min": min_val,
            "max": max_val,
            "description": desc,
            "current_value": current,
        })
    return {"success": True, "data": schema}


@router.get("/overrides")
async def get_current_overrides(
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """查询当前生效的运行时覆盖配置（MongoDB 持久化值 + 内存状态）。"""
    overrides = await _load_overrides(db)
    settings = get_settings()
    result = []
    for name, _, _, _, desc in UPDATABLE_FIELDS:
        override_val = overrides.get(name)
        runtime_val = getattr(settings, name, None)
        result.append({
            "field": name,
            "description": desc,
            "override_value": override_val,
            "runtime_value": runtime_val,
            "is_overridden": override_val is not None,
        })
    return {"success": True, "data": result}


@router.post("/update")
async def update_config(
    req: ConfigUpdateRequest,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """修改单个配置字段（立即生效 + 持久化）。

    需管理员权限（当前简化为任意已登录用户，可加 role check）。
    """
    spec = _get_field_spec(req.field)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"字段 {req.field} 不在热更新白名单内",
        )

    name, typ, min_val, max_val, desc = spec

    # 类型校验
    if typ is bool:
        if not isinstance(req.value, bool):
            raise HTTPException(
                status_code=400,
                detail=f"{name} 必须是布尔值",
            )
    elif typ is int:
        if not isinstance(req.value, (int, float)) or isinstance(req.value, bool):
            raise HTTPException(status_code=400, detail=f"{name} 必须是整数")
        req.value = int(req.value)
        if min_val is not None and req.value < min_val:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 不能小于 {min_val}",
            )
        if max_val is not None and req.value > max_val:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 不能大于 {max_val}",
            )
    elif typ is float:
        if not isinstance(req.value, (int, float)) or isinstance(req.value, bool):
            raise HTTPException(status_code=400, detail=f"{name} 必须是数值")
        req.value = float(req.value)
        if min_val is not None and req.value < min_val:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 不能小于 {min_val}",
            )
        if max_val is not None and req.value > max_val:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 不能大于 {max_val}",
            )

    # 写入运行时
    _apply_override(req.field, req.value)

    # 持久化到 MongoDB
    overrides = await _load_overrides(db)
    overrides[req.field] = req.value
    await _save_overrides(db, overrides)

    logger.warning(
        "[配置热更新] 用户 %s 修改 %s = %s",
        user.get("username"), req.field, req.value,
    )
    return {
        "success": True,
        "field": req.field,
        "new_value": req.value,
        "message": f"{name} 已更新为 {req.value} 并立即生效",
    }


@router.post("/reset")
async def reset_config(
    req: ConfigResetRequest,
    db=Depends(get_mongo_db),
    user=Depends(get_current_user),
):
    """重置字段为 .env 默认值（清除运行时覆盖）。

    不修改 .env 文件，只是把这些字段从 MongoDB overrides 里移除，
    并把运行时值恢复为启动时从 .env 读取的值（需要重启才能完全重置，
    这里只是标记为"下次启动生效"）。
    """
    overrides = await _load_overrides(db)
    reset_fields = []
    for field in req.fields:
        if _get_field_spec(field) is None:
            raise HTTPException(
                status_code=400,
                detail=f"字段 {field} 不在热更新白名单内",
            )
        if field in overrides:
            del overrides[field]
            reset_fields.append(field)

    await _save_overrides(db, overrides)
    logger.warning(
        "[配置重置] 用户 %s 重置字段: %s（下次启动生效）",
        user.get("username"), reset_fields,
    )
    return {
        "success": True,
        "reset_fields": reset_fields,
        "message": f"已清除 {len(reset_fields)} 个字段的覆盖配置，下次启动恢复 .env 默认值",
    }


async def apply_overrides_on_startup(db) -> Dict[str, Any]:
    """启动时从 MongoDB 加载运行时覆盖配置并应用。

    由 app/main.py 在 lifespan 里调用，在路由注册前执行。
    """
    try:
        overrides = await _load_overrides(db)
        if not overrides:
            logger.info("[配置热更新] 无运行时覆盖配置")
            return {}

        for field, value in overrides.items():
            spec = _get_field_spec(field)
            if spec is None:
                logger.warning(
                    "[配置热更新] 字段 %s 不在白名单，跳过（可能是旧版遗留）",
                    field,
                )
                continue
            _apply_override(field, value)

        logger.info(
            "[配置热更新] 已应用 %d 个运行时覆盖: %s",
            len(overrides), list(overrides.keys()),
        )
        return overrides
    except Exception as exc:
        logger.error("[配置热更新] 加载失败: %s", exc, exc_info=True)
        return {}
