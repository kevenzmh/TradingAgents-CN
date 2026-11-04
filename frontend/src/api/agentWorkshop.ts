/**
 * Agent 工坊 API（Pro）— 后端 app/pro/routers/agent_workshop.py
 *
 * 生命周期：草稿 → 缺口分析 → 工具计划 → 构建版本 → 真数据测试 → 评估 → 发布 / 迭代
 */

import { ApiClient } from './request'

export type Phase = 'draft' | 'testing' | 'evaluation' | 'publish-ready' | 'published' | 'archived'

export interface WorkshopVersion {
  version_id: string
  spec_id: string
  status: string
  version_number: number
  required_tools?: string[]
  output_field?: string
  phase: Phase
  phase_label: string
  created_at?: string
  updated_at?: string
  last_debug_report?: string
  latest_evaluation?: { decision: string; created_at?: string; user_feedback?: string } | null
}

export interface SpecRuntime {
  enabled: boolean
  version_status?: string
  runtime_status?: string
}

export interface WorkshopSpec {
  spec_id: string
  name: string
  domain?: string
  primary_goal: string
  responsibilities?: string[]
  non_goals?: string[]
  constraints?: string[]
  preferred_methods?: string[]
  status?: string
  current_version_id?: string | null
  updated_at?: string
  versions: WorkshopVersion[]
  version_count?: number
  latest_version?: WorkshopVersion | null
  runtime?: SpecRuntime | null
  sessions?: Array<{ session_id: string; status: string; completeness_score?: number; created_at?: string }>
}

export interface Gap {
  gap_id: string
  description: string
  blocking: boolean
  suggested_resolution?: string
}

export interface GapReport {
  blocking_gaps: Gap[]
  non_blocking_gaps: Gap[]
  available_tools: string[]
  missing_capabilities: string[]
  completeness_score: number
  feasibility_status: string
  analyzed_at?: string
}

export interface WorkshopSession {
  session_id: string
  spec_id: string
  status: string
  gap_report: GapReport | null
  completeness_score: number
  builder_contracts?: { tooling_plan?: { required_tools: string[]; plan: string; missing_capabilities: string[] } }
  current_version_id?: string | null
}

export interface ToolInfo {
  tool_id: string
  name: string
  description: string
  category: string
}

export interface DraftPayload {
  agent_id: string
  agent_name: string
  target_responsibility: string
  output_field: string
  tool_ids: string[]
  non_goals: string[]
  preferred_methods: string[]
  constraints: string[]
}

const BASE = '/api/agent-workshop'
/** 业务失败（409/400）时交给调用方展示后端的具体原因 */
const QUIET = { skipErrorHandler: true, showLoading: false } as const

export class WorkshopError extends Error {
  detail: any
  status?: number
  constructor(message: string, detail?: any, status?: number) {
    super(message)
    this.detail = detail
    this.status = status
  }
}

function toError(e: any): WorkshopError {
  const detail = e?.response?.data?.detail
  const status = e?.response?.status
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) return new WorkshopError(detail.message || '操作失败', detail, status)
  return new WorkshopError(typeof detail === 'string' ? detail : e?.message || '操作失败', detail, status)
}

async function call<T = any>(fn: () => Promise<any>): Promise<T> {
  try {
    const res: any = await fn()
    return res?.data as T
  } catch (e) {
    throw toError(e)
  }
}

export const listSpecs = (keyword = '') => call<WorkshopSpec[]>(() => ApiClient.get(`${BASE}/specs`, { keyword: keyword || undefined }, QUIET))
export const getSpec = (id: string) => call<WorkshopSpec>(() => ApiClient.get(`${BASE}/specs/${id}`, undefined, QUIET))
export const getSession = (id: string) => call<WorkshopSession>(() => ApiClient.get(`${BASE}/sessions/${id}`, undefined, QUIET))
export const createDraft = (p: DraftPayload) =>
  call<{ spec_id: string; session_id: string; message?: string }>(() => ApiClient.post(`${BASE}/drafts`, p, QUIET))
export const analyzeGaps = (sid: string) => call<{ gap_report: GapReport }>(() => ApiClient.post(`${BASE}/sessions/${sid}/analyze`, {}, QUIET))
export const toolingPlan = (sid: string) => call<{ tooling_plan: any }>(() => ApiClient.post(`${BASE}/sessions/${sid}/tooling-plan`, {}, QUIET))
export const acknowledgeGaps = (sid: string) => call<{ acknowledged: string[] }>(() => ApiClient.post(`${BASE}/sessions/${sid}/acknowledge-gaps`, {}, QUIET))
export const buildVersion = (sid: string) => call<{ version_id: string }>(() => ApiClient.post(`${BASE}/sessions/${sid}/build`, {}, QUIET))
export const testVersion = (vid: string, p: { symbol: string; market_type: string; analysis_date?: string; user_goal?: string }) =>
  call<{ report: string; report_length: number; success: boolean }>(() => ApiClient.post(`${BASE}/versions/${vid}/test`, p, { ...QUIET, timeout: 300000 }))
export const evaluateVersion = (vid: string, user_feedback = '') =>
  call<{ decision: string }>(() => ApiClient.post(`${BASE}/versions/${vid}/evaluate`, { user_feedback }, QUIET))
export const publishVersion = (vid: string) => call(() => ApiClient.post(`${BASE}/versions/${vid}/publish`, {}, QUIET))
export const iterateVersion = (vid: string, feedback: string) =>
  call<{ session_id: string; version_id: string }>(() => ApiClient.post(`${BASE}/versions/${vid}/iterate`, { feedback }, QUIET))
export const deleteVersion = (vid: string, force = false) => call(() => ApiClient.delete(`${BASE}/versions/${vid}?force=${force}`, QUIET))
export const syncTools = (vid: string, required_tools: string[]) =>
  call(() => ApiClient.put(`${BASE}/versions/${vid}/tools`, { required_tools, default_tools: required_tools.slice(0, 4), max_tool_calls: Math.max(6, required_tools.length) }, QUIET))
export const listAvailableTools = (keyword = '') => call<ToolInfo[]>(() => ApiClient.get(`${BASE}/available-tools`, { keyword: keyword || undefined }, QUIET))
