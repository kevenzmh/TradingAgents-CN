/**
 * 授权信息 Store（Pro 版）
 *
 * Pro 版内置授权：不访问授权服务器。保持与原 stores/license.ts 一致的公开接口，
 * 消费方（Dashboard/UserProfile/featureAccess/Learning/DataImport/TradeReview）零改动。
 *
 * 行为语义：
 * - 不发起任何网络请求（无 /api/license/status 探活）
 * - isPro 恒为 true，plan 恒为 'pro'
 * - hasFeature 放行全部 Pro 功能（与后端 core/licensing/community_stub.py 对齐）
 * - hasLegacyCourseAccess 恒为 true：进阶课程全部课时可读
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 授权信息接口（与 Pro 版保持一致）
export interface LicenseInfo {
  email: string
  plan: 'free' | 'trial' | 'pro' | 'enterprise'
  features: string[]
  auth_server?: string
  auth_server_options?: Array<{ value: string; label: string }>
  device_registered: boolean
  is_valid: boolean
  error_message?: string
  verified_at?: string
  trial_end_at?: string
  pro_expire_at?: string
  offline_mode?: boolean
}

// 高级学员功能列表（与 Pro 版保持一致，仅作为类型与枚举来源）
export const PRO_FEATURES = [
  'advanced_courses',
  'email_notification',
  'watchlist_groups',
  'scheduled_analysis',
  'portfolio_analysis',
  'trade_review',
  'batch_analysis',
  'export_reports',
  'trading_plan_check',
  'workflow',
  'workflow_tools',
  'workflow_agents',
  'prompt_templates',
] as const

export type ProFeature = typeof PRO_FEATURES[number]

// Pro 版放行的功能集（后端 require_feature/require_pro 均为放行透传，前端同步放行）
// 与 core/licensing/community_stub.py FREE_ACCESS_FEATURES / PRO_FEATURES 对齐
const FREE_ACCESS_FEATURES = new Set<ProFeature>([
  'portfolio_analysis',
  'trade_review',
  'advanced_courses',
  'email_notification',
  'watchlist_groups',
  'scheduled_analysis',
  'batch_analysis',
  'export_reports',
  'trading_plan_check',
  'workflow',
  'workflow_tools',
  'workflow_agents',
  'prompt_templates'
])

// Pro 版固定授权信息（内置，无到期）
const COMMUNITY_LICENSE_INFO: LicenseInfo = {
  email: '',
  plan: 'pro',
  features: [
    'portfolio_analysis',
    'trade_review',
    'advanced_courses',
    'email_notification',
    'watchlist_groups',
    'scheduled_analysis',
    'batch_analysis',
    'export_reports',
    'trading_plan_check',
    'workflow',
    'workflow_tools',
    'workflow_agents',
    'prompt_templates'
  ],
  device_registered: false,
  is_valid: true,
  offline_mode: false
}

export const useLicenseStore = defineStore('license', () => {
  // 状态（保留接口兼容；licenseInfo 固定为 Pro 档，不来自网络）
  const appToken = ref<string | null>(localStorage.getItem('app-token'))
  const licenseInfo = ref<LicenseInfo | null>({ ...COMMUNITY_LICENSE_INFO })
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastVerifiedAt = ref<Date | null>(null)

  // 计算属性：Pro 版恒定值
  const isPro = computed(() => true)
  const isEnterprise = computed(() => false)
  const isTrial = computed(() => false)
  // 类型保持完整联合类型，运行时值恒为 'pro'
  const plan = computed((): LicenseInfo['plan'] => 'pro')

  const expireAt = computed(() => null)
  const daysRemaining = computed(() => null)
  const isExpiringSoon = computed(() => false)
  const isExpired = computed(() => false)
  const isOffline = computed(() => false)
  const hasLegacyCourseAccess = computed(() => true)

  const hasFeature = (_feature: ProFeature) => {
    return FREE_ACCESS_FEATURES.has(_feature)
  }

  // Actions：本地空操作，不发网络请求
  const setAppToken = async (token: string) => {
    appToken.value = token
    localStorage.setItem('app-token', token)
  }

  const clearAppToken = () => {
    appToken.value = null
    licenseInfo.value = { ...COMMUNITY_LICENSE_INFO }
    localStorage.removeItem('app-token')
  }

  const verifyLicense = async (_force = false, _authServer?: string): Promise<boolean> => {
    // Pro 版内置授权：直接返回有效状态，不发起请求
    lastVerifiedAt.value = new Date()
    return true
  }

  // 社区版无需初始化验证（不发请求）

  return {
    // State
    appToken,
    licenseInfo,
    loading,
    error,
    // Getters
    isPro,
    isEnterprise,
    isTrial,
    plan,
    expireAt,
    daysRemaining,
    isExpiringSoon,
    isExpired,
    isOffline,
    hasLegacyCourseAccess,
    hasFeature,
    // Actions
    setAppToken,
    clearAppToken,
    verifyLicense
  }
})
