<template>
  <el-card class="settlement-status-card" shadow="never">
    <template #header>
      <div class="card-header">
        <span class="header-title">
          <el-icon><TrendCharts /></el-icon>
          决策结算状态
        </span>
        <span class="header-sub" v-if="status">
          持有窗口 {{ status.holding_period_days }} 个交易日
        </span>
      </div>
    </template>

    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="2" animated />
    </div>

    <template v-else-if="status">
      <!-- KPI 行 -->
      <div class="kpi-row" v-if="status.total > 0">
        <div class="kpi-item">
          <div class="kpi-value">{{ status.total }}</div>
          <div class="kpi-label">累计决策</div>
        </div>
        <div class="kpi-item">
          <div class="kpi-value pending-color">{{ status.pending }}</div>
          <div class="kpi-label">待结算</div>
        </div>
        <div class="kpi-item">
          <div class="kpi-value">{{ status.resolved }}</div>
          <div class="kpi-label">已结算</div>
        </div>
        <div class="kpi-item" v-if="status.review > 0">
          <div class="kpi-value review-color">{{ status.review }}</div>
          <div class="kpi-label">待复核</div>
        </div>
      </div>
      <el-empty
        v-else
        description="暂无决策记录 — 跑一次分析后，结论会自动进入结算队列"
        :image-size="60"
      />

      <!-- 按倾向统计表 -->
      <el-table
        v-if="ratingRows.length"
        :data="ratingRows"
        size="small"
        style="margin-top: 12px;"
      >
        <el-table-column label="结论倾向" width="100">
          <template #default="{ row }">
            <el-tag :type="row.type" size="small">{{ row.rating }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="样本数" prop="count" width="90" />
        <el-table-column label="方向命中率" width="130">
          <template #default="{ row }">
            <span v-if="row.hit_rate !== null">{{ (row.hit_rate * 100).toFixed(1) }}%</span>
            <span v-else style="color: #c0c4cc;">—</span>
          </template>
        </el-table-column>
        <el-table-column label="平均 alpha">
          <template #default="{ row }">
            <span :class="row.mean_alpha >= 0 ? 'alpha-pos' : 'alpha-neg'">
              {{ row.mean_alpha >= 0 ? '+' : '' }}{{ (row.mean_alpha * 100).toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 待结算分布 -->
      <div v-if="pendingTickerEntries.length" class="pending-dist">
        <span class="dist-label">待结算分布：</span>
        <el-tag
          v-for="p in pendingTickerEntries"
          :key="p.ticker"
          size="small"
          type="info"
          style="margin-right: 6px;"
        >{{ p.ticker }} ×{{ p.count }}</el-tag>
      </div>

      <div class="action-row">
        <el-button
          size="small"
          type="primary"
          plain
          :loading="settling"
          :disabled="status.pending === 0"
          @click="handleSettle"
        >
          立即结算{{ status.pending > 0 ? `（${status.pending} 笔到期待结算）` : '' }}
        </el-button>
        <span class="action-tip">每交易日收盘后自动结算</span>
      </div>
    </template>

    <div v-else class="error-state">
      <el-text type="info" size="small">结算状态加载失败</el-text>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { TrendCharts } from '@element-plus/icons-vue'
import decisionSettlementApi from '@/api/decisionSettlement'
import type { SettlementStatus } from '@/api/decisionSettlement'

const status = ref<SettlementStatus | null>(null)
const loading = ref(true)
const settling = ref(false)

interface RatingRow {
  rating: string
  type: 'success' | 'warning' | 'info'
  count: number
  hit_rate: number | null
  mean_alpha: number
}

const ratingTypeMap: Record<string, 'success' | 'warning' | 'info'> = {
  乐观: 'success',
  审慎: 'warning'
}

const ratingRows = computed<RatingRow[]>(() => {
  const by = status.value?.by_rating ?? {}
  return Object.entries(by).map(([rating, bucket]) => ({
    rating,
    type: ratingTypeMap[rating] ?? 'info',
    count: bucket.count,
    hit_rate: typeof bucket.hit_rate === 'number' ? bucket.hit_rate : null,
    mean_alpha: bucket.mean_alpha ?? 0
  }))
})

const pendingTickerEntries = computed(() => {
  const by = status.value?.pending_by_ticker ?? {}
  return Object.entries(by)
    .map(([ticker, count]) => ({ ticker, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8)
})

const loadStatus = async () => {
  loading.value = true
  try {
    const res = await decisionSettlementApi.getSettlementStatus()
    if (res.success) status.value = res.data
  } catch {
    status.value = null
  } finally {
    loading.value = false
  }
}

const handleSettle = async () => {
  settling.value = true
  try {
    const res = await decisionSettlementApi.triggerSettlement()
    if (res.success) {
      const { settled, failed, failures } = res.data
      if (settled > 0 && failed === 0) {
        ElMessage.success(`结算完成：${settled} 笔`)
      } else if (failed > 0) {
        ElMessage.warning(`结算 ${settled} 笔成功、${failed} 笔失败`)
        if (failures?.length) {
          console.warn('[决策结算] 失败明细:', failures)
        }
      } else {
        ElMessage.info('本次没有到期可结算的决策')
      }
      await loadStatus()
    }
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '结算失败')
  } finally {
    settling.value = false
  }
}

onMounted(loadStatus)

defineExpose({ reload: loadStatus })
</script>

<style lang="scss" scoped>
.settlement-status-card {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;

    .header-title {
      display: flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
    }

    .header-sub {
      font-size: 12px;
      color: #909399;
    }
  }

  .kpi-row {
    display: flex;
    gap: 32px;
    padding: 4px 8px;

    .kpi-item {
      text-align: center;

      .kpi-value {
        font-size: 24px;
        font-weight: 700;

        &.pending-color { color: #e6a23c; }
        &.review-color { color: #9b59b6; }
      }

      .kpi-label {
        font-size: 12px;
        color: #909399;
        margin-top: 2px;
      }
    }
  }

  .alpha-pos { color: #67c23a; }
  .alpha-neg { color: #f56c6c; }

  .pending-dist {
    margin-top: 10px;
    font-size: 12px;

    .dist-label { color: #909399; }
  }

  .action-row {
    margin-top: 12px;
    display: flex;
    align-items: center;
    gap: 12px;

    .action-tip {
      font-size: 12px;
      color: #c0c4cc;
    }
  }
}
</style>
