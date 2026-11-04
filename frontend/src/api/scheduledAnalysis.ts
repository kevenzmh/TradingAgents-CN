/**
 * 定时分析 API（Pro）— 后端 app/pro/routers/scheduled_analysis.py
 */

import { ApiClient } from './request'

export interface Slot {
  name: string
  cron_expression: string
  enabled: boolean
  group_ids: string[]
  analysis_depth: number | null
  has_workflow?: boolean
  next_run_at?: string | null
}

export interface ScheduleConfig {
  id: string
  name: string
  description: string
  enabled: boolean
  time_slots: Slot[]
  default_group_ids: string[]
  default_analysis_depth: number
  notify_on_complete: boolean
  notify_on_error: boolean
  send_email: boolean
  last_run_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ConfigList {
  items: ScheduleConfig[]
  timezone: string
  min_interval_minutes: number
  max_configs: number
  max_slots: number
}

export type ConfigPayload = Omit<ScheduleConfig, 'id' | 'last_run_at' | 'created_at' | 'updated_at' | 'time_slots'> & {
  time_slots: Array<Pick<Slot, 'name' | 'cron_expression' | 'enabled' | 'group_ids' | 'analysis_depth'>>
}

export interface HistoryItem {
  id: string
  config_id: string
  config_name: string
  time_slot_name: string
  status: 'success' | 'partial' | 'failed'
  total_count: number
  success_count: number
  failed_count: number
  task_ids: string[]
  result_summary: string | null
  created_at: string
}

const BASE = '/api/scheduled-analysis'

export async function listConfigs(): Promise<ConfigList> {
  const res: any = await ApiClient.get(`${BASE}/configs`, undefined, { showLoading: false })
  return res.data
}

export async function createConfig(p: ConfigPayload): Promise<ScheduleConfig> {
  const res: any = await ApiClient.post(`${BASE}/configs`, p)
  return res.data
}

export async function updateConfig(id: string, p: ConfigPayload): Promise<ScheduleConfig> {
  const res: any = await ApiClient.put(`${BASE}/configs/${id}`, p)
  return res.data
}

export const toggleConfig = (id: string, enabled: boolean) => ApiClient.post(`${BASE}/configs/${id}/toggle`, { enabled })
export const deleteConfig = (id: string) => ApiClient.delete(`${BASE}/configs/${id}`)
export const runSlotNow = (id: string, slot_index: number) => ApiClient.post(`${BASE}/configs/${id}/run`, { slot_index })

export async function getHistory(config_id?: string, limit = 20): Promise<HistoryItem[]> {
  const res: any = await ApiClient.get(`${BASE}/history`, { config_id, limit }, { showLoading: false })
  return res.data.items
}

export interface CronPreview {
  valid: boolean
  message: string
  next: string[]
}

export async function previewCron(cron_expression: string, count = 5): Promise<CronPreview> {
  const res: any = await ApiClient.post(`${BASE}/cron/preview`, { cron_expression, count }, { showLoading: false })
  return res.data
}
