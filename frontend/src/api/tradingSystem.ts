/**
 * 交易计划 API（社区版重建）
 *
 * 原 Pro 交易计划管理（trading_plan_check 门控）在社区版开源分离时被剔除，
 * 本文件曾为返回空数据的 stub。后端已重建 /api/trading-systems CRUD + 模板端点
 * （app/pro/routers/trading_systems.py），此文件对接真实接口：
 * - 引用组件（持仓复盘/操作历史的"关联交易计划"下拉）自然恢复
 * - 模板数据源：config/tradingSystemTemplates.ts 前端本地保留（预览用）
 */

import { ApiClient } from './request'

/** 交易计划类型（style: short_term / medium_term / long_term） */
export interface TradingSystem {
  id?: string
  name: string
  style: string
  description?: string | null
  is_active?: boolean
  created_at?: string | null
}

export interface TradingSystemsResponse {
  data: { systems: TradingSystem[] }
}

/** 交易计划模板（与前端 tradingSystemTemplates.ts 同构） */
export interface TradingSystemTemplateInfo {
  id: string
  name: string
  description: string
  style: string
  risk_profile: string
  data: Record<string, any>
}

/**
 * 获取交易计划列表
 */
export const getTradingSystems = async (): Promise<TradingSystemsResponse> => {
  const res: any = await ApiClient.get('/api/trading-systems')
  const data = res?.data?.data || res?.data || {}
  return { data: { systems: data.systems || [] } }
}

/**
 * 获取交易计划模板列表
 */
export const getTradingSystemTemplates = async (): Promise<TradingSystemTemplateInfo[]> => {
  const res: any = await ApiClient.get('/api/trading-systems/templates')
  const data = res?.data?.data || res?.data || {}
  return data.templates || []
}

/**
 * 从模板创建交易计划
 */
export const createTradingSystemFromTemplate = async (templateId: string) => {
  return ApiClient.post<{ id: string }>(`/api/trading-systems/from-template`, { template_id: templateId })
}

/**
 * 设为默认交易计划
 */
export const activateTradingSystem = async (systemId: string) => {
  return ApiClient.post(`/api/trading-systems/${systemId}/activate`)
}

/**
 * 删除交易计划
 */
export const deleteTradingSystem = async (systemId: string) => {
  return ApiClient.delete(`/api/trading-systems/${systemId}`)
}

export interface TradingSystemDetail {
  id: string
  name: string
  description: string | null
  style: string
  risk_profile: string
  is_active: boolean
  template_id?: string
  data: Record<string, any>
  created_at?: string | null
  updated_at?: string | null
}

export interface TradingSystemPayload {
  name?: string
  description?: string
  style?: string
  is_active?: boolean
  data?: Record<string, any>
}

/** 交易计划详情（含规则数据） */
export const getTradingSystem = async (systemId: string): Promise<TradingSystemDetail> => {
  const res: any = await ApiClient.get(`/api/trading-systems/${systemId}`, undefined, { showLoading: false })
  const doc = res?.data || {}
  return { ...doc, id: doc.id || doc._id }
}

/** 新建交易计划（自定义） */
export const createTradingSystem = async (payload: TradingSystemPayload & { name: string }) => {
  const res: any = await ApiClient.post('/api/trading-systems', payload)
  return res.data as { id: string }
}

/** 更新交易计划 */
export const updateTradingSystem = async (systemId: string, payload: TradingSystemPayload) => {
  return ApiClient.put(`/api/trading-systems/${systemId}`, payload)
}
