# 社媒消息批量导入 API 指南

把雪球、股吧、微博等平台的帖子/评论批量导入系统，供**情绪分析工具**与**社媒分析师**使用。
导入时系统会自动补全情绪、关键词与重要性（见 §4，**规则方法，不是大模型**）。

## 1. 认证

同《股票数据批量导入 API 指南》：先 `POST /api/auth/login` 取 `access_token`，请求头带 `Authorization: Bearer <token>`。

## 2. 接口

| 用途 | 方法与路径 |
|---|---|
| 批量导入 | `POST /api/data-import/social-media` |
| 查询列表 | `GET /api/data-import/social-media` |
| 统计 | `GET /api/data-import/social-media/stats` |
| 删除单条 | `DELETE /api/data-import/social-media/{message_id}?platform=...` |

数据写入集合 `social_media_messages`。

## 3. 批量导入

`POST /api/data-import/social-media`

```json
{
  "messages": [
    {
      "platform": "xueqiu",
      "symbol": "600519",
      "content": "贵州茅台三季报业绩超预期，看好后市 #茅台#",
      "publish_time": "2026-01-05 10:30:00",
      "author": {"author_name": "价值投资者", "followers_count": 52000, "verified": true},
      "engagement": {"views": 12000, "likes": 380, "shares": 45, "comments": 120}
    }
  ],
  "overwrite": false
}
```

- 单次最多 **1000** 条；`overwrite=false`（默认）时已存在的消息被跳过，`true` 时覆盖。
- 同一批里的重复消息保留第一条。

### 3.1 消息字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `platform` | 是 | 平台标识，如 `xueqiu` / `guba` / `weibo` / `twitter`，统一转小写，≤32 字符 |
| `symbol` | 是 | 股票代码。A 股可写 `600519` / `600519.SH` / `1`，自动规范为 6 位；`market` 为 `HK`/`US` 时保持原样 |
| `content` | 是 | 正文，≤10000 字符 |
| `publish_time` | 是 | 发布时间，格式见 §3.2 |
| `market` | 否 | `CN`（默认）/ `HK` / `US` |
| `message_id` | 否 | 平台原始消息 ID。不提供时由「平台 + 代码 + 时间 + 正文前 200 字」哈希生成 |
| `title` `url` `language` | 否 | 标题 / 链接 / 语言（缺省按正文自动判断 zh / en） |
| `author` | 否 | `{author_id, author_name, followers_count, influence_score(0-100), verified}` |
| `engagement` | 否 | `{views, likes, shares, comments, engagement_rate}`，互动率缺省按 `(赞+转+评)/浏览×100` 计算 |
| `hashtags` `keywords` | 否 | 数组；不提供则自动提取 |
| `sentiment` `sentiment_score` | 否 | 见 §3.3；不提供则自动分析 |
| `importance` | 否 | `high` / `medium` / `low`；不提供则自动评估 |

### 3.2 时间规则

- `publish_time` 支持：ISO 8601（`2026-01-05T10:30:00+08:00`、`...Z`）、`YYYY-MM-DD HH:MM:SS`、`YYYY/MM/DD HH:MM`、`YYYYMMDD`、10 位/13 位 Unix 时间戳。
- **不带时区的时间一律按北京时间（UTC+8）解释**；入库统一转为 UTC。
- 晚于「明天」的时间视为非法。

### 3.3 情绪取值

库里的 `sentiment` 只会是 `positive` / `negative` / `neutral` 三者之一（下游情绪工具只认这三个值）。
你可以传以下别名，系统会归一：

| 归一为 | 可接受的写法 |
|---|---|
| `positive` | `positive` `pos` `bullish` `bull` `积极` `正面` `看涨` `利好` `乐观` |
| `negative` | `negative` `neg` `bearish` `bear` `消极` `负面` `看跌` `利空` `悲观` |
| `neutral` | `neutral` `中性` `中立` `平稳` |

无法识别的值会被忽略并改为自动分析（`sentiment_source` 记为 `auto`）。

## 4. 自动补全是怎么做的

这些字段都是**内置规则**，用于给批量导入的数据一个可用的基线；如果你已有更准确的标注，直接在消息里传入即可覆盖。

| 字段 | 方法 | 局限 |
|---|---|---|
| `sentiment` / `sentiment_score` | 金融情绪词典（利好、涨停、暴雷、减持…）命中计数，前 2 个字符内出现否定词（不/没/未/无…）则翻转；得分 −1~1，≥0.2 为 positive，≤−0.2 为 negative；排除「未来」「不断」「无论」等非否定搭配 | 不理解反讽、语境和长句；词典外的表达判为 neutral |
| `keywords` | 话题标签（`#xx#`）优先，其后是词典命中词，最多 8 个 | 不做通用分词，只识别词典内的词 |
| `importance` / `importance_score` | 作者影响力 50% + 互动量 30% + 浏览量 20%，得分 0–100，≥60 high、≥30 medium | 作者无粉丝数/影响力时偏低 |

## 5. 响应

```json
{"success": true,
 "data": {"total": 100, "saved": 96, "updated": 0, "skipped": 3, "failed": 1,
          "errors": [{"index": 17, "error": "缺少 content"}]}}
```

- `skipped`：已存在（未覆盖）或批内重复。
- `errors[].index` 对应请求数组下标；最多返回 50 条。
- 全部失败返回 HTTP 422。

## 6. 查询与统计

```
GET /api/data-import/social-media?symbol=600519&platform=xueqiu&sentiment=positive&hours_back=72&skip=0&limit=20
GET /api/data-import/social-media/stats?symbol=600519&hours_back=168
```

统计返回：总数、情绪分布、平台分布、重要性分布、Top10 关键词、最新发布时间。

## 7. 下游如何使用这些数据

- **情绪工具**（`stock_sentiment`）：分析 A 股/港股时，优先读取最近 **7 天**、`symbol` 匹配的消息，统计情绪占比、平台分布、关键词、互动量，并挑出影响力高的消息写入分析上下文。
- 缓存适配器提供了 `get_social_media_data(symbol, hours_back, limit)` 读取接口，按 `symbol` 与时间窗查询；目前代码库内尚无调用方，数据格式与之兼容。

因此：`symbol` 必须与分析时使用的代码一致；超过 7 天的消息不会进入情绪工具的统计窗口。

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| `缺少 platform` / `缺少 symbol` / `缺少 content` | 必填字段为空 |
| `publish_time 无法解析` | 换用 §3.2 支持的格式 |
| `publish_time 在未来` | 检查时区与年份 |
| 重复导入后 `skipped` 很大 | 幂等键相同，属正常；需更新请 `overwrite=true` |
| 导入了但分析没用上 | 超过 7 天，或 `symbol` 与分析代码不一致 |
