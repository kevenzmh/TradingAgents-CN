/**
 * 提示词模板 API（Pro）
 *
 * 后端：app/pro/routers/prompt_templates.py（/api/prompt-templates）
 * 模板写入 Agent 运行时读取的 prompt_templates / user_template_configs：
 * 用户模板需“发布”后再“设为生效”，才会覆盖该 Agent 的系统默认提示词。
 */

import { ApiClient } from './request'

export const CONTENT_FIELDS = [
  { key: 'system_prompt', label: '系统提示词', rows: 16, hint: '角色、职责与总体要求' },
  { key: 'user_prompt', label: '用户提示词', rows: 10, hint: '每次任务发给模型的指令（可含变量）' },
  { key: 'tool_guidance', label: '工具使用指引', rows: 6, hint: '如何选择与调用工具' },
  { key: 'analysis_requirements', label: '分析要求', rows: 6, hint: '分析口径、深度、客观性约束' },
  { key: 'output_format', label: '输出格式', rows: 6, hint: '报告结构与格式' },
  { key: 'constraints', label: '约束条件', rows: 6, hint: '禁止事项、风险提示等' }
] as const

export type ContentKey = (typeof CONTENT_FIELDS)[number]['key']
export type TemplateContent = Record<ContentKey, string>

export const emptyContent = (): TemplateContent => ({
  system_prompt: '', user_prompt: '', tool_guidance: '', analysis_requirements: '', output_format: '', constraints: ''
})

export interface PromptTemplate {
  id: string
  agent_type: string
  agent_name: string
  template_name: string
  is_system: boolean
  status: 'draft' | 'active'
  version: number
  preference_type: string | null
  base_template_id: string | null
  base_version: number | null
  remark: string
  created_at: string | null
  updated_at: string | null
  variables: string[]
  is_active_for_me: boolean
  content?: TemplateContent
  system_prompt_preview?: string
}

export interface AgentRow {
  agent_type: string
  agent_name: string
  system_count: number
  my_count: number
  active_template_id: string | null
  using: 'system' | 'custom'
}

export interface TemplateQuery {
  scope?: 'all' | 'system' | 'mine'
  agent_type?: string
  agent_name?: string
  status?: 'draft' | 'active'
  keyword?: string
  skip?: number
  limit?: number
}

export interface PreviewResult {
  rendered: Record<string, string>
  empty_variables: string[]
  variables: string[]
}

const BASE = '/api/prompt-templates'

export async function listTemplates(q: TemplateQuery): Promise<{ items: PromptTemplate[]; total: number }> {
  const res: any = await ApiClient.get(BASE, q, { showLoading: false })
  return res?.data ?? { items: [], total: 0 }
}

export async function listAgents(): Promise<AgentRow[]> {
  const res: any = await ApiClient.get(`${BASE}/agents`, undefined, { showLoading: false })
  return res?.data ?? []
}

export async function getTemplate(id: string): Promise<PromptTemplate> {
  const res: any = await ApiClient.get(`${BASE}/${id}`, undefined, { showLoading: false })
  return res.data
}

export interface CreatePayload {
  agent_type: string
  agent_name: string
  template_name: string
  content: TemplateContent
  remark?: string
  base_template_id?: string | null
}

export async function createTemplate(payload: CreatePayload): Promise<PromptTemplate> {
  const res: any = await ApiClient.post(BASE, payload)
  return res.data
}

export async function updateTemplate(
  id: string,
  payload: { template_name?: string; content?: TemplateContent; remark?: string }
): Promise<PromptTemplate> {
  const res: any = await ApiClient.put(`${BASE}/${id}`, payload)
  return res.data
}

export async function duplicateTemplate(id: string, template_name?: string): Promise<PromptTemplate> {
  const res: any = await ApiClient.post(`${BASE}/${id}/duplicate`, { template_name })
  return res.data
}

export const publishTemplate = (id: string) => ApiClient.post(`${BASE}/${id}/publish`)
export const unpublishTemplate = (id: string) => ApiClient.post(`${BASE}/${id}/unpublish`)
export const activateTemplate = (id: string) => ApiClient.post(`${BASE}/${id}/activate`)
export const deleteTemplate = (id: string) => ApiClient.delete(`${BASE}/${id}`)
export const deactivateAgent = (agentType: string, agentName: string) =>
  ApiClient.delete(`${BASE}/active/${encodeURIComponent(agentType)}/${encodeURIComponent(agentName)}`)

export async function previewTemplate(payload: {
  template_id?: string
  content?: TemplateContent
  variables: Record<string, unknown>
}): Promise<PreviewResult> {
  const res: any = await ApiClient.post(`${BASE}/preview`, payload, { showLoading: false })
  return res.data
}

/** 与后端 extract_variables 一致：{name} 与 {{a.b}}，用于编辑时实时提示 */
export function extractVariables(content: Partial<TemplateContent>): string[] {
  const seen = new Set<string>()
  for (const text of Object.values(content)) {
    if (typeof text !== 'string') continue
    for (const m of text.matchAll(/\{\{([^}]+)\}\}/g)) seen.add(m[1].trim())
    for (const m of text.replace(/\{\{([^}]+)\}\}/g, '').matchAll(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g)) seen.add(m[1])
  }
  return [...seen]
}
