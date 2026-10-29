import { ApiClient } from './request'

/** 决策结算闭环 API：结算状态统计、手动触发结算、历史教训查询 */

export interface RatingBucket {
  count: number
  hit_rate?: number
  mean_alpha: number
}

export interface SettlementStatus {
  total: number
  pending: number
  resolved: number
  review: number
  by_rating: Record<string, RatingBucket>
  pending_by_ticker: Record<string, number>
  holding_period_days: number
  log_path?: string | null
}

export interface SettleResult {
  settled: number
  failed: number
  failures: Array<{ ticker: string; date: string; reason: string }>
}

export interface DecisionLessons {
  symbol: string
  lessons: string
}

const decisionSettlementApi = {
  /** 决策结算状态统计（命中率 / 平均 alpha / 待结算分布） */
  getSettlementStatus() {
    return ApiClient.get<SettlementStatus>('/api/decisions/settlement-status')
  },

  /** 手动触发一次全量结算（与每交易日收盘后的定时任务同一入口） */
  triggerSettlement() {
    return ApiClient.post<SettleResult>('/api/decisions/settle')
  },

  /** 查看该股票的历史决策教训（注入后续分析的同一内容） */
  getLessons(symbol: string) {
    return ApiClient.get<DecisionLessons>('/api/decisions/lessons', { symbol })
  }
}

export default decisionSettlementApi
