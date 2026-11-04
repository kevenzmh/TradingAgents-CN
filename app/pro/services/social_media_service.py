"""社媒消息导入服务（Pro）。

把用户通过 API 批量导入的社交媒体消息规范化后写入 `social_media_messages`，
供情绪工具 `core/tools/implementations/social/stock_sentiment.py` 与
`tradingagents/dataflows/cache/mongodb_cache_adapter.py` 读取。

**数据契约（由两个消费方的读取代码决定，不可随意改动）**

- `symbol`        规范化股票代码（A 股 6 位）
- `publish_time`  BSON datetime，UTC 无时区。消费方用 datetime 比较做时间窗查询，
                  写成 ISO 字符串会导致查询不到
- `sentiment`     只能是 positive / negative / neutral。消费方有
                  `sentiment_counts[sentiment] += 1`，其他取值会 KeyError
- `keywords` / `hashtags`  字符串列表
- `engagement`    {views, likes, shares, comments, engagement_rate}
- `author`        {author_id, author_name, followers_count, influence_score, verified}

**情绪 / 关键词 / 重要性均为内置规则方法，不是模型：**
- 情绪：金融情绪词典命中计数 + 否定词翻转，得分 [-1, 1]
- 关键词：话题标签 + 金融词典命中（项目不依赖 jieba，不引入新依赖）
- 重要性：作者影响力 + 互动量 + 浏览量加权，得分 [0, 100]
调用方若自带 sentiment / keywords / importance，则以调用方为准。

时间规则：不带时区的 publish_time 一律按北京时间（UTC+8）解释，入库统一转 UTC。
"""

import hashlib
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION = "social_media_messages"
MAX_BATCH = 1000
MAX_CONTENT_LEN = 10000
BULK_CHUNK = 500
BEIJING_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# 规则词典
# ---------------------------------------------------------------------------

POSITIVE_WORDS = (
    "利好", "看涨", "看多", "看好", "上涨", "大涨", "涨停", "拉升", "突破", "创新高",
    "增长", "超预期", "增持", "回购", "加仓", "买入", "抄底", "反弹", "强势", "龙头",
    "放量上攻", "业绩亮眼", "盈利", "扭亏", "分红", "中标", "订单", "景气", "复苏",
    "牛市", "起飞", "翻倍", "稳健", "低估", "潜力", "受益", "获批", "重大突破",
)
NEGATIVE_WORDS = (
    "利空", "看跌", "看空", "下跌", "大跌", "跌停", "暴跌", "杀跌", "破位", "新低",
    "下滑", "不及预期", "减持", "亏损", "爆雷", "暴雷", "退市", "割肉", "清仓", "跑路",
    "强势下行", "业绩变脸", "违规", "处罚", "立案", "调查", "诉讼", "商誉减值", "质押",
    "熊市", "崩盘", "腰斩", "高估", "泡沫", "踩雷", "风险", "警示", "被查", "ST",
)
NEGATIONS = ("不", "没", "未", "无", "别", "非", "难以", "并非", "不会", "没有")
# 含否定字但并非否定的常见搭配（"未来上涨""不断上涨""无论涨跌"），紧邻命中词时不翻转
NON_NEGATING = ("未来", "不断", "不仅", "不但", "无论", "不愧", "没准", "无疑", "不止", "不限")

# 话题词（用于关键词提取，不参与情绪计分）
TOPIC_TERMS = (
    "业绩", "财报", "年报", "季报", "中报", "分红", "回购", "并购", "重组", "定增",
    "政策", "监管", "减持", "增持", "涨停", "跌停", "龙头", "主力", "资金", "北向",
    "板块", "估值", "机构", "研报", "评级", "目标价", "产能", "订单", "新品", "出海",
    "芯片", "新能源", "AI", "人工智能", "光伏", "白酒", "医药", "银行", "地产", "军工",
)

_HASHTAG_PAIR = re.compile(r"#([^#\s]{1,30})#")
_HASHTAG_LATIN = re.compile(r"(?<![\w#])#([A-Za-z0-9_一-鿿]{1,30})(?=\s|$|[,，。.!！?？])")
_CJK = re.compile(r"[一-鿿]")

_SENTIMENT_ALIASES = {
    "positive": "positive", "pos": "positive", "bullish": "positive", "bull": "positive",
    "积极": "positive", "正面": "positive", "看涨": "positive", "利好": "positive", "乐观": "positive",
    "negative": "negative", "neg": "negative", "bearish": "negative", "bear": "negative",
    "消极": "negative", "负面": "negative", "看跌": "negative", "利空": "negative", "悲观": "negative",
    "neutral": "neutral", "中性": "neutral", "中立": "neutral", "平稳": "neutral",
}

_SUFFIXES = (".SH", ".SZ", ".SS", ".HK", ".XSHE", ".XSHG", ".BJ")


# ---------------------------------------------------------------------------
# 纯函数：字段规范化与规则分析（便于单测）
# ---------------------------------------------------------------------------

def normalize_symbol(raw: Any, market: str = "CN") -> str:
    """规范化股票代码：去交易所后缀；A 股数字补齐 6 位；其他市场保持原样（大写）。"""
    text = str(raw or "").strip().upper()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = text.strip()
    if not text:
        return ""
    if text.isdigit() and str(market or "CN").upper() == "CN" and len(text) <= 6:
        return text.zfill(6)
    return text


def parse_publish_time(value: Any) -> datetime:
    """解析发布时间，返回 UTC 无时区 datetime。无法解析抛 ValueError。

    无时区信息的输入按北京时间解释；带时区的转换为 UTC。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("publish_time 不能为空")

    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e11:  # 毫秒时间戳
            ts /= 1000.0
        if ts <= 0:
            raise ValueError(f"publish_time 时间戳非法: {value}")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d{10}|\d{13}", text):
            return parse_publish_time(int(text))
        iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
                        "%Y%m%d%H%M%S", "%Y%m%d %H:%M:%S", "%Y/%m/%d", "%Y%m%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            raise ValueError(f"publish_time 无法解析: {value!r}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if utc > datetime.utcnow() + timedelta(days=1):
        raise ValueError(f"publish_time 在未来: {value!r}")
    return utc


def _safe_int(value: Any) -> int:
    try:
        n = int(float(value))
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _scan_lexicon(text: str, words: Iterable[str]) -> List[Tuple[int, str]]:
    """在文本中扫描词典命中，长词优先、不重叠。返回 [(起始位置, 词)]。"""
    hits: List[Tuple[int, str]] = []
    covered = [False] * len(text)
    for word in sorted(set(words), key=len, reverse=True):
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            end = idx + len(word)
            if not any(covered[idx:end]):
                for i in range(idx, end):
                    covered[i] = True
                hits.append((idx, word))
            start = idx + 1
    return sorted(hits)


def _negated(text: str, position: int) -> bool:
    """命中词前 2 个字符内出现否定词则视为被否定。"""
    window = text[max(0, position - 2):position]
    if window in NON_NEGATING:
        return False
    return any(neg in window for neg in NEGATIONS)


def analyze_sentiment(content: str) -> Tuple[str, float]:
    """规则情绪分析。返回 (label, score)，score ∈ [-1, 1]。"""
    text = str(content or "")
    pos = neg = 0
    for position, _word in _scan_lexicon(text, POSITIVE_WORDS):
        if _negated(text, position):
            neg += 1
        else:
            pos += 1
    for position, _word in _scan_lexicon(text, NEGATIVE_WORDS):
        if _negated(text, position):
            pos += 1
        else:
            neg += 1
    total = pos + neg
    if total == 0:
        return "neutral", 0.0
    score = round((pos - neg) / total, 4)
    if score >= 0.2:
        return "positive", score
    if score <= -0.2:
        return "negative", score
    return "neutral", score


def normalize_sentiment(value: Any) -> Optional[str]:
    """把调用方给出的情绪值归一为三个标准值；无法识别返回 None（改为自动分析）。"""
    if value is None:
        return None
    return _SENTIMENT_ALIASES.get(str(value).strip().lower())


def extract_hashtags(content: str) -> List[str]:
    tags: List[str] = []
    for pattern in (_HASHTAG_PAIR, _HASHTAG_LATIN):
        for match in pattern.findall(content or ""):
            tag = match.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags[:10]


def extract_keywords(content: str, hashtags: Optional[List[str]] = None, limit: int = 8) -> List[str]:
    """关键词：话题标签优先，其后是金融词典命中（按出现次数、位置排序）。"""
    text = str(content or "")
    keywords: List[str] = []
    for tag in hashtags or []:
        if tag not in keywords:
            keywords.append(tag)
    counted: Dict[str, Tuple[int, int]] = {}
    for position, word in _scan_lexicon(text, POSITIVE_WORDS + NEGATIVE_WORDS + TOPIC_TERMS):
        times, first = counted.get(word, (0, position))
        counted[word] = (times + 1, first)
    for word, _ in sorted(counted.items(), key=lambda kv: (-kv[1][0], kv[1][1])):
        if word not in keywords:
            keywords.append(word)
    return keywords[:limit]


def compute_importance(author: Dict[str, Any], engagement: Dict[str, Any]) -> Tuple[str, float]:
    """重要性：作者影响力 50% + 互动量 30% + 浏览量 20%，得分 [0, 100]。"""
    followers = _safe_int(author.get("followers_count"))
    influence = _safe_float(author.get("influence_score"))
    if influence is None:
        influence = min(100.0, math.log10(followers + 1) / 6.0 * 100.0)
    if author.get("verified"):
        influence = min(100.0, influence + 10.0)
    interactions = engagement["likes"] + engagement["shares"] + engagement["comments"]
    interaction_part = min(100.0, math.log10(interactions + 1) / 4.0 * 100.0)
    view_part = min(100.0, math.log10(engagement["views"] + 1) / 5.0 * 100.0)
    score = round(max(0.0, min(100.0, 0.5 * influence + 0.3 * interaction_part + 0.2 * view_part)), 2)
    if score >= 60:
        return "high", score
    if score >= 30:
        return "medium", score
    return "low", score


def message_key(platform: str, symbol: str, publish_time: datetime, content: str,
                provided_id: Any = None) -> str:
    """幂等键：优先用调用方的 message_id，否则由平台/代码/时间/正文前 200 字哈希生成。"""
    pid = str(provided_id).strip() if provided_id not in (None, "") else ""
    if pid:
        return pid[:128]
    raw = f"{platform}|{symbol}|{publish_time.isoformat()}|{content[:200]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def normalize_message(raw: Dict[str, Any], user_id: str = "") -> Dict[str, Any]:
    """把一条原始消息规范化为入库文档。非法输入抛 ValueError（带可读原因）。"""
    if not isinstance(raw, dict):
        raise ValueError("消息必须是对象")

    platform = str(raw.get("platform") or "").strip().lower()
    if not platform:
        raise ValueError("缺少 platform")
    if len(platform) > 32:
        raise ValueError("platform 过长（≤32）")

    market = str(raw.get("market") or "CN").strip().upper() or "CN"
    symbol = normalize_symbol(raw.get("symbol"), market)
    if not symbol:
        raise ValueError("缺少 symbol")

    content = str(raw.get("content") or "").strip()
    if not content:
        raise ValueError("缺少 content")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError(f"content 过长（>{MAX_CONTENT_LEN} 字符）")

    publish_time = parse_publish_time(raw.get("publish_time"))

    raw_author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    author = {
        "author_id": str(raw_author.get("author_id") or "")[:64],
        "author_name": str(raw_author.get("author_name") or raw_author.get("name") or "")[:64],
        "followers_count": _safe_int(raw_author.get("followers_count")),
        "verified": bool(raw_author.get("verified", False)),
    }
    provided_influence = _safe_float(raw_author.get("influence_score"))
    if provided_influence is not None:
        author["influence_score"] = max(0.0, min(100.0, provided_influence))

    raw_eng = raw.get("engagement") if isinstance(raw.get("engagement"), dict) else {}
    engagement = {
        "views": _safe_int(raw_eng.get("views")),
        "likes": _safe_int(raw_eng.get("likes")),
        "shares": _safe_int(raw_eng.get("shares")),
        "comments": _safe_int(raw_eng.get("comments")),
    }
    interactions = engagement["likes"] + engagement["shares"] + engagement["comments"]
    provided_rate = _safe_float(raw_eng.get("engagement_rate"))
    if provided_rate is not None:
        engagement["engagement_rate"] = round(max(0.0, provided_rate), 2)
    elif engagement["views"] > 0:
        engagement["engagement_rate"] = round(min(100.0, interactions / engagement["views"] * 100.0), 2)
    else:
        engagement["engagement_rate"] = 0.0

    hashtags = raw.get("hashtags")
    if isinstance(hashtags, list):
        hashtags = [str(t).strip().lstrip("#").rstrip("#") for t in hashtags if str(t).strip()][:10]
    else:
        hashtags = extract_hashtags(content)

    keywords = raw.get("keywords")
    if isinstance(keywords, list) and keywords:
        keywords = list(dict.fromkeys(str(k).strip() for k in keywords if str(k).strip()))[:10]
    else:
        keywords = extract_keywords(content, hashtags)

    sentiment = normalize_sentiment(raw.get("sentiment"))
    sentiment_source = "provided"
    sentiment_score = _safe_float(raw.get("sentiment_score"))
    if sentiment is None:
        sentiment, auto_score = analyze_sentiment(content)
        sentiment_score = auto_score
        sentiment_source = "auto"
    elif sentiment_score is None:
        sentiment_score = {"positive": 0.6, "negative": -0.6, "neutral": 0.0}[sentiment]
    sentiment_score = max(-1.0, min(1.0, float(sentiment_score)))

    importance, importance_score = compute_importance(author, engagement)
    provided_importance = str(raw.get("importance") or "").strip().lower()
    if provided_importance in ("high", "medium", "low"):
        importance = provided_importance

    now = datetime.utcnow()
    return {
        "message_id": message_key(platform, symbol, publish_time, content, raw.get("message_id")),
        "platform": platform,
        "symbol": symbol,
        "market": market,
        "content": content,
        "title": str(raw.get("title") or "")[:200],
        "url": str(raw.get("url") or "")[:500],
        "language": str(raw.get("language") or ("zh" if _CJK.search(content) else "en"))[:8],
        "publish_time": publish_time,
        "author": author,
        "engagement": engagement,
        "hashtags": hashtags,
        "keywords": keywords,
        "sentiment": sentiment,
        "sentiment_score": round(sentiment_score, 4),
        "sentiment_source": sentiment_source,
        "importance": importance,
        "importance_score": importance_score,
        "data_source": "user_import",
        "imported_by": str(user_id or ""),
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# 落库与查询（motor 异步）
# ---------------------------------------------------------------------------

_indexes_ready = False


async def ensure_indexes(db: Any) -> None:
    """幂等建立索引（进程内只尝试一次；失败只记日志不阻断导入）。"""
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        coll = db[COLLECTION]
        await coll.create_index([("platform", 1), ("message_id", 1)], unique=True,
                                name="platform_message_unique", background=True)
        await coll.create_index([("symbol", 1), ("publish_time", -1)],
                                name="symbol_time", background=True)
        await coll.create_index([("publish_time", -1)], name="publish_time_desc", background=True)
        _indexes_ready = True
    except Exception as exc:  # pragma: no cover - 索引失败不应阻断主流程
        logger.warning("[社媒导入] 索引创建失败（继续）: %s", exc)


def prepare_batch(messages: List[Dict[str, Any]], user_id: str = ""
                  ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """规范化整批消息。返回 (有效文档, 错误列表, 批内重复数)。"""
    docs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    duplicates = 0
    for index, raw in enumerate(messages):
        try:
            doc = normalize_message(raw, user_id)
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
            continue
        key = (doc["platform"], doc["message_id"])
        if key in docs:
            duplicates += 1  # 批内重复：保留首条
            continue
        docs[key] = doc
    return list(docs.values()), errors, duplicates


async def save_messages(db: Any, messages: List[Dict[str, Any]], overwrite: bool = False,
                        user_id: str = "") -> Dict[str, Any]:
    """批量保存。返回 {total, saved, updated, skipped, failed, errors}。"""
    from pymongo import UpdateOne
    from pymongo.errors import BulkWriteError

    docs, errors, duplicates = prepare_batch(messages, user_id)
    result = {
        "total": len(messages), "saved": 0, "updated": 0,
        "skipped": duplicates, "failed": len(errors), "errors": errors[:50],
    }
    if not docs:
        return result

    await ensure_indexes(db)
    now = datetime.utcnow()
    operations = []
    for doc in docs:
        flt = {"platform": doc["platform"], "message_id": doc["message_id"]}
        if overwrite:
            operations.append(UpdateOne(flt, {"$set": doc, "$setOnInsert": {"created_at": now}}, upsert=True))
        else:
            operations.append(UpdateOne(flt, {"$setOnInsert": {**doc, "created_at": now}}, upsert=True))

    for start in range(0, len(operations), BULK_CHUNK):
        chunk = operations[start:start + BULK_CHUNK]
        try:
            res = await db[COLLECTION].bulk_write(chunk, ordered=False)
            saved, updated = res.upserted_count, res.modified_count
        except BulkWriteError as exc:
            details = exc.details or {}
            saved = len(details.get("upserted", []))
            updated = int(details.get("nModified", 0))
            write_errors = details.get("writeErrors", [])
            result["failed"] += len(write_errors)
            for we in write_errors[:10]:
                result["errors"].append({"index": None, "error": str(we.get("errmsg", "写入失败"))[:200]})
        result["saved"] += saved
        result["updated"] += updated
        result["skipped"] += max(0, len(chunk) - saved - updated)
    result["errors"] = result["errors"][:50]
    return result


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    for key in ("publish_time", "created_at", "updated_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat() + "Z"
    return out


def _build_query(symbol: Optional[str], platform: Optional[str], sentiment: Optional[str],
                 hours_back: Optional[int], market: str = "CN") -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if symbol:
        query["symbol"] = normalize_symbol(symbol, market)
    if platform:
        query["platform"] = platform.strip().lower()
    if sentiment:
        normalized = normalize_sentiment(sentiment)
        if normalized:
            query["sentiment"] = normalized
    if hours_back:
        query["publish_time"] = {"$gte": datetime.utcnow() - timedelta(hours=int(hours_back))}
    return query


async def list_messages(db: Any, *, symbol: Optional[str] = None, platform: Optional[str] = None,
                        sentiment: Optional[str] = None, hours_back: Optional[int] = None,
                        skip: int = 0, limit: int = 20) -> Dict[str, Any]:
    query = _build_query(symbol, platform, sentiment, hours_back)
    coll = db[COLLECTION]
    total = await coll.count_documents(query)
    cursor = coll.find(query).sort("publish_time", -1).skip(max(0, skip)).limit(max(1, min(limit, 200)))
    items = [_serialize(doc) async for doc in cursor]
    return {"items": items, "total": total}


async def message_stats(db: Any, *, symbol: Optional[str] = None,
                        hours_back: Optional[int] = None) -> Dict[str, Any]:
    query = _build_query(symbol, None, None, hours_back)
    coll = db[COLLECTION]
    out: Dict[str, Any] = {"total": await coll.count_documents(query),
                           "by_sentiment": {"positive": 0, "negative": 0, "neutral": 0},
                           "by_platform": {}, "by_importance": {}, "top_keywords": [],
                           "latest_publish_time": None}
    for row in await coll.aggregate([{"$match": query},
                                     {"$group": {"_id": "$sentiment", "n": {"$sum": 1}}}]).to_list(10):
        if row["_id"] in out["by_sentiment"]:
            out["by_sentiment"][row["_id"]] = row["n"]
    for row in await coll.aggregate([{"$match": query},
                                     {"$group": {"_id": "$platform", "n": {"$sum": 1}}},
                                     {"$sort": {"n": -1}}]).to_list(50):
        out["by_platform"][str(row["_id"])] = row["n"]
    for row in await coll.aggregate([{"$match": query},
                                     {"$group": {"_id": "$importance", "n": {"$sum": 1}}}]).to_list(10):
        out["by_importance"][str(row["_id"])] = row["n"]
    out["top_keywords"] = [
        {"keyword": r["_id"], "count": r["n"]}
        for r in await coll.aggregate([{"$match": query}, {"$unwind": "$keywords"},
                                       {"$group": {"_id": "$keywords", "n": {"$sum": 1}}},
                                       {"$sort": {"n": -1}}, {"$limit": 10}]).to_list(10)
    ]
    latest = await coll.find(query, {"publish_time": 1}).sort("publish_time", -1).limit(1).to_list(1)
    if latest and isinstance(latest[0].get("publish_time"), datetime):
        out["latest_publish_time"] = latest[0]["publish_time"].isoformat() + "Z"
    return out


async def delete_message(db: Any, message_id: str, platform: Optional[str] = None) -> int:
    query: Dict[str, Any] = {"message_id": str(message_id)}
    if platform:
        query["platform"] = platform.strip().lower()
    res = await db[COLLECTION].delete_many(query)
    return int(res.deleted_count)
