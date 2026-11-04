/**
 * 数据导入 API（Pro）
 *
 * 后端：
 * - app/pro/routers/data_import.py   历史K线 / 社媒消息 / 指南 / 测试程序
 * - app/routers/stock_data.py        基本信息 / 实时行情
 * - app/routers/financial_data.py    财务数据
 * - app/routers/news_data.py         新闻数据
 *
 * 在线导入对话框由 IMPORT_KINDS 驱动：每种数据声明路径、请求体键、是否需要股票代码、示例模板。
 */

import { ApiClient } from './request'

export type ImportKindKey = 'basic' | 'quotes' | 'financial' | 'news' | 'historical' | 'social'

export interface ImportKind {
  key: ImportKindKey
  label: string
  path: string
  /** 请求体里承载数据数组的键 */
  arrayKey: string
  /** 是否需要在请求体里附带股票代码（财务、历史K线必填，新闻可选） */
  symbol: 'required' | 'optional' | 'none'
  /** 是否有 overwrite 开关及其默认值 */
  overwriteDefault: boolean
  /** 历史K线额外参数 */
  extra?: Array<{ key: string; label: string; options: string[]; default: string }>
  hint: string
  /** 示例数组（JSON 文本，已格式化） */
  template: string
}

const pretty = (v: unknown) => JSON.stringify(v, null, 2)

export const IMPORT_KINDS: ImportKind[] = [
  {
    key: 'basic',
    label: '股票基本信息',
    path: '/api/stock-data/save-basic-info',
    arrayKey: 'stocks',
    symbol: 'none',
    overwriteDefault: false,
    hint: '必填：symbol(6位)、name、area、industry、list_date；另需至少一组 市值/财务比率/股本。',
    template: pretty([
      { symbol: '600519', name: '贵州茅台', area: '贵州', industry: '白酒', list_date: '2001-08-27', total_mv: 1850000000000, pe: 24.5, pb: 8.1 }
    ])
  },
  {
    key: 'quotes',
    label: '实时行情',
    path: '/api/stock-data/save-quotes',
    arrayKey: 'quotes',
    symbol: 'none',
    overwriteDefault: true,
    hint: '必填：symbol(6位数字)。建议提供 close/open/high/low/pre_close/volume/amount/pct_chg。',
    template: pretty([
      { symbol: '600519', close: 1688.0, open: 1675.0, high: 1692.0, low: 1670.5, pre_close: 1672.0, volume: 21345, amount: 360000000, pct_chg: 0.96 }
    ])
  },
  {
    key: 'financial',
    label: '财务数据',
    path: '/api/financial-data/save',
    arrayKey: 'financial_data',
    symbol: 'required',
    overwriteDefault: false,
    hint: '需填写股票代码；每条必填 report_period（8位 YYYYMMDD，如 20250930）。',
    template: pretty([
      { report_period: '20250930', revenue: 120000000000, net_profit: 61000000000, roe: 24.3 }
    ])
  },
  {
    key: 'news',
    label: '新闻数据',
    path: '/api/news-data/save',
    arrayKey: 'news_list',
    symbol: 'optional',
    overwriteDefault: false,
    hint: '每条必填：title、url(http/https)、publish_time(ISO 8601)。以 url 判重；可填写股票代码关联。',
    template: pretty([
      { title: '贵州茅台发布三季报', url: 'https://example.com/news/1', publish_time: '2026-01-05T10:30:00+08:00', source: '示例财经' }
    ])
  },
  {
    key: 'historical',
    label: '历史K线',
    path: '/api/data-import/historical',
    arrayKey: 'records',
    symbol: 'required',
    overwriteDefault: true,
    extra: [
      { key: 'market', label: '市场', options: ['CN', 'HK', 'US'], default: 'CN' },
      { key: 'period', label: '周期', options: ['daily', 'weekly', 'monthly'], default: 'daily' }
    ],
    hint: '需填写股票代码；每条必填 date 与 close（>0），open/high/low/volume/amount 可选。单次最多 5000 条，数据源标记为 user_import。',
    template: pretty([
      { date: '2026-01-05', open: 1675, high: 1692, low: 1670.5, close: 1688, volume: 21345, amount: 360000000 },
      { date: '2026-01-06', close: 1695 }
    ])
  },
  {
    key: 'social',
    label: '社媒消息',
    path: '/api/data-import/social-media',
    arrayKey: 'messages',
    symbol: 'none',
    overwriteDefault: false,
    hint: '每条必填：platform、symbol、content、publish_time（无时区按北京时间）。情绪/关键词/重要性缺省时由规则自动补全。单次最多 1000 条。',
    template: pretty([
      {
        platform: 'xueqiu',
        symbol: '600519',
        content: '贵州茅台三季报业绩超预期，看好后市 #茅台#',
        publish_time: '2026-01-05 10:30:00',
        author: { author_name: '价值投资者', followers_count: 52000, verified: true },
        engagement: { views: 12000, likes: 380, shares: 45, comments: 120 }
      }
    ])
  }
]

export interface ImportResult {
  total?: number
  saved?: number
  updated?: number
  skipped?: number
  failed?: number
  errors?: Array<{ index?: number | null; symbol?: string; error?: string; [k: string]: any }>
}

/** 提交一批数据到对应导入接口；校验类 4xx 返回后端的逐条明细，不弹全局错误 */
export async function submitImport(
  kind: ImportKind,
  items: unknown[],
  options: { overwrite: boolean; symbol?: string; extra?: Record<string, string> }
): Promise<{ ok: boolean; result: ImportResult; message: string }> {
  const body: Record<string, unknown> = { [kind.arrayKey]: items, overwrite: options.overwrite }
  if (kind.symbol !== 'none' && options.symbol) body.symbol = options.symbol.trim()
  if (kind.extra) for (const e of kind.extra) body[e.key] = options.extra?.[e.key] ?? e.default
  try {
    const res: any = await ApiClient.post(kind.path, body, { skipErrorHandler: true, showLoading: false })
    const data = res?.data ?? res ?? {}
    return { ok: true, result: data, message: res?.message || '' }
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      return { ok: false, result: detail, message: detail.message || '导入失败' }
    }
    if (Array.isArray(detail)) {
      // FastAPI 参数校验：[{loc, msg}]
      const msg = detail.map((d: any) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('；')
      return { ok: false, result: {}, message: msg || '参数校验失败' }
    }
    return { ok: false, result: {}, message: typeof detail === 'string' ? detail : e?.message || '导入失败' }
  }
}

// ==================== 社媒消息管理 ====================

export interface SocialMessage {
  message_id: string
  platform: string
  symbol: string
  content: string
  publish_time: string
  sentiment: 'positive' | 'negative' | 'neutral'
  sentiment_score?: number
  sentiment_source?: 'auto' | 'provided'
  importance?: 'high' | 'medium' | 'low'
  importance_score?: number
  keywords?: string[]
  hashtags?: string[]
  author?: { author_name?: string; followers_count?: number }
  engagement?: { views?: number; likes?: number; shares?: number; comments?: number; engagement_rate?: number }
}

export interface SocialStats {
  total: number
  by_sentiment: Record<string, number>
  by_platform: Record<string, number>
  by_importance: Record<string, number>
  top_keywords: Array<{ keyword: string; count: number }>
  latest_publish_time: string | null
}

export interface SocialQuery {
  symbol?: string
  platform?: string
  sentiment?: string
  hours_back?: number
  skip?: number
  limit?: number
}

export async function listSocialMessages(q: SocialQuery): Promise<{ items: SocialMessage[]; total: number }> {
  const res: any = await ApiClient.get('/api/data-import/social-media', q, { showLoading: false })
  return res?.data ?? { items: [], total: 0 }
}

export async function getSocialStats(symbol?: string): Promise<SocialStats> {
  const res: any = await ApiClient.get('/api/data-import/social-media/stats', symbol ? { symbol } : undefined, { showLoading: false })
  return (
    res?.data ?? { total: 0, by_sentiment: {}, by_platform: {}, by_importance: {}, top_keywords: [], latest_publish_time: null }
  )
}

export async function deleteSocialMessage(messageId: string, platform: string): Promise<void> {
  await ApiClient.delete(`/api/data-import/social-media/${encodeURIComponent(messageId)}?platform=${encodeURIComponent(platform)}`)
}

// ==================== 指南与测试程序 ====================

export type GuideKind = 'stock-data' | 'social-media'

export async function getGuideMarkdown(kind: GuideKind): Promise<string> {
  const res: any = await ApiClient.get(`/api/data-import/guide/${kind}`, undefined, { showLoading: false })
  return typeof res === 'string' ? res : String(res?.data ?? '')
}

export async function downloadTestProgram(): Promise<void> {
  await ApiClient.download('/api/data-import/test-program', 'test_batch_import_apis.py')
}
