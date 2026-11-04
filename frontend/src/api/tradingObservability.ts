/**
 * 交易可观测性 API（对应 app/pro/routers/trading_observability.py）
 *
 * 后端端点：
 * - GET  /api/trading-observability/metrics        Prometheus 文本（前端不用）
 * - GET  /api/trading-observability/metrics/json   结构化指标快照
 * - GET  /api/trading-observability/alerts         告警列表
 * - GET  /api/trading-observability/alerts/stats   告警统计
 * - POST /api/trading-observability/alerts/{id}/resolve  确认告警
 * - POST /api/trading-observability/patrol         手动巡检
 */

import { ApiClient } from './request'

/** 单条指标序列（counter 用 value，histogram 用 count/sum/avg） */
export interface MetricSeries {
  labels: Record<string, string>
  value?: number
  count?: number
  sum_seconds?: number
  avg_seconds?: number | null
}

export interface MetricEntry {
  type: 'counter' | 'histogram'
  series: MetricSeries[]
}

export type MetricsSnapshot = Record<string, MetricEntry>

/** 交易告警 */
export interface TradingAlert {
  alert_type: string
  severity: 'critical' | 'warning' | string
  order_id?: string
  settlement_id?: string
  broker_order_id?: string
  signal_id?: string
  symbol?: string
  side?: string
  quantity?: number
  user_id?: string
  reason?: string
  created_at?: string
  resolved?: boolean
  resolved_at?: string
  emailed_at?: string
  last_patrolled_at?: string
}

export interface AlertStats {
  open: number
  resolved: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
}

/** 获取指标快照 */
export async function getMetricsSnapshot(): Promise<MetricsSnapshot> {
  const res: any = await ApiClient.get('/api/trading-observability/metrics/json')
  return res?.data?.data || res?.data || {}
}

/** 获取告警列表 */
export async function getAlerts(params: {
  resolved?: boolean
  severity?: string
  limit?: number
} = {}): Promise<TradingAlert[]> {
  const query = new URLSearchParams()
  if (params.resolved !== undefined) query.set('resolved', String(params.resolved))
  if (params.severity) query.set('severity', params.severity)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  const res: any = await ApiClient.get(
    `/api/trading-observability/alerts${qs ? `?${qs}` : ''}`
  )
  return res?.data?.data || res?.data || []
}

/** 获取告警统计 */
export async function getAlertStats(): Promise<AlertStats> {
  const res: any = await ApiClient.get('/api/trading-observability/alerts/stats')
  return res?.data?.data || res?.data || { open: 0, resolved: 0, by_type: {}, by_severity: {} }
}

/** 确认某订单的告警已处理 */
export async function resolveAlert(orderId: string, note = ''): Promise<void> {
  const qs = note ? `?note=${encodeURIComponent(note)}` : ''
  await ApiClient.post(`/api/trading-observability/alerts/${orderId}/resolve${qs}`)
}

/** 手动触发一次巡检 */
export async function runPatrol(): Promise<{ patrolled: number; email?: any }> {
  const res: any = await ApiClient.post('/api/trading-observability/patrol')
  return res?.data?.data || res?.data || { patrolled: 0 }
}

// ==================== 配置热更新（third phase） ====================

export interface ConfigField {
  field: string
  type: 'int' | 'float' | 'bool'
  min: number | null
  max: number | null
  description: string
  current_value: any
}

export interface ConfigOverride {
  field: string
  description: string
  override_value: any
  runtime_value: any
  is_overridden: boolean
}

/** 获取可热更新字段定义 */
export async function getConfigSchema(): Promise<ConfigField[]> {
  const res: any = await ApiClient.get('/api/trading-config/schema')
  return res?.data?.data || res?.data || []
}

/** 获取当前覆盖状态 */
export async function getConfigOverrides(): Promise<ConfigOverride[]> {
  const res: any = await ApiClient.get('/api/trading-config/overrides')
  return res?.data?.data || res?.data || []
}

/** 更新单个配置（立即生效并持久化） */
export async function updateConfig(field: string, value: any) {
  const res: any = await ApiClient.post('/api/trading-config/update', { field, value })
  return res?.data || res
}

/** 重置配置为 .env 默认值 */
export async function resetConfig(fields: string[]) {
  const res: any = await ApiClient.post('/api/trading-config/reset', { fields })
  return res?.data || res
}
