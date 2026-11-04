<template>
  <el-menu
    :default-active="activeMenu"
    :collapse="appStore.sidebarCollapsed"
    :unique-opened="true"
    router
    class="sidebar-menu"
  >
    <el-menu-item index="/dashboard">
      <el-icon><Odometer /></el-icon>
      <template #title>仪表板</template>
    </el-menu-item>

    <!-- ==================== 研究分析 ==================== -->
    <el-sub-menu index="group-research">
      <template #title>
        <el-icon><TrendCharts /></el-icon>
        <span>研究分析</span>
      </template>
      <el-menu-item index="/analysis/single">单股研究</el-menu-item>
      <el-menu-item index="/analysis/batch">批量分析</el-menu-item>
      <el-menu-item index="/analysis/general">通用研究</el-menu-item>
      <el-menu-item index="/reports">研究报告</el-menu-item>
      <el-menu-item index="/tasks/unified">任务中心</el-menu-item>
      <el-menu-item v-for="item in proResearchItems" :key="item.path" :index="item.path">
        {{ item.label }}
        <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
      </el-menu-item>

      <!-- 分析流：管理与编辑器 -->
      <el-sub-menu index="group-workflow">
        <template #title>分析流</template>
        <el-menu-item v-for="child in proWorkflowMenu.children" :key="child.path" :index="child.path">
          {{ child.label }}
          <el-tag v-if="child.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
          <el-tag v-else-if="child.badge" :type="child.badge.type" size="small" class="menu-tag">{{ child.badge.text }}</el-tag>
        </el-menu-item>
      </el-sub-menu>
    </el-sub-menu>

    <!-- ==================== AI 助手 ==================== -->
    <el-sub-menu index="group-ai">
      <template #title>
        <el-icon><MagicStick /></el-icon>
        <span>AI 助手</span>
      </template>
      <el-menu-item index="/assistant">智能助手</el-menu-item>
      <el-menu-item v-for="item in proAssistantItems" :key="item.path" :index="item.path">
        {{ item.label }}
        <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
      </el-menu-item>
      <el-menu-item index="/skills">Skill 中心</el-menu-item>
      <el-menu-item index="/memory">AI 记忆</el-menu-item>
    </el-sub-menu>

    <!-- ==================== 投资管理 ==================== -->
    <el-sub-menu index="group-invest">
      <template #title>
        <el-icon><PieChart /></el-icon>
        <span>投资管理</span>
      </template>

      <el-menu-item index="/screening">股票筛选</el-menu-item>
      <el-menu-item index="/favorites">股票关注列表</el-menu-item>
      <el-menu-item v-for="item in proWatchlistItems" :key="item.path" :index="item.path">
        {{ item.label }}
        <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
      </el-menu-item>

      <el-menu-item index="/portfolio">持仓研究</el-menu-item>
      <el-menu-item index="/paper">模拟交易</el-menu-item>
      <el-menu-item index="/review">操作复盘</el-menu-item>
      <el-menu-item index="/backtest">网格回测</el-menu-item>

      <el-menu-item index="/trading">交易信号中心</el-menu-item>
      <el-menu-item index="/trading-observability">交易监控</el-menu-item>
      <el-menu-item v-for="item in proTradingItems" :key="item.path" :index="item.path">
        {{ item.label }}
        <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
      </el-menu-item>
    </el-sub-menu>

    <!-- ==================== 设置 ==================== -->
    <el-sub-menu index="/settings">
      <template #title>
        <el-icon><Setting /></el-icon>
        <span>设置</span>
      </template>

      <!-- 个人设置 -->
      <el-sub-menu index="group-settings-personal">
        <template #title>个人设置</template>
        <el-menu-item index="/settings">通用设置</el-menu-item>
        <el-menu-item index="/settings?tab=appearance">外观设置</el-menu-item>
        <el-menu-item index="/settings?tab=analysis">分析偏好</el-menu-item>
        <el-menu-item index="/settings?tab=notifications">通知设置</el-menu-item>
        <el-menu-item index="/settings?tab=security">安全设置</el-menu-item>
        <el-menu-item index="/settings?tab=assistant">助理设置</el-menu-item>

        <el-menu-item v-for="item in proPersonalItems" :key="item.path" :index="item.path">
          {{ item.label }}
          <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
        </el-menu-item>
        <el-menu-item v-if="!isJdyunMode() && proLicenseMenuItem" :index="proLicenseMenuItem.path">
          {{ proLicenseMenuItem.label }}
          <el-icon style="margin-left: 4px;"><Key /></el-icon>
        </el-menu-item>
      </el-sub-menu>

      <!-- 系统配置 -->
      <el-sub-menu index="group-settings-config">
        <template #title>系统配置</template>
        <el-menu-item v-if="isJdyunMode()" index="/settings/jdyun-config">系统配置</el-menu-item>
        <el-menu-item v-else index="/settings/config">配置管理</el-menu-item>
        <el-menu-item index="/settings/cache">缓存管理</el-menu-item>
        <el-menu-item index="/settings/sync">多数据源同步</el-menu-item>
        <el-menu-item index="/settings/capability-index">工具能力索引</el-menu-item>
      </el-sub-menu>

      <!-- 数据管理 -->
      <el-sub-menu index="group-settings-data">
        <template #title>数据管理</template>
        <el-menu-item index="/settings/data-import">
          数据导入管理
          <el-tag v-if="isAdvancedMenuItem('/settings/data-import')" type="success" size="small" class="menu-tag">高级</el-tag>
        </el-menu-item>
        <el-menu-item index="/settings/scheduler">定时任务</el-menu-item>
      </el-sub-menu>

      <!-- 分析配置 -->
      <el-sub-menu index="group-settings-analysis">
        <template #title>分析配置</template>
        <el-menu-item index="/settings/analysis-profiles">
          行业/个股配置
          <el-tag type="info" size="small" class="menu-tag">智能</el-tag>
        </el-menu-item>
        <el-menu-item v-for="item in proAnalysisItems" :key="item.path" :index="item.path">
          {{ item.label }}
          <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
        </el-menu-item>
      </el-sub-menu>

      <!-- 系统管理 -->
      <el-sub-menu index="group-settings-admin">
        <template #title>系统管理</template>
        <el-menu-item index="/settings/database">数据库管理</el-menu-item>
        <el-menu-item index="/settings/logs">操作日志</el-menu-item>
        <el-menu-item index="/settings/system-logs">系统日志</el-menu-item>
        <el-menu-item v-for="item in proAdminItems" :key="item.path" :index="item.path">
          {{ item.label }}
          <el-tag v-if="item.advanced" type="success" size="small" class="menu-tag">高级</el-tag>
        </el-menu-item>
        <el-menu-item index="/settings/usage">使用统计</el-menu-item>
        <el-menu-item index="/settings/update">系统更新</el-menu-item>
      </el-sub-menu>
    </el-sub-menu>
  </el-menu>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { isJdyunMode } from '@/utils/config'
// Pro 专属菜单项集中配置于 navigation.pro.ts：社区版发布时整文件替换为空配置，
// Pro 菜单项自动不渲染（各分组按槽位取用，保证排版顺序稳定）
import {
  proResearchItems,
  proAssistantItems,
  proWatchlistItems,
  proTradingItems,
  proWorkflowMenu,
  proPersonalItems,
  proLicenseMenuItem,
  proAnalysisItems,
  proAdminItems
} from '@/config/navigation.pro'
import {
  Odometer,
  TrendCharts,
  Setting,
  PieChart,
  Key,
  MagicStick
} from '@element-plus/icons-vue'

const route = useRoute()
const appStore = useAppStore()

/** 需要打“高级”标记的固定菜单项（所有模式统一显示，功能不收费） */
const advancedMenuItems = new Set(['/settings/data-import'])
const isAdvancedMenuItem = (index: string) => advancedMenuItems.has(index)

/** 高亮当前项：/settings 的 Tab 页靠 query 区分，其余按 path */
const activeMenu = computed(() => {
  if (route.path === '/settings' && route.query.tab) return `/settings?tab=${route.query.tab}`
  // 分析流编辑器等子路由回落到父菜单项
  if (route.path.startsWith('/workflow')) return route.path
  return route.path
})
</script>

<style lang="scss" scoped>
.sidebar-menu {
  border: none;
  height: 100%;

  :deep(.el-menu-item),
  :deep(.el-sub-menu__title) {
    height: 44px;
    line-height: 44px;
  }

  // 子菜单项缩进更紧凑，避免三级嵌套后文字被挤
  :deep(.el-menu .el-menu-item) {
    min-width: 0;
  }

  :deep(.el-menu-item.is-active) {
    background-color: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
  }
}

.menu-tag {
  margin-left: 4px;
  transform: scale(0.85);
}
</style>
