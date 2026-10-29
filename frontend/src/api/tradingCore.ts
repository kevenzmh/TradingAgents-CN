import { ApiClient } from './request'

/** 交易核心 API（trading_core）：信号查询、订单确认/取消、风控状态、kill switch */

export interface TradingSignal {
  signal_id: string
  user_id?: string
  symbol: string
  market: string
  side: 'buy' | 'sell'
  quantity: number
  reference_price?: number | null
  confidence: number
  analysis_date: string
  action: string
  task_id?: string
  analysis_id?: string
  created_at: string
}

export type TradingOrderStatus =
  | 'pending_confirmation'
  | 'risk_rejected'
  | 'recorded'
  | 'filled'
  | 'failed'
  | 'submitted'
  | 'needs_review'
  | 'cancelled'
  | 'draft'

export interface RiskCheck {
  approved: boolean
  rejections: string[]
  details?: Record<string, unknown>
}

export interface OrderFill {
  status?: string
  price?: number
  notional?: number
  broker_order_id?: string
  error?: string
  [key: string]: unknown
}

export interface TradingOrder {
  order_id: string
  user_id?: string
  task_id?: string
  symbol: string
  market?: string
  side: 'buy' | 'sell'
  quantity: number
  limit_price?: number
  notional?: number
  status: TradingOrderStatus
  broker_order_id?: string
  risk_check?: RiskCheck
  fill?: OrderFill | null
  created_at: string
  [key: string]: unknown
}

export interface KillSwitchState {
  active: boolean
  reason?: string
  at?: string
  [key: string]: unknown
}

export interface RiskLimits {
  max_position_pct?: number
  max_order_notional?: number
  max_daily_orders?: number
  max_daily_notional?: number
  min_confidence?: number
  signal_max_age_minutes?: number
}

export interface DailyCounters {
  orders?: number
  notional?: number
  [key: string]: unknown
}

export interface RiskStatus {
  live_trading_enabled: boolean
  trading_mode: 'signal_only' | 'dry_run' | 'live' | string
  kill_switch: KillSwitchState
  limits: RiskLimits
  today: DailyCounters
}

export interface SignalsResponse {
  signals: TradingSignal[]
}

export interface OrdersResponse {
  orders: TradingOrder[]
}

export interface OrderActionResult {
  order_id: string
  status: TradingOrderStatus
  risk?: RiskCheck
  fill?: OrderFill | null
  [key: string]: unknown
}

const tradingApi = {
  /** 查询交易信号（分析产出的可交易倾向记录） */
  getSignals(params: { limit?: number; symbol?: string } = {}) {
    return ApiClient.get<SignalsResponse>('/api/trading/signals', params)
  },

  /** 查询交易引擎订单（含风控拒绝与待确认单） */
  getOrders(status?: string, limit: number = 50) {
    const params: Record<string, unknown> = { limit }
    if (status) params.status = status
    return ApiClient.get<OrdersResponse>('/api/trading/orders', params)
  },

  /** 人工确认待确认订单（确认后后端会重跑风控再撮合） */
  confirmOrder(orderId: string) {
    return ApiClient.post<OrderActionResult>('/api/trading/orders/confirm', {
      order_id: orderId
    })
  },

  /** 取消待确认订单 */
  cancelOrder(orderId: string) {
    return ApiClient.post<OrderActionResult>('/api/trading/orders/cancel', {
      order_id: orderId
    })
  },

  /** 风控状态：模式、限额、今日已用、kill switch */
  getRiskStatus() {
    return ApiClient.get<RiskStatus>('/api/trading/risk/status')
  },

  /** 设置/解除全局交易熔断（仅管理员；开启后所有执行请求立即拒绝） */
  setKillSwitch(reason: string, off: boolean = false) {
    return ApiClient.post<Record<string, unknown>>('/api/trading/risk/kill-switch', {
      reason,
      off
    })
  }
}

export default tradingApi
