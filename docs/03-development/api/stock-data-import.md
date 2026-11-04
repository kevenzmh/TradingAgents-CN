# 股票数据批量导入 API 指南

通过 HTTP 接口把自有数据批量写入系统，覆盖 5 类股票数据：基本信息、实时行情、财务数据、新闻、历史K线。
社媒消息见另一份指南（`social-media-import.md`）。

> 所有接口都需要登录令牌。导入的数据**只读不改券商账户**，仅写本系统数据库。

## 1. 认证

```bash
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<你的密码>"}'
```

取响应里的 `data.access_token`，之后所有请求带请求头：

```
Authorization: Bearer <access_token>
```

## 2. 接口总览

| 数据 | 方法与路径 | 请求体主键 | 存储集合 | 数据源标识 |
|---|---|---|---|---|
| 股票基本信息 | `POST /api/stock-data/save-basic-info` | `stocks[]` | `stock_basic_info` | `local` |
| 实时行情 | `POST /api/stock-data/save-quotes` | `quotes[]` | `market_quotes` | `local` |
| 财务数据 | `POST /api/financial-data/save` | `symbol` + `financial_data[]` | `stock_financial_data` | `local` |
| 新闻数据 | `POST /api/news-data/save` | `news_list[]`（可选 `symbol`） | `stock_news` | `local` |
| 历史K线 | `POST /api/data-import/historical` | `symbol` + `records[]` | `stock_daily_quotes` | `user_import` |

通用约定：

- 股票代码 `symbol` 为 **6 位数字字符串**（如 `"600519"`），不带交易所后缀（历史K线接口例外，见 §3.5）。
- `overwrite`：已存在数据时是否覆盖。基本信息、财务、新闻默认 `false`（已存在则跳过）；行情、历史K线默认 `true`。
- 单条数据校验失败**不影响其余数据**，失败原因在响应的 `errors` 中（最多返回前 10 条，历史K线最多 50 条）。
- 响应统一形如 `{"success": true, "data": {"saved", "updated", "skipped", "failed", "total", "errors"}, "message": "..."}`。

## 3. 各接口说明

### 3.1 股票基本信息

`POST /api/stock-data/save-basic-info`

必填字段：

| 字段 | 说明 |
|---|---|
| `symbol` | 6 位数字代码 |
| `name` | 股票名称 |
| `area` | 所在地区 |
| `industry` | 所属行业 |
| `list_date` | 上市日期 |

另须**至少提供一组**财务数据（否则该条被拒绝）：

- 市值：`total_mv` / `circ_mv`
- 财务比率：`pe` / `pb` / `ps` / `pe_ttm` / `pb_mrq` / `ps_ttm` / `roe` 任一
- 股本：`total_share` / `float_share`

```json
{
  "stocks": [
    {"symbol": "600519", "name": "贵州茅台", "area": "贵州", "industry": "白酒",
     "list_date": "2001-08-27", "total_mv": 1850000000000, "pe": 24.5, "pb": 8.1}
  ],
  "overwrite": false
}
```

### 3.2 实时行情

`POST /api/stock-data/save-quotes`

必填：`symbol`（6 位数字）。其余字段原样写入，建议提供 `close`、`open`、`high`、`low`、`pre_close`、`volume`、`amount`、`pct_chg`。
系统会补全 `code`（默认等于 `symbol`）供读取方按代码查询。

```json
{"quotes": [{"symbol": "600519", "close": 1688.0, "open": 1675.0, "high": 1692.0,
             "low": 1670.5, "pre_close": 1672.0, "volume": 21345, "amount": 360000000,
             "pct_chg": 0.96}],
 "overwrite": true}
```

### 3.3 财务数据

`POST /api/financial-data/save`

必填：请求体 `symbol`；每条 `financial_data` 必须有 `report_period`（**8 位数字 `YYYYMMDD`**，报告期末，如 `20250930`）。其余财务指标字段原样写入。同一 `symbol` + `report_period` 视为同一条。

```json
{"symbol": "600519",
 "financial_data": [{"report_period": "20250930", "revenue": 1.2e11, "net_profit": 6.1e10, "roe": 24.3}],
 "overwrite": false}
```

### 3.4 新闻数据

`POST /api/news-data/save`

每条必填：`title`、`url`（必须是 `http://` 或 `https://`）、`publish_time`（ISO 8601 字符串，如 `2026-01-05T10:30:00+08:00` 或 `...Z`）。
以 `url` 判重。请求体可带 `symbol`，系统会把它写入每条新闻的 `symbol` / `symbols`。

```json
{"symbol": "600519",
 "news_list": [{"title": "贵州茅台发布三季报", "url": "https://example.com/news/1",
                "publish_time": "2026-01-05T10:30:00+08:00", "source": "示例财经"}],
 "overwrite": false}
```

### 3.5 历史K线

`POST /api/data-import/historical`

请求体：

| 字段 | 必填 | 说明 |
|---|---|---|
| `symbol` | 是 | 股票代码，`600519` 或 `600519.SH` 均可（A 股数字自动补齐 6 位） |
| `market` | 否 | `CN`（默认）/ `HK` / `US` |
| `period` | 否 | `daily`（默认）/ `weekly` / `monthly` |
| `overwrite` | 否 | 同一交易日已存在时是否覆盖，默认 `true` |
| `records` | 是 | K线记录，单次最多 **5000** 条 |

每条记录：

| 字段 | 必填 | 说明 |
|---|---|---|
| `date`（或 `trade_date`） | 是 | `YYYY-MM-DD` / `YYYYMMDD` / `YYYY/MM/DD`，不得晚于明天 |
| `close` | 是 | 收盘价，必须 > 0 |
| `open` `high` `low` | 否 | 缺省时由收盘价补齐；须满足 `high ≥ low`，且收盘价落在 `[low, high]` 内 |
| `volume`（或 `vol`）`amount`（或 `turnover`） | 否 | 成交量 / 成交额，不得为负，缺省为 0 |
| `pre_close` `turnover_rate` `pe` `pb` `ps` `volume_ratio` | 否 | 可选扩展字段 |

行为说明：

- 数据源固定为 `user_import`：**不会覆盖**同步任务写入的 tushare / akshare / baostock 数据，也不触发其单位换算。
- 同一批内同一交易日重复时，保留**最后一条**（视为更正）。
- 涨跌额、涨跌幅在提供 `pre_close` 时自动计算。
- 全部记录校验失败返回 HTTP 422，并附带逐条原因。

```json
{"symbol": "600519", "market": "CN", "period": "daily", "overwrite": true,
 "records": [
   {"date": "2026-01-05", "open": 1675, "high": 1692, "low": 1670.5, "close": 1688, "volume": 21345, "amount": 360000000},
   {"date": "2026-01-06", "close": 1695}
 ]}
```

## 4. Python 示例

```python
import requests

BASE = "http://localhost:8000"
token = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "admin", "password": "<密码>"}).json()["data"]["access_token"]
headers = {"Authorization": f"Bearer {token}"}

resp = requests.post(f"{BASE}/api/data-import/historical", headers=headers, json={
    "symbol": "600519",
    "records": [{"date": "2026-01-05", "close": 1688.0}],
})
print(resp.json())
```

## 5. 测试程序

仓库自带命令行测试程序，一次覆盖全部 6 类数据（含社媒消息）：

```bash
# 默认只预览将要发送的请求，不写库
python scripts/test_batch_import_apis.py --base http://localhost:8000 --user admin --password <密码>
# 真正发送（使用测试代码 999999；社媒测试消息结束后自动清理）
python scripts/test_batch_import_apis.py ... --send
# 只测某几类（basic,quotes,financial,news,historical,social）
python scripts/test_batch_import_apis.py ... --send --only historical,social
```

> 基本信息 / 行情 / 财务 / 新闻 / K线没有删除接口，测试写入的 `999999` 数据会留在库中，需要时请手工清理。

也可在「系统设置 → 数据导入管理」页面直接下载，或通过 `GET /api/data-import/test-program` 获取。

## 6. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `symbol 必须是6位数字` | 基本信息 / 行情接口不接受 `600519.SH` 这类带后缀的代码，请去掉后缀 |
| 基本信息报「缺少核心业务字段」 | `area` / `industry` / `list_date` 任一为空 |
| 基本信息报「缺少财务数据」 | 市值、财务比率、股本三组中一组都没提供 |
| 财务报 `report_period 必须是8位数字` | 用 `20250930`，不要用 `2025-09-30` |
| 新闻报 `url 必须是有效的 HTTP/HTTPS 链接` | 补全协议头 |
| K线报 `close(…) 超过 high(…)` | 收盘价必须落在最高/最低价之间，请核对数据 |
| 导入后分析仍读不到 | 确认 `symbol` 与分析时使用的代码一致。读取最新收盘价时不区分周期，取 `trade_date` 最大的一条（周/月K线与日K线在同一集合，请避免导入日期晚于最新交易日的周/月K线） |
