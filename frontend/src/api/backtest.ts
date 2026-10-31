import { ApiClient } from './request'

/** 网格回测 API：股票×日期网格重放分析引擎 */

export interface BacktestRatingScore {
  count: number
  /** 方向命中率（乐观/审慎才有；中性无方向声明） */
  hit_rate?: number | null
  mean_alpha: number
}

export interface BacktestSummaryData {
  resolved: number
  pending: number
  unscored: number
  holding: string
  by_rating: Record<string, BacktestRatingScore>
}

export interface BacktestRunStatus {
  run_id: string
  status: 'pending' | 'running' | 'settling' | 'completed' | 'failed'
  tickers: string[]
  dates: string[]
  total_cells: number
  cells_done: number
  cells_run: number
  cells_skipped: number
  current_cell: string
  failures: Array<{ ticker: string; date: string; error: string }>
  settlement_failures: Array<{ ticker: string; error: string }>
  summary_text: string
  summary_data: BacktestSummaryData | null
  log_path: string
  created_at: string
  finished_at: string
  error: string
}

export interface StartBacktestPayload {
  tickers: string[]
  start_date: string
  end_date: string
  every_n_days?: number
  research_depth?: string
}

const backtestApi = {
  /** 启动网格回测（后台执行，返回 run_id） */
  start(payload: StartBacktestPayload) {
    return ApiClient.post<{ run_id: string }>('/api/backtest/start', payload)
  },

  /** 轮询 run 状态与进度 */
  getStatus(runId: string) {
    return ApiClient.get<BacktestRunStatus>(`/api/backtest/status/${runId}`)
  },

  /** 历史回测列表 */
  listRuns(limit: number = 20) {
    return ApiClient.get<BacktestRunStatus[]>('/api/backtest/runs', { limit })
  },

  /** 重算某 run 的分倾向统计（结算后会更新） */
  getSummary(runId: string) {
    return ApiClient.get<{ summary_text: string; summary_data: BacktestSummaryData }>(
      `/api/backtest/summary/${runId}`
    )
  },
}

export default backtestApi
