/**
 * Pro 专属路由
 *
 * Pro 版逐项恢复原 Pro 页面：每新增一个页面，在对应数组里追加路由，
 * 并在 config/navigation.pro.ts 登记菜单项；router/index.ts 无需改动。
 */
import type { RouteRecordRaw } from 'vue-router'

export const proWorkflowRoutes: RouteRecordRaw[] = []
export const proTradingSystemRoutes: RouteRecordRaw[] = []
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
  }
]
export const proLearningChildren: RouteRecordRaw[] = []
