# 前端展示补齐方案：交易核心 / 决策结算 / run_settings / 回测

> 状态：方案（未实施）
> 背景：后端四块功能（trading_core 信号流、决策结算闭环、报告 run_settings、回测框架）API 已注册可用，前端零消费。

## 总览

| # | 功能 | 后端现状 | 缺口 | 优先级 | 工作量 |
|---|------|---------|------|--------|--------|
| P1 | 实盘信号流页面 | `/api/trading` 6 端点齐备 | 无页面、无菜单、无 API 模块 | **高**（dry_run 必需） | ~1.5 天 |
| P2 | 决策结算状态卡 | `/api/decisions` 3 端点齐备 | 无页面 | 中（观察信号质量） | ~0.5 天 |
| P3 | 报告 run_settings 折叠区 | 已入库 `analysis_reports.run_settings`，但 reports.py 详情白名单未透出 | API 白名单加 1 行 + 前端折叠区 | 中 | ~2 小时 |
| P4 | 回测 | 纯 Python 模块，无 API | 全部 | 低（先 API 化再谈页面） | 后置 |

实施顺序：P3 → P1 → P2 →（观察期）→ P4。P3 最小且独立，先行验证报告链路。

---

## P3：报告详情页 run_settings 折叠区（约 2 小时）

**目的**：实盘审计链的前端可视化——每份报告能回答"当时用什么配置跑的"。

### 后端（1 处）

[app/routers/reports.py](../app/routers/reports.py) `get_report_detail` 的 analysis_reports 命中分支字段白名单加：

```python
"run_settings": doc.get("run_settings", {}),
```

旧报告无该字段，回退 `{}` 前端自然隐藏，无兼容问题。

### 前端（1 处）

[frontend/src/views/Reports/ReportDetail.vue](../frontend/src/views/Reports/ReportDetail.vue)：

1. `ReportDetailData` interface 加 `run_settings?: Record<string, unknown>`
2. 元信息行下方加 `el-collapse` 折叠区"运行配置"，仅在 `Object.keys(report.run_settings).length > 0` 时渲染
3. 内容用 `el-descriptions` 两列渲染 key-value；已知键给中文标签映射：

| 键 | 标签 |
|---|---|
| quick_model / deep_model / model_info | 快速/深度模型 |
| research_depth / analysts / max_debate_rounds | 深度/分析师/辩论轮数 |
| preference_type / engine_type / workflow_id | 偏好/引擎/工作流 |
| temperature_env_override | 温度覆盖（TRADINGAGENTS_TEMPERATURE） |
| analyst_max_tool_calls | 工具调用上限（env） |
| engine_mode / live_trading_enabled | 交易模式 / 实盘开关 |
| app_version | 系统版本 |

未知键原样显示键名，不吞字段。

**不做**：编辑功能、对比视图、独立 API（详情接口顺带返回即可）。

---

## P1：交易信号中心页面（约 1.5 天）

**目的**：dry_run 模式的操作面板——信号 → 风控 → 待确认 → 人工 confirm/cancel 的完整闭环，加 kill switch。

### API 模块（新建 `frontend/src/api/tradingCore.ts`）

照 [paper.ts](../frontend/src/api/paper.ts) 惯例（ApiClient + interface），覆盖 6 端点：

```typescript
export interface TradingSignal { signal_id, symbol, market, side: 'buy'|'sell', quantity,
  reference_price?, confidence, analysis_date, action /* 乐观|审慎 */, task_id, analysis_id, created_at }
export interface TradingOrder { order_id, symbol, side, quantity, limit_price, notional,
  status: 'pending_confirmation'|'risk_rejected'|'recorded'|'filled'|'failed'|'submitted'|'needs_review'|'cancelled',
  broker_order_id?, risk_check: { approved, rejections: string[], details }, fill?, created_at }
export interface RiskStatus { live_trading_enabled, trading_mode,
  kill_switch: { active, reason?, at? }, limits: {...}, today: {...} }
```

| 方法 | 端点 |
|---|---|
| getSignals(params) / getOrders(status?) | GET `/api/trading/signals`、`/orders` |
| confirmOrder(orderId) / cancelOrder(orderId) | POST `/api/trading/orders/confirm`、`/cancel` |
| getRiskStatus() | GET `/api/trading/risk/status` |
| setKillSwitch(reason, off) | POST `/api/trading/risk/kill-switch`（后端仅 admin） |

### 页面（新建 `frontend/src/views/TradingCore/index.vue`）

布局——上中下三段：

1. **风控状态条**（顶部 el-alert 风格横条）
   - 当前 `trading_mode` tag（signal_only 灰 / dry_run 橙 / live 红）
   - 今日已用额度 vs 限额（订单数/名义额两个进度条）
   - kill switch 状态：激活时整页红条置顶"交易已熔断：{reason}"；admin 可见"解除"按钮，非 admin 只读
2. **待确认订单**（核心操作区，el-table）
   - 过滤 `status=pending_confirmation`，空态显示"暂无待确认订单"
   - 列：代码 / 方向（buy 绿 sell 红）/ 数量 / 委托价 / 名义额 / 信号置信度 / 分析日期 / 时间
   - 操作列：**确认**（二次确认弹窗，展示风控明细后 POST confirm，成功后刷新列表）/ **取消**（POST cancel）
   - 行内展开（el-table-column type="expand"）：`risk_check.rejections` 全文 + `fill` 成交明细 + 关联 signal/analysis_id 跳转报告详情
3. **全部订单 + 信号**（下方 el-tabs 两个 tab）
   - 订单 tab：全量订单表 + status 筛选下拉（8 个状态各自带色 tag：pending_confirmation 橙 / filled 绿 / risk_rejected 红 / needs_review 紫红醒目 / submitted 蓝 / recorded 灰）
   - 信号 tab：信号表，行点击带 `analysis_id` 跳报告详情页（审计链闭环：信号 → 源头报告 → run_settings）

**安全注意**：确认/取消操作必须二次弹窗 + 操作后强制刷新（后端确认时会**重跑风控**，前端不能假设 confirm 一定变 filled——响应 status 可能是 filled/failed/submitted，按响应渲染结果提示）。

### 路由与菜单（2 处）

- [router/index.ts](../frontend/src/router/index.ts)：照 TradeReview（629-643 行）模式加 `/trading` 块，BasicLayout 承载
- [SidebarMenu.vue](../frontend/src/components/Layout/SidebarMenu.vue)：`/review` 下方加 `el-menu-item index="/trading"`，图标 `Warning`（有限责任公司编入"交易"区组）

**不做**：手动下单（页面只管理分析产生的信号，不提供人为造单入口——与"LLM 不生成委托参数"的安全设计一致）；订单图表；WebSocket 实时推送（轮询 30s 足够，低频场景）。

---

## P2：决策结算状态卡（约 0.5 天）

**目的**：信号质量可见——命中率/平均 alpha/待结算数，判断何时从 dry_run 升级 live 的数据依据。

### 落点选择

三选一，倾向 **(a)**：

- **(a) Dashboard 加 KPI 卡**（[KpiCards.vue](../frontend/src/components/Dashboard/KpiCards.vue) 旁加一卡）：跳转 `/trading` 或 Memory 提升
- (b) Memory 页加 tab（[Memory/index.vue](../frontend/src/views/Memory/index.vue)）：与"决策记忆"主题契合
- (c) TradingCore 页加第 4 段：信息最聚拢但页面变长

### API 模块（新建 `frontend/src/api/decisionSettlement.ts`）

```
getSettlementStatus() -> GET /api/decisions/settlement-status
triggerSettlement()   -> POST /api/decisions/settle   // 手动结算按钮，admin/进阶用户
getLessons(symbol)    -> GET /api/decisions/lessons   // 个股历史教训查看
```

`SettlementStatus` 结构（`get_settlement_status` 已定型）：`total / pending / resolved / review / by_rating（含 count、hit_rate、mean_alpha）/ pending_by_ticker / holding_period_days`。

### 页面内容

1. **KPI 行**：总决策数 / 待结算数 / 已结算 / 待复核（review，紫 tag）
2. **按倾向统计表**（乐观/审慎两行）：样本数、方向命中率、平均 alpha（正绿负红）
3. **待结算按代码分布**：`pending_by_ticker` 热力列表（数量倒序）
4. **操作**："立即结算"按钮（调 settle，转圈显示 settled/failed 数，失败原因 tooltip）
5. （可选）某报告详情联动：报告页如决策已记录，显示"该信号结算状态：pending/已结算 alpha=x%"

**不做**：结算明细日志表（log 是 Markdown 追加文件，无明细 API；状态统计足够）；教训编辑。

---

## P4：回测（后置，先 API 化）

页面暂不做。理由：`tradingagents/backtest.py` 目前是**库**（`run_cell` 回调注入式，无独立运行入口），先回答"谁来跑"：

1. 若实盘前才需要批量验证信号 → 写一个 `app/services/backtest_service.py`（复用任务中心排队跑网格，一格一个 UnifiedAnalysisTask + 独立决策日志），加 `POST /api/backtest/runs` + `GET /api/backtest/runs/{id}`（进度回调已有 `progress` 参数）+ `summarize` 结果端点
2. 页面届时仅是：tickers×日期网格表单 + run 列表 + `summarize` 命中率表（结构同 P2 统计表，可复用组件）

**本次不做**，待 P1-P2 上线后按实盘节奏决定。

---

## 实施顺序与验证

```
P3（2h）──┬─ reports.py 白名单 + ReportDetail 折叠区 → 手工验证：旧报告隐藏新区/新报告显示完整配置
P1（1.5d）┤─ api/tradingCore.ts → TradingCore/index.vue → 路由+菜单 → dev server 手工全流程：
          │    signal_only 看信号记录 → 切 dry_run 产生 pending → confirm 看成交 → cancel 一单 → kill switch 开关
P2（0.5d）┴─ api/decisionSettlement.ts + Dashboard KPI / Memory tab → 手动结算按钮跑通
```

每步完成后端点 `npm run build`（frontend）确认无 TS 错误；不需要后端改动除 P3 一行白名单。

## 设计原则贯穿

- **审计链闭环**：信号行 → analysis_id → 报告详情 → run_settings，任何一笔委托可一路溯源到"当时什么配置产出的结论"
- **不确定状态醒目**：needs_review/submitted 用紫/蓝高亮，与"受理≠成交、不确定绝不静默"的后端原则一致
- **页面不造单**：全部操作对象都是系统产生的信号/订单，页面无人工新建委托入口
