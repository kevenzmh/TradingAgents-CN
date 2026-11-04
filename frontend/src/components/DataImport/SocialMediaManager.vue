<template>
  <el-card class="manager-card" shadow="never">
    <template #header>
      <div class="card-header">
        <span>
          <el-icon><ChatDotRound /></el-icon>
          社媒消息管理
        </span>
        <el-button size="small" :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </template>

    <div class="stats" v-loading="statsLoading">
      <div class="stat">
        <div class="stat-value">{{ stats.total }}</div>
        <div class="stat-label">消息总数</div>
      </div>
      <div class="stat">
        <div class="stat-value positive">{{ stats.by_sentiment.positive || 0 }}</div>
        <div class="stat-label">正面</div>
      </div>
      <div class="stat">
        <div class="stat-value negative">{{ stats.by_sentiment.negative || 0 }}</div>
        <div class="stat-label">负面</div>
      </div>
      <div class="stat">
        <div class="stat-value">{{ stats.by_sentiment.neutral || 0 }}</div>
        <div class="stat-label">中性</div>
      </div>
      <div class="stat wide">
        <div class="stat-label">平台分布</div>
        <div class="tags">
          <el-tag v-for="(n, p) in stats.by_platform" :key="p" size="small" effect="plain">{{ p }} · {{ n }}</el-tag>
          <span v-if="!Object.keys(stats.by_platform).length" class="muted">暂无</span>
        </div>
      </div>
      <div class="stat wide">
        <div class="stat-label">热门关键词</div>
        <div class="tags">
          <el-tag v-for="k in stats.top_keywords.slice(0, 10)" :key="k.keyword" size="small" type="info">
            {{ k.keyword }} · {{ k.count }}
          </el-tag>
          <span v-if="!stats.top_keywords.length" class="muted">暂无</span>
        </div>
      </div>
    </div>

    <el-form :inline="true" class="filters" @submit.prevent="search">
      <el-form-item label="股票代码">
        <el-input v-model="filters.symbol" placeholder="如 600519" clearable style="width: 130px" @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="平台">
        <el-input v-model="filters.platform" placeholder="如 xueqiu" clearable style="width: 130px" @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="情绪">
        <el-select v-model="filters.sentiment" clearable placeholder="全部" style="width: 110px">
          <el-option label="正面" value="positive" />
          <el-option label="中性" value="neutral" />
          <el-option label="负面" value="negative" />
        </el-select>
      </el-form-item>
      <el-form-item label="时间">
        <el-select v-model="filters.hours_back" clearable placeholder="全部" style="width: 120px">
          <el-option label="近 24 小时" :value="24" />
          <el-option label="近 7 天" :value="24 * 7" />
          <el-option label="近 30 天" :value="24 * 30" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="items" v-loading="loading" size="small" stripe empty-text="暂无社媒消息，点击上方“在线导入”添加">
      <el-table-column label="发布时间" width="160">
        <template #default="{ row }">{{ formatTime(row.publish_time) }}</template>
      </el-table-column>
      <el-table-column prop="symbol" label="代码" width="80" />
      <el-table-column prop="platform" label="平台" width="90" />
      <el-table-column label="内容" min-width="260">
        <template #default="{ row }">
          <el-tooltip :content="row.content" placement="top" :show-after="400" popper-class="msg-tooltip">
            <span class="content-cell">{{ row.content }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="情绪" width="90">
        <template #default="{ row }">
          <el-tag :type="sentimentType(row.sentiment)" size="small">{{ sentimentLabel(row.sentiment) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="重要性" width="80">
        <template #default="{ row }">{{ importanceLabel(row.importance) }}</template>
      </el-table-column>
      <el-table-column label="作者" width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ row.author?.author_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="互动" width="130">
        <template #default="{ row }">
          <span class="muted">赞 {{ row.engagement?.likes ?? 0 }} · 评 {{ row.engagement?.comments ?? 0 }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-popconfirm title="确认删除这条消息？" @confirm="remove(row)">
            <template #reference>
              <el-button link type="danger" size="small">删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      class="pager"
      layout="total, sizes, prev, pager, next"
      :page-sizes="[20, 50, 100]"
      :total="total"
      @current-change="load"
      @size-change="onSizeChange"
    />
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Refresh, Search } from '@element-plus/icons-vue'

import {
  deleteSocialMessage,
  getSocialStats,
  listSocialMessages,
  type SocialMessage,
  type SocialStats
} from '@/api/dataImport'

const emptyStats = (): SocialStats => ({
  total: 0, by_sentiment: {}, by_platform: {}, by_importance: {}, top_keywords: [], latest_publish_time: null
})

const items = ref<SocialMessage[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const statsLoading = ref(false)
const stats = ref<SocialStats>(emptyStats())
const filters = reactive<{ symbol: string; platform: string; sentiment: string; hours_back?: number }>({
  symbol: '', platform: '', sentiment: '', hours_back: undefined
})

function formatTime(iso: string) {
  if (!iso) return '—'
  // 后端以 UTC naive 存储，接口按 ISO 返回；无时区后缀时按 UTC 解析再转本地显示
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const sentimentLabel = (s: string) => ({ positive: '正面', negative: '负面', neutral: '中性' } as Record<string, string>)[s] || s
const sentimentType = (s: string) =>
  ({ positive: 'success', negative: 'danger', neutral: 'info' } as Record<string, 'success' | 'danger' | 'info'>)[s] || 'info'
const importanceLabel = (v?: string) => ({ high: '高', medium: '中', low: '低' } as Record<string, string>)[v || ''] || '—'

async function load() {
  loading.value = true
  try {
    const res = await listSocialMessages({
      symbol: filters.symbol.trim() || undefined,
      platform: filters.platform.trim() || undefined,
      sentiment: filters.sentiment || undefined,
      hours_back: filters.hours_back || undefined,
      skip: (page.value - 1) * pageSize.value,
      limit: pageSize.value
    })
    items.value = res.items
    total.value = res.total
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await getSocialStats(filters.symbol.trim() || undefined)
  } finally {
    statsLoading.value = false
  }
}

function onSizeChange() {
  page.value = 1
  load()
}

function search() {
  page.value = 1
  load()
  loadStats()
}

function resetFilters() {
  filters.symbol = filters.platform = filters.sentiment = ''
  filters.hours_back = undefined
  search()
}

async function reload() {
  await Promise.all([load(), loadStats()])
}

async function remove(row: SocialMessage) {
  try {
    await deleteSocialMessage(row.message_id, row.platform)
    ElMessage.success('已删除')
    // 删除最后一页的唯一一条后回退一页
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await reload()
  } catch {
    /* 全局拦截器已提示错误 */
  }
}

defineExpose({ reload })
onMounted(reload)
</script>

<style scoped lang="scss">
.manager-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 16px;

  span {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
  margin-bottom: 16px;
  padding: 12px 16px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);

  .stat {
    min-width: 72px;

    &.wide {
      flex: 1 1 260px;
    }
  }

  .stat-value {
    font-size: 22px;
    font-weight: 600;
    line-height: 1.2;

    &.positive {
      color: var(--el-color-success);
    }

    &.negative {
      color: var(--el-color-danger);
    }
  }

  .stat-label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-bottom: 4px;
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.content-cell {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
}

.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>

<style>
.msg-tooltip {
  max-width: 480px;
}
</style>
