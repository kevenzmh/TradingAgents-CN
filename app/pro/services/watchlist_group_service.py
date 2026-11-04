"""自选股分组服务（Pro）

数据落在 watchlist_groups 集合，字段与 AI 助理工具（core/tools/.../watchlist_manager.py）
以及定时分析执行器（app/worker/watchlist_analysis_task.py）完全一致：

    user_id(str) / name / description / color / icon / stock_codes[str] /
    analysis_depth / quick_analysis_model / deep_analysis_model / prompt_template_id /
    sort_order / is_active / created_at / updated_at

注意 user_id 在该集合里是**字符串**（不是 ObjectId），_id 是 ObjectId。
分组被定时分析配置引用时禁止删除：删掉后定时任务会静默跳过该分组，用户察觉不到。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId

COLLECTION = "watchlist_groups"
CONFIG_COLLECTION = "scheduled_analysis_configs"

MAX_GROUPS = 50
MAX_STOCKS_PER_GROUP = 100
MAX_NAME_LEN = 30
MAX_DESC_LEN = 200
DEFAULT_COLOR = "#409EFF"

_CODE_RE = re.compile(r"^[0-9A-Z][0-9A-Z.\-]{0,11}$")
_CN_SUFFIX_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ|SS)$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class GroupError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def to_oid(value: Any, what: str = "分组ID") -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        raise GroupError(f"无效的{what}", 400)


# ==================== 规整 ====================

def normalize_code(raw: Any) -> Optional[str]:
    """规整股票代码：去空白、大写；A 股 000001.SZ 之类去掉交易所后缀（与自选股存储一致）。非法返回 None。"""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    m = _CN_SUFFIX_RE.match(code)
    if m:
        code = m.group(1)
    return code if _CODE_RE.match(code) else None


def normalize_codes(raw: Iterable[Any]) -> Tuple[List[str], List[str]]:
    """返回 (去重保序的合法代码, 非法输入)"""
    seen: Dict[str, None] = {}
    invalid: List[str] = []
    for item in raw or []:
        code = normalize_code(item)
        if code is None:
            invalid.append(str(item))
        else:
            seen.setdefault(code, None)
    return list(seen), invalid


def normalize_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise GroupError("分组名称不能为空")
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        raise GroupError(f"分组名称不能超过 {MAX_NAME_LEN} 字符")
    return name


def normalize_color(color: Any) -> str:
    if color in (None, ""):
        return DEFAULT_COLOR
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise GroupError("颜色必须是 #RRGGBB 格式")
    return color


def normalize_depth(depth: Any) -> Optional[int]:
    if depth in (None, ""):
        return None
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 5:
        raise GroupError("分析深度必须是 1-5 的整数")
    return depth


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    codes = list(doc.get("stock_codes") or [])
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "description": doc.get("description", ""),
        "color": doc.get("color") or DEFAULT_COLOR,
        "stock_codes": codes,
        "stock_count": len(codes),
        "analysis_depth": doc.get("analysis_depth"),
        "sort_order": doc.get("sort_order", 0),
        "is_active": bool(doc.get("is_active", True)),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else v


# ==================== 查询 ====================

async def list_groups(db: Any, user_id: str) -> List[Dict[str, Any]]:
    cursor = db[COLLECTION].find({"user_id": user_id}).sort([("sort_order", 1), ("created_at", 1)])
    return [serialize(d) async for d in cursor]


async def _get(db: Any, user_id: str, group_id: Any) -> Dict[str, Any]:
    doc = await db[COLLECTION].find_one({"_id": to_oid(group_id), "user_id": user_id})
    if not doc:
        raise GroupError("分组不存在", 404)
    return doc


async def resolve_names(db: Any, codes: Iterable[str]) -> Dict[str, str]:
    """批量查股票/ETF 名称，查不到的不返回。"""
    wanted = list({c for c in codes if c})
    if not wanted:
        return {}
    variants = wanted + [c.zfill(6) for c in wanted if c.isdigit() and len(c) < 6]
    names: Dict[str, str] = {}
    for coll in ("stock_basic_info", "etf_basic_info"):
        async for d in db[coll].find({"code": {"$in": variants}}, {"code": 1, "name": 1, "_id": 0}):
            code, name = str(d.get("code")), d.get("name")
            if name and code not in names:
                names[code] = name
    return names


# ==================== 写入 ====================

async def _ensure_unique_name(db: Any, user_id: str, name: str, exclude: Optional[ObjectId] = None) -> None:
    q: Dict[str, Any] = {"user_id": user_id, "name": name}
    if exclude is not None:
        q["_id"] = {"$ne": exclude}
    if await db[COLLECTION].find_one(q, {"_id": 1}):
        raise GroupError(f"已存在同名分组：{name}", 409)


async def create_group(db: Any, user_id: str, *, name: str, description: str = "", color: Optional[str] = None,
                       stock_codes: Optional[List[str]] = None, analysis_depth: Optional[int] = None,
                       is_active: bool = True) -> Dict[str, Any]:
    name = normalize_name(name)
    description = (description or "").strip()
    if len(description) > MAX_DESC_LEN:
        raise GroupError(f"描述不能超过 {MAX_DESC_LEN} 字符")
    codes, invalid = normalize_codes(stock_codes or [])
    if invalid:
        raise GroupError(f"股票代码格式不正确：{'、'.join(invalid[:5])}")
    if len(codes) > MAX_STOCKS_PER_GROUP:
        raise GroupError(f"单个分组最多 {MAX_STOCKS_PER_GROUP} 只股票")
    if await db[COLLECTION].count_documents({"user_id": user_id}) >= MAX_GROUPS:
        raise GroupError(f"最多创建 {MAX_GROUPS} 个分组")
    await _ensure_unique_name(db, user_id, name)

    last = await db[COLLECTION].find_one({"user_id": user_id}, sort=[("sort_order", -1)])
    now = datetime.utcnow()
    doc = {
        "user_id": user_id, "name": name, "description": description, "color": normalize_color(color),
        "icon": "folder", "stock_codes": codes, "analysis_depth": normalize_depth(analysis_depth),
        "quick_analysis_model": None, "deep_analysis_model": None, "prompt_template_id": None,
        "sort_order": (last.get("sort_order", 0) + 1) if last else 0,
        "is_active": bool(is_active), "created_at": now, "updated_at": now,
    }
    res = await db[COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    return serialize(doc)


_UNSET = object()


async def update_group(db: Any, user_id: str, group_id: Any, *, name: Any = _UNSET, description: Any = _UNSET,
                       color: Any = _UNSET, analysis_depth: Any = _UNSET, is_active: Any = _UNSET) -> Dict[str, Any]:
    doc = await _get(db, user_id, group_id)
    update: Dict[str, Any] = {}
    if name is not _UNSET:
        n = normalize_name(name)
        if n != doc.get("name"):
            await _ensure_unique_name(db, user_id, n, doc["_id"])
        update["name"] = n
    if description is not _UNSET:
        d = (description or "").strip()
        if len(d) > MAX_DESC_LEN:
            raise GroupError(f"描述不能超过 {MAX_DESC_LEN} 字符")
        update["description"] = d
    if color is not _UNSET:
        update["color"] = normalize_color(color)
    if analysis_depth is not _UNSET:
        update["analysis_depth"] = normalize_depth(analysis_depth)
    if is_active is not _UNSET:
        update["is_active"] = bool(is_active)
    if not update:
        raise GroupError("没有需要更新的字段")
    update["updated_at"] = datetime.utcnow()
    await db[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": update})
    return serialize(await _get(db, user_id, group_id))


async def referencing_configs(db: Any, user_id: str, group_id: str) -> List[str]:
    """引用了该分组的定时分析配置名称"""
    q = {"user_id": user_id, "$or": [{"default_group_ids": group_id}, {"time_slots.group_ids": group_id}]}
    return [c.get("name", "未命名") async for c in db[CONFIG_COLLECTION].find(q, {"name": 1})]


async def delete_group(db: Any, user_id: str, group_id: Any) -> None:
    doc = await _get(db, user_id, group_id)
    used = await referencing_configs(db, user_id, str(doc["_id"]))
    if used:
        raise GroupError(f"该分组正被定时分析配置使用：{'、'.join(used[:5])}。请先在定时分析中移除后再删除", 409)
    await db[COLLECTION].delete_one({"_id": doc["_id"]})


async def add_stocks(db: Any, user_id: str, group_id: Any, codes: Iterable[Any]) -> Dict[str, Any]:
    doc = await _get(db, user_id, group_id)
    valid, invalid = normalize_codes(codes)
    if not valid and invalid:
        raise GroupError(f"股票代码格式不正确：{'、'.join(invalid[:5])}")
    if not valid:
        raise GroupError("请提供至少一个股票代码")
    current = list(doc.get("stock_codes") or [])
    existing = set(current)
    new = [c for c in valid if c not in existing]
    if len(current) + len(new) > MAX_STOCKS_PER_GROUP:
        raise GroupError(f"单个分组最多 {MAX_STOCKS_PER_GROUP} 只股票（当前 {len(current)}，本次新增 {len(new)}）")
    if new:
        await db[COLLECTION].update_one(
            {"_id": doc["_id"]},
            {"$addToSet": {"stock_codes": {"$each": new}}, "$set": {"updated_at": datetime.utcnow()}},
        )
    return {"added": new, "skipped": [c for c in valid if c in existing], "invalid": invalid,
            "group": serialize(await _get(db, user_id, group_id))}


async def remove_stocks(db: Any, user_id: str, group_id: Any, codes: Iterable[Any]) -> Dict[str, Any]:
    doc = await _get(db, user_id, group_id)
    valid, _ = normalize_codes(codes)
    if not valid:
        raise GroupError("请提供至少一个股票代码")
    present = set(doc.get("stock_codes") or [])
    removed = [c for c in valid if c in present]
    if removed:
        await db[COLLECTION].update_one(
            {"_id": doc["_id"]},
            {"$pull": {"stock_codes": {"$in": removed}}, "$set": {"updated_at": datetime.utcnow()}},
        )
    return {"removed": removed, "group": serialize(await _get(db, user_id, group_id))}


async def import_from_favorites(db: Any, user_id: str, group_id: Any, tag: Optional[str] = None) -> Dict[str, Any]:
    """把「股票关注列表」（可按标签过滤）里的股票加入分组。"""
    await _get(db, user_id, group_id)
    fav = await db.user_favorites.find_one({"user_id": user_id}) or {}
    codes = []
    for item in fav.get("favorites") or []:
        if tag and tag not in (item.get("tags") or []):
            continue
        if item.get("stock_code"):
            codes.append(item["stock_code"])
    if not codes:
        raise GroupError("没有可导入的自选股" + (f"（标签「{tag}」下为空）" if tag else ""), 404)
    return await add_stocks(db, user_id, group_id, codes)


async def reorder(db: Any, user_id: str, ordered_ids: List[str]) -> None:
    oids = [to_oid(i) for i in ordered_ids]
    if len(set(oids)) != len(oids):
        raise GroupError("排序列表含重复分组")
    owned = {d["_id"] async for d in db[COLLECTION].find({"user_id": user_id, "_id": {"$in": oids}}, {"_id": 1})}
    if owned != set(oids):
        raise GroupError("排序列表含不存在的分组", 404)
    for index, oid in enumerate(oids):
        await db[COLLECTION].update_one({"_id": oid, "user_id": user_id}, {"$set": {"sort_order": index}})
