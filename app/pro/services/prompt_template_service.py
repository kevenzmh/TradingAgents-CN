"""提示词模板管理服务（Pro）

数据落在 Agent 运行时实际读取的两个集合上（tradingagents/utils/template_client.py）：
- prompt_templates       模板本体（系统模板 is_system=True；用户模板 is_system=False）
- user_template_configs  用户对某 Agent 的模板选择（user_id + agent_type + agent_name → template_id）

运行时优先级里“用户配置模板”高于“系统模板”，因此：
- 用户模板只有 status=active 且被 activate（写入 user_template_configs）后才会生效
- 系统模板只读；要改动请“复制为我的模板”（base_template_id 记录来源）
- 删除或停用被引用的模板时同步清理配置，避免配置指向悬空模板（运行时会静默降级，难以排查）

本模块只做数据层：所有函数接收 motor 的 db 对象，便于单测与复用。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

TEMPLATE_COLLECTION = "prompt_templates"
CONFIG_COLLECTION = "user_template_configs"

CONTENT_FIELDS = (
    "system_prompt", "user_prompt", "tool_guidance",
    "analysis_requirements", "output_format", "constraints",
)
MAX_FIELD_LEN = 50_000
MAX_NAME_LEN = 100
MAX_REMARK_LEN = 500
STATUSES = ("draft", "active")

# 与 template_client.format_template 保持一致的占位符语法
_DOUBLE_BRACE = re.compile(r"\{\{([^}]+)\}\}")
_SINGLE_BRACE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# 运行时由 template_client 自动补空的分层提示词变量（不算“未知变量”）
BUILTIN_VARIABLES = ("analysis_context_block", "capability_block", "task_block")


class TemplateError(Exception):
    """业务错误；status_code 供路由映射为 HTTP 状态码"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ==================== 基础工具 ====================

def to_object_id(value: Any, what: str = "ID") -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        raise TemplateError(f"无效的{what}: {value}", 400)


def _iso(dt: Any) -> Optional[str]:
    if isinstance(dt, datetime):
        return dt.isoformat() + "Z"
    return dt


def normalize_content(raw: Any) -> Dict[str, str]:
    """校验并规整模板内容：只保留已知字段、统一为 str、去首尾空白。"""
    if not isinstance(raw, dict):
        raise TemplateError("content 必须是对象", 400)
    unknown = [k for k in raw if k not in CONTENT_FIELDS]
    if unknown:
        raise TemplateError(f"不支持的 content 字段: {', '.join(sorted(unknown))}", 400)
    out: Dict[str, str] = {}
    for field in CONTENT_FIELDS:
        value = raw.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise TemplateError(f"content.{field} 必须是字符串", 400)
        value = value.strip()
        if len(value) > MAX_FIELD_LEN:
            raise TemplateError(f"content.{field} 超过 {MAX_FIELD_LEN} 字符上限", 400)
        out[field] = value
    if not out["system_prompt"] and not out["user_prompt"]:
        raise TemplateError("system_prompt 与 user_prompt 至少填写一个", 400)
    return out


def normalize_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise TemplateError("模板名称不能为空", 400)
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        raise TemplateError(f"模板名称不能超过 {MAX_NAME_LEN} 字符", 400)
    return name


def normalize_remark(remark: Any) -> str:
    if remark is None:
        return ""
    if not isinstance(remark, str):
        raise TemplateError("备注必须是字符串", 400)
    remark = remark.strip()
    if len(remark) > MAX_REMARK_LEN:
        raise TemplateError(f"备注不能超过 {MAX_REMARK_LEN} 字符", 400)
    return remark


def extract_variables(content: Dict[str, Any]) -> List[str]:
    """提取模板用到的变量名（{name} 与 {{a.b}}），去重保序。"""
    seen: Dict[str, None] = {}
    for field in CONTENT_FIELDS:
        text = content.get(field) or ""
        if not isinstance(text, str):
            continue
        for m in _DOUBLE_BRACE.finditer(text):
            seen.setdefault(m.group(1).strip(), None)
        # 先剔除双层占位符，避免 {{x}} 内层的 {x} 被重复识别
        for m in _SINGLE_BRACE.finditer(_DOUBLE_BRACE.sub("", text)):
            seen.setdefault(m.group(1), None)
    return list(seen)


def render_preview(content: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """按运行时同样的规则渲染，返回渲染结果与仍未替换的变量。

    规则：双层 {{a.b}} 支持点号路径，单层 {name} 仅简单变量名；变量缺失渲染为空串。
    """
    variables = dict(variables or {})
    for name in BUILTIN_VARIABLES:
        variables.setdefault(name, "")

    def nested(path: str) -> Any:
        value: Any = variables
        for key in path.split("."):
            if isinstance(value, dict):
                value = value.get(key, "")
            else:
                return ""
        return value

    rendered: Dict[str, str] = {}
    missing: Dict[str, None] = {}
    for field in CONTENT_FIELDS:
        text = content.get(field) or ""
        if not isinstance(text, str):
            rendered[field] = ""
            continue

        def sub_double(m):
            path = m.group(1).strip()
            val = nested(path)
            if val in ("", None):
                missing.setdefault(path, None)
            return "" if val is None else str(val)

        def sub_single(m):
            name = m.group(1).strip()
            val = variables.get(name)
            if val in ("", None):
                missing.setdefault(name, None)
            return "" if val is None else str(val)

        text = _DOUBLE_BRACE.sub(sub_double, text)
        text = _SINGLE_BRACE.sub(sub_single, text)
        rendered[field] = text
    # 内置分层变量本就允许为空，不提示
    return {
        "rendered": rendered,
        "empty_variables": [v for v in missing if v not in BUILTIN_VARIABLES],
    }


def serialize(doc: Dict[str, Any], *, active_template_ids: Optional[set] = None,
              include_content: bool = True) -> Dict[str, Any]:
    content = doc.get("content") or {}
    out: Dict[str, Any] = {
        "id": str(doc["_id"]),
        "agent_type": doc.get("agent_type", ""),
        "agent_name": doc.get("agent_name", ""),
        "template_name": doc.get("template_name", ""),
        "is_system": bool(doc.get("is_system")),
        "status": doc.get("status", "draft"),
        "version": doc.get("version", 1),
        "preference_type": doc.get("preference_type"),
        "workflow_id": doc.get("workflow_id"),
        "node_id": doc.get("node_id") or None,
        "base_template_id": str(doc["base_template_id"]) if doc.get("base_template_id") else None,
        "base_version": doc.get("base_version"),
        "remark": doc.get("remark", ""),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "variables": extract_variables(content),
        "is_active_for_me": bool(active_template_ids and doc["_id"] in active_template_ids),
    }
    if include_content:
        out["content"] = {f: content.get(f, "") for f in CONTENT_FIELDS}
    else:
        out["system_prompt_preview"] = (content.get("system_prompt") or "")[:120]
    return out


# ==================== 查询 ====================

def _visibility_filter(user_oid: ObjectId) -> Dict[str, Any]:
    """系统模板 + 当前用户自己的模板；排除流程/节点专属模板（它们属于分析流编辑器）。"""
    return {
        "$and": [
            {"$or": [{"is_system": True}, {"created_by": user_oid}]},
            {"$or": [{"workflow_id": None}, {"workflow_id": {"$exists": False}}]},
        ]
    }


async def _active_ids(db: Any, user_oid: ObjectId) -> set:
    ids = set()
    async for cfg in db[CONFIG_COLLECTION].find({"user_id": user_oid, "is_active": True}, {"template_id": 1}):
        tid = cfg.get("template_id")
        if tid is not None:
            ids.add(tid if isinstance(tid, ObjectId) else ObjectId(str(tid)))
    return ids


async def list_templates(db: Any, user_id: Any, *, scope: str = "all", agent_type: Optional[str] = None,
                         agent_name: Optional[str] = None, status: Optional[str] = None,
                         keyword: Optional[str] = None, skip: int = 0, limit: int = 50) -> Dict[str, Any]:
    user_oid = to_object_id(user_id, "用户ID")
    clauses: List[Dict[str, Any]] = [_visibility_filter(user_oid)]
    if scope == "system":
        clauses.append({"is_system": True})
    elif scope == "mine":
        clauses.append({"is_system": {"$ne": True}, "created_by": user_oid})
    elif scope != "all":
        raise TemplateError("scope 必须是 all / system / mine", 400)
    if agent_type:
        clauses.append({"agent_type": agent_type})
    if agent_name:
        clauses.append({"agent_name": agent_name})
    if status:
        if status not in STATUSES:
            raise TemplateError("status 必须是 draft / active", 400)
        clauses.append({"status": status})
    if keyword and keyword.strip():
        pattern = {"$regex": re.escape(keyword.strip()), "$options": "i"}
        clauses.append({"$or": [{"template_name": pattern}, {"agent_name": pattern}, {"remark": pattern}]})
    query = {"$and": clauses}

    coll = db[TEMPLATE_COLLECTION]
    total = await coll.count_documents(query)
    cursor = coll.find(query).sort([("is_system", -1), ("agent_type", 1), ("agent_name", 1), ("updated_at", -1)])
    cursor = cursor.skip(max(skip, 0)).limit(max(1, min(limit, 200)))
    active = await _active_ids(db, user_oid)
    items = [serialize(d, active_template_ids=active, include_content=False) async for d in cursor]
    return {"items": items, "total": total}


async def list_agents(db: Any, user_id: Any) -> List[Dict[str, Any]]:
    """每个 Agent 一行：系统模板数、我的模板数、当前生效来源。供“按 Agent 管理”视图。"""
    user_oid = to_object_id(user_id, "用户ID")
    coll = db[TEMPLATE_COLLECTION]
    agents: Dict[tuple, Dict[str, Any]] = {}
    cursor = coll.find(_visibility_filter(user_oid), {"agent_type": 1, "agent_name": 1, "is_system": 1, "status": 1})
    async for d in cursor:
        key = (d.get("agent_type", ""), d.get("agent_name", ""))
        row = agents.setdefault(key, {
            "agent_type": key[0], "agent_name": key[1],
            "system_count": 0, "my_count": 0, "active_template_id": None, "using": "system",
        })
        if d.get("is_system"):
            row["system_count"] += 1
        else:
            row["my_count"] += 1
    async for cfg in db[CONFIG_COLLECTION].find({"user_id": user_oid, "is_active": True}):
        key = (cfg.get("agent_type", ""), cfg.get("agent_name", ""))
        if key in agents and cfg.get("template_id") is not None:
            tpl = await coll.find_one({"_id": cfg["template_id"], "status": "active"}, {"_id": 1})
            if tpl:
                agents[key]["active_template_id"] = str(cfg["template_id"])
                agents[key]["using"] = "custom"
    return sorted(agents.values(), key=lambda r: (r["agent_type"], r["agent_name"]))


async def get_template(db: Any, user_id: Any, template_id: Any) -> Dict[str, Any]:
    user_oid = to_object_id(user_id, "用户ID")
    doc = await _load_visible(db, user_oid, template_id)
    active = await _active_ids(db, user_oid)
    return serialize(doc, active_template_ids=active)


async def _load_visible(db: Any, user_oid: ObjectId, template_id: Any) -> Dict[str, Any]:
    doc = await db[TEMPLATE_COLLECTION].find_one({"_id": to_object_id(template_id, "模板ID")})
    if not doc or not (doc.get("is_system") or doc.get("created_by") == user_oid):
        raise TemplateError("模板不存在", 404)
    return doc


async def _load_owned(db: Any, user_oid: ObjectId, template_id: Any) -> Dict[str, Any]:
    doc = await _load_visible(db, user_oid, template_id)
    if doc.get("is_system"):
        raise TemplateError("系统模板为只读，请先“复制为我的模板”再修改", 403)
    return doc


# ==================== 写入 ====================

async def create_template(db: Any, user_id: Any, *, agent_type: str, agent_name: str, template_name: str,
                          content: Dict[str, Any], remark: str = "",
                          base_template_id: Optional[str] = None) -> Dict[str, Any]:
    user_oid = to_object_id(user_id, "用户ID")
    if not agent_type or not agent_name:
        raise TemplateError("agent_type 与 agent_name 必填", 400)
    name = normalize_name(template_name)
    body = normalize_content(content)
    remark = normalize_remark(remark)

    base_oid = base_version = None
    if base_template_id:
        base = await _load_visible(db, user_oid, base_template_id)
        if (base.get("agent_type"), base.get("agent_name")) != (agent_type, agent_name):
            raise TemplateError("基础模板与目标 Agent 不一致", 400)
        base_oid, base_version = base["_id"], base.get("version", 1)

    await _ensure_unique_name(db, user_oid, agent_type, agent_name, name)
    now = datetime.utcnow()
    doc = {
        "agent_type": agent_type, "agent_name": agent_name, "template_name": name,
        "content": body, "remark": remark,
        "status": "draft", "is_system": False, "created_by": user_oid,
        "workflow_id": None, "preference_type": None,
        "base_template_id": base_oid, "base_version": base_version,
        "version": 1, "created_at": now, "updated_at": now,
    }
    res = await db[TEMPLATE_COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    return serialize(doc)


async def _ensure_unique_name(db: Any, user_oid: ObjectId, agent_type: str, agent_name: str,
                              name: str, exclude_id: Optional[ObjectId] = None) -> None:
    query: Dict[str, Any] = {
        "created_by": user_oid, "is_system": {"$ne": True},
        "agent_type": agent_type, "agent_name": agent_name, "template_name": name,
    }
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    if await db[TEMPLATE_COLLECTION].find_one(query, {"_id": 1}):
        raise TemplateError(f"已存在同名模板：{name}", 409)


async def update_template(db: Any, user_id: Any, template_id: Any, *, template_name: Optional[str] = None,
                          content: Optional[Dict[str, Any]] = None, remark: Optional[str] = None) -> Dict[str, Any]:
    user_oid = to_object_id(user_id, "用户ID")
    doc = await _load_owned(db, user_oid, template_id)
    update: Dict[str, Any] = {}
    if template_name is not None:
        name = normalize_name(template_name)
        if name != doc.get("template_name"):
            await _ensure_unique_name(db, user_oid, doc["agent_type"], doc["agent_name"], name, doc["_id"])
        update["template_name"] = name
    if content is not None:
        update["content"] = normalize_content(content)
    if remark is not None:
        update["remark"] = normalize_remark(remark)
    if not update:
        raise TemplateError("没有需要更新的字段", 400)

    changed = any(doc.get(k) != v for k, v in update.items())
    if not changed:
        return serialize(doc)
    update["updated_at"] = datetime.utcnow()
    # 乐观并发：以读到的 version 为条件，避免两个窗口互相覆盖
    res = await db[TEMPLATE_COLLECTION].update_one(
        {"_id": doc["_id"], "version": doc.get("version", 1)},
        {"$set": update, "$inc": {"version": 1}},
    )
    if res.matched_count == 0:
        raise TemplateError("模板已被其他会话修改，请刷新后重试", 409)
    return await get_template(db, user_oid, doc["_id"])


async def set_status(db: Any, user_id: Any, template_id: Any, status: str) -> Dict[str, Any]:
    """发布（active）/ 撤回（draft）。撤回时同步取消对它的生效配置。"""
    if status not in STATUSES:
        raise TemplateError("status 必须是 draft / active", 400)
    user_oid = to_object_id(user_id, "用户ID")
    doc = await _load_owned(db, user_oid, template_id)
    if status == "active":
        normalize_content(doc.get("content") or {})  # 发布前再次校验内容
    if doc.get("status") != status:
        await db[TEMPLATE_COLLECTION].update_one(
            {"_id": doc["_id"]}, {"$set": {"status": status, "updated_at": datetime.utcnow()}})
    if status == "draft":
        await _deactivate_configs_for(db, doc["_id"])
    return await get_template(db, user_oid, doc["_id"])


async def delete_template(db: Any, user_id: Any, template_id: Any) -> None:
    user_oid = to_object_id(user_id, "用户ID")
    doc = await _load_owned(db, user_oid, template_id)
    await _deactivate_configs_for(db, doc["_id"])
    await db[TEMPLATE_COLLECTION].delete_one({"_id": doc["_id"]})


async def _deactivate_configs_for(db: Any, template_oid: ObjectId) -> int:
    res = await db[CONFIG_COLLECTION].delete_many({"template_id": template_oid})
    return res.deleted_count


async def duplicate_template(db: Any, user_id: Any, template_id: Any,
                             template_name: Optional[str] = None) -> Dict[str, Any]:
    """把系统模板或自己的模板复制为一份新的草稿模板。"""
    user_oid = to_object_id(user_id, "用户ID")
    src = await _load_visible(db, user_oid, template_id)
    base_name = (template_name or "").strip() or f"{src.get('template_name', '模板')}（副本）"
    name = base_name
    n = 2
    while await db[TEMPLATE_COLLECTION].find_one(
        {"created_by": user_oid, "is_system": {"$ne": True}, "agent_type": src["agent_type"],
         "agent_name": src["agent_name"], "template_name": name}, {"_id": 1}
    ):
        name = f"{base_name} {n}"
        n += 1
    return await create_template(
        db, user_oid, agent_type=src["agent_type"], agent_name=src["agent_name"],
        template_name=name, content=src.get("content") or {}, remark=src.get("remark", "") if not src.get("is_system") else "",
        base_template_id=str(src["_id"]),
    )


# ==================== 生效配置 ====================

async def activate_template(db: Any, user_id: Any, template_id: Any) -> Dict[str, Any]:
    """让自己的已发布模板对该 Agent 生效。"""
    user_oid = to_object_id(user_id, "用户ID")
    doc = await _load_owned(db, user_oid, template_id)
    if doc.get("status") != "active":
        raise TemplateError("仅已发布的模板可以设为生效，请先发布", 400)
    now = datetime.utcnow()
    await db[CONFIG_COLLECTION].update_one(
        {"user_id": user_oid, "agent_type": doc["agent_type"], "agent_name": doc["agent_name"]},
        {"$set": {"template_id": doc["_id"], "is_active": True, "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return await get_template(db, user_oid, doc["_id"])


async def deactivate_agent(db: Any, user_id: Any, agent_type: str, agent_name: str) -> int:
    """取消该 Agent 的自定义模板，回到系统默认。"""
    user_oid = to_object_id(user_id, "用户ID")
    res = await db[CONFIG_COLLECTION].delete_many(
        {"user_id": user_oid, "agent_type": agent_type, "agent_name": agent_name})
    return res.deleted_count
