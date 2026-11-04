/**
 * Pro 专属路由
 *
 * Pro 版逐项恢复原 Pro 页面：每新增一个页面，在对应数组里追加路由，
 * 并在 config/navigation.pro.ts 登记菜单项；router/index.ts 无需改动。
 */
import type { RouteRecordRaw } from 'vue-router'

export const proWorkflowRoutes: RouteRecordRaw[] = [
  {
    path: '/agent-workshop',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'AgentWorkshop',
        component: () => import('@/views/AgentWorkshop/index.vue'),
        meta: { title: 'Agent 工坊', requiresAuth: true }
      }
    ]
  }
]
export const proTradingSystemRoutes: RouteRecordRaw[] = [
  {
    path: '/trading-system',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'TradingSystems',
        component: () => import('@/views/TradingSystem/index.vue'),
        meta: { title: '交易计划', requiresAuth: true }
      }
    ]
  }
]
export const proAnalysisChildren: RouteRecordRaw[] = []
export const proSettingsChildren: RouteRecordRaw[] = [
  {
    path: 'templates',
    name: 'PromptTemplates',
    component: () => import('@/views/Settings/PromptTemplates.vue'),
    meta: { title: '提示词模板', requiresAuth: true }
  },
  {
    path: 'email',
    name: 'EmailSettings',
    component: () => import('@/views/Settings/EmailSettings.vue'),
    meta: { title: '邮件通知', requiresAuth: true }
  },
  {
    path: 'channel-configs',
    name: 'ChannelConfigs',
    component: () => import('@/views/Settings/ChannelConfigs.vue'),
    meta: { title: '渠道推送', requiresAuth: true }
  },
  {
    path: 'watchlist-groups',
    name: 'WatchlistGroups',
    component: () => import('@/views/Settings/WatchlistGroups.vue'),
    meta: { title: '自选股分组', requiresAuth: true }
  },
  {
    path: 'scheduled-analysis',
    name: 'ScheduledAnalysis',
    component: () => import('@/views/Settings/ScheduledAnalysis.vue'),
    meta: { title: '定时分析', requiresAuth: true }
  }
]
export const proLearningChildren: RouteRecordRaw[] = []
