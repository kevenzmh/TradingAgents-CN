/**
 * 自选股分组 API（Pro）— 后端 app/pro/routers/watchlist_groups.py
 */

import { ApiClient } from './request'

export interface GroupStock {
  code: string
  name: string
}

export interface WatchlistGroup {
  id: string
  name: string
  description: string
  color: string
  stock_codes: string[]
  stocks: GroupStock[]
  stock_count: number
  analysis_depth: number | null
  sort_order: number
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

export interface GroupList {
  items: WatchlistGroup[]
  max_groups: number
  max_stocks: number
}

const BASE = '/api/watchlist-groups'

export async function listGroups(): Promise<GroupList> {
  const res: any = await ApiClient.get(BASE, undefined, { showLoading: false })
  return res.data
}

export interface GroupPayload {
  name?: string
  description?: string
  color?: string
  /** 1-5；0 表示清除（跟随定时分析配置的默认深度） */
  analysis_depth?: number | null
  is_active?: boolean
}

export async function createGroup(payload: GroupPayload & { name: string; stock_codes?: string[] }) {
  const res: any = await ApiClient.post(BASE, payload)
  return res.data as WatchlistGroup
}

export async function updateGroup(id: string, payload: GroupPayload) {
  const res: any = await ApiClient.put(`${BASE}/${id}`, payload)
  return res.data as WatchlistGroup
}

export const deleteGroup = (id: string) => ApiClient.delete(`${BASE}/${id}`)

export interface AddResult {
  added: string[]
  skipped: string[]
  invalid: string[]
  group: WatchlistGroup
}

export async function addStocks(id: string, codes: string[]): Promise<AddResult> {
  const res: any = await ApiClient.post(`${BASE}/${id}/stocks`, { codes })
  return res.data
}

export async function removeStocks(id: string, codes: string[]) {
  const res: any = await ApiClient.post(`${BASE}/${id}/stocks/remove`, { codes })
  return res.data as { removed: string[]; group: WatchlistGroup }
}

export async function importFavorites(id: string, tag?: string): Promise<AddResult> {
  const res: any = await ApiClient.post(`${BASE}/${id}/import-favorites`, { tag: tag || null })
  return res.data
}

export const reorderGroups = (ids: string[]) => ApiClient.put(`${BASE}/order`, { ids })
