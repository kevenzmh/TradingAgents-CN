/**
 * Pro 专属菜单配置
 *
 * 侧栏按功能域分组后，Pro 菜单项按「槽位」插入各分组，保证与免费项的排版顺序稳定。
 * 社区版发布时本文件整体替换为空配置，SidebarMenu 无需改动：
 * 所有槽位导出为空数组 / null 时，对应菜单项自动不渲染。
 */

export interface ProMenuItem {
  path: string
  label: string
  advanced?: boolean
  badge?: { text: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' }
}

export interface ProMenuGroup {
  path: string
  label: string
  advanced?: boolean
  children: ProMenuItem[]
}

// ==================== 研究分析 ====================

/** 研究分析分组：通用研究之后的 Pro 项 */
export const proResearchItems: ProMenuItem[] = []

/** 分析流子菜单（管理 / 编辑器） */
export const proWorkflowMenu: ProMenuGroup = {
  path: '/workflow',
  label: '分析流',
  children: [
    { path: '/workflow', label: '分析流管理' },
    { path: '/workflow/editor', label: '流程编辑器' },
  ]
}

// ==================== AI 助手 ====================

/** AI 助手分组：智能助手之后的 Pro 项 */
export const proAssistantItems: ProMenuItem[] = [
  { path: '/agent-workshop', label: 'Agent 工坊' }
]

// ==================== 投资管理 ====================

/** 投资管理·自选相关：股票关注列表之后的 Pro 项 */
export const proWatchlistItems: ProMenuItem[] = [
  { path: '/settings/watchlist-groups', label: '自选股分组' },
  { path: '/settings/scheduled-analysis', label: '定时分析' }
]

/** 投资管理·交易相关：交易监控之后的 Pro 项 */
export const proTradingItems: ProMenuItem[] = [
  { path: '/trading-system', label: '交易计划' }
]

// ==================== 设置 ====================

/** 个人设置中的 Pro 项 */
export const proPersonalItems: ProMenuItem[] = [
  { path: '/settings/email', label: '邮件通知' },
  { path: '/settings/channel-configs', label: '渠道推送' }
]

/** 授权管理菜单项 */
export const proLicenseMenuItem: ProMenuItem | null = null

/** 分析配置中的 Pro 项 */
export const proAnalysisItems: ProMenuItem[] = [
  { path: '/settings/templates', label: '提示词模板' }
]

/** 系统管理中的 Pro 项 */
export const proAdminItems: ProMenuItem[] = []
