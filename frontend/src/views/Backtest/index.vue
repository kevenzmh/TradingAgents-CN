<template>
  <div class="backtest-page">
    <el-card class="form-card">
      <template #header>
        <div class="card-header">
          <span>网格回测</span>
          <el-tooltip content="在股票×日期网格上重放分析引擎，持有窗口走出后按决策倾向结算命中率与 alpha。每格约 6-8 分钟（本地模型）">
            <el-icon><InfoFilled /></el-icon>
          </el-tooltip>
        </div>
      </template>

      <el-form :model="form" label-width="110px" inline>
        <el-form-item label="股票代码">
          <el-input
            v-model="tickersInput"
            placeholder="如 600519, 000858（逗号分隔，最多 3 只）"
            style="width: 280px"
          />
        </el-form-item>
        <el-form-item label="网格起点">
          <el-date-picker
            v-model="form.start_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="回放起始日期"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="网格终点">
          <el-date-picker
            v-model="form.end_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="回放结束日期"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="步长(天)">
          <el-input-number v-model="form.every_n_days" :min="1" :max="30" style="width: 110px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="starting" @click="startBacktest">
            启动回测
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="gridPreview > 0" class="grid-preview">
        预计 {{ gridPreview }} 个格子 · 约 {{ estimatedHours }} 小时（本地模型 6-8 分钟/格）
      </div>
    </el-card>

    <el-card v-if="currentRun" class="progress-card">
      <template #header>
        <div class="card-header">
          <span>进行中：{{ currentRun.run_id }}</span>
          <el-tag :type="statusTagType(currentRun.status)" size="small">{{ statusText(currentRun.status) }}</el-tag>
        </div>
      </template>

      <el-progress
        :percentage="progressPercent"
        :status="currentRun.status === 'completed' ? 'success' : undefined"
      />
      <div class="progress-meta">
        第 {{ currentRun.cells_done }} / {{ currentRun.total_cells }} 格
        <template v-if="currentRun.current_cell">· 当前：{{ currentRun.current_cell }}</template>
      </div>
      <div v-if="currentRun.status === 'failed'" class="error-box">{{ currentRun.error }}</div>
      <div v-if="currentRun.failures.length" class="warn-box">
        失败格子 {{ currentRun.failures.length }} 个：
        <span v-for="(f, i) in currentRun.failures.slice(0, 5)" :key="i" class="fail-item">
          {{ f.ticker }}@{{ f.date }}
        </span>
      </div>
    </el-card>

    <el-card v-if="summary && currentRun?.status === 'completed'" class="summary-card">
      <template #header><span>回测结果</span></template>
      <pre class="summary-text">{{ summary.summary_text }}</pre>

      <el-table v-if="summary.summary_data" :data="ratingRows" size="small" border>
        <el-table-column prop="rating" label="结论倾向" width="100" />
        <el-table-column prop="count" label="样本数" width="90" />
        <el-table-column label="方向命中率" width="110">
          <template #default="{ row }">
            <span v-if="row.hit_rate === null || row.hit_rate === undefined">无方向声明</span>
            <span v-else :class="hitRateClass(row.hit_rate)">{{ (row.hit_rate * 100).toFixed(0) }}%</span>
          </template>
        </el-table-column>
        <el-table-column label="平均 alpha（相对基准）">
          <template #default="{ row }">
            <span :class="alphaClass(row.mean_alpha)">{{ (row.mean_alpha * 100).toFixed(2) }}%</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="history-card">
      <template #header><span>历史回测</span></template>
      <el-table :data="runs" size="small" border>
        <el-table-column prop="run_id" label="Run" width="160" />
        <el-table-column label="标的" width="180">
          <template #default="{ row }">{{ (row.tickers || []).join(', ') }}</template>
        </el-table-column>
        <el-table-column label=" 格子" width="120">
          <template #default="{ row }">{{ row.cells_done }} / {{ row.total_cells }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="viewRun(row.run_id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import backtestApi from '@/api/backtest'
import type { BacktestRunStatus } from '@/api/backtest'

const tickersInput = ref('600519, 000858')
const form = ref({
  start_date: '',
  end_date: '',
  every_n_days: 5
})

const starting = ref(false)
const currentRun = ref<BacktestRunStatus | null>(null)
const summary = ref<{ summary_text: string; summary_data: any } | null>(null)
const runs = ref<any[]>([])
let pollTimer: number | null = null

const parsedTickers = computed(() =>
  tickersInput.value
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter((t) => /^\d{6}$/.test(t))
)

const gridPreview = computed(() => {
  if (!form.value.start_date || !form.value.end_date) return 0
  const days = Math.max(
    0,
    Math.floor(
      (new Date(form.value.end_date).getTime() - new Date(form.value.start_date).getTime()) / 86400000
    ) + 1
  )
  const points = Math.max(0, Math.ceil(days / form.value.every_n_days))
  return points * parsedTickers.value.length
})

const estimatedHours = computed(() => ((gridPreview.value * 7) / 60).toFixed(1))

const progressPercent = computed(() => {
  if (!currentRun.value || !currentRun.value.total_cells) return 0
  return Math.round((currentRun.value.cells_done / currentRun.value.total_cells) * 100)
})

const ratingRows = computed(() => {
  const by = summary.value?.summary_data?.by_rating || {}
  return Object.entries(by).map(([rating, s]: [string, any]) => ({
    rating,
    count: s.count,
    hit_rate: s.hit_rate,
    mean_alpha: s.mean_alpha
  }))
})

function statusText(s: string) {
  return ({ pending: '排队中', running: '运行中', settling: '结算中', completed: '已完成', failed: '失败' } as Record<string, string>)[s] || s
}
function statusTagType(s: string): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ({ completed: 'success', failed: 'danger', running: 'primary', settling: 'warning' } as Record<string, 'primary' | 'success' | 'warning' | 'info' | 'danger'>)[s] || 'info'
}
function hitRateClass(r: number) {
  return r >= 0.5 ? 'positive' : 'negative'
}
function alphaClass(a: number) {
  return a > 0 ? 'positive' : 'negative'
}
function formatTime(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

async function startBacktest() {
  if (!parsedTickers.value.length) {
    ElMessage.warning('请输入至少一个 6 位 A 股代码')
    return
  }
  if (!form.value.start_date || !form.value.end_date) {
    ElMessage.warning('请选择网格起止日期')
    return
  }
  starting.value = true
  try {
    const res: any = await backtestApi.start({
      tickers: parsedTickers.value,
      start_date: form.value.start_date,
      end_date: form.value.end_date,
      every_n_days: form.value.every_n_days
    })
    ElMessage.success(`回测已启动（run=${(res.data as any).run_id}）`)
    currentRun.value = (await backtestApi.getStatus((res.data as any).run_id)).data ?? null
    startPolling()
    await loadRuns()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '启动失败')
  } finally {
    starting.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    const run = currentRun.value
    if (!run) return stopPolling()
    try {
      currentRun.value = (await backtestApi.getStatus(run.run_id)).data ?? null
      const status = currentRun.value?.status
      if (status && ['completed', 'failed'].includes(status)) {
        stopPolling()
        if (status === 'completed') {
          summary.value = (await backtestApi.getSummary(currentRun.value!.run_id)).data ?? null
          ElMessage.success('回测完成')
        }
        await loadRuns()
      }
    } catch {
      /* 单次轮询失败忽略 */
    }
  }, 10000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadRuns() {
  try {
    runs.value = (await backtestApi.listRuns(20)).data ?? []
  } catch {
    /* 忽略 */
  }
}

async function viewRun(runId: string) {
  try {
    const status = (await backtestApi.getStatus(runId)).data ?? null
    currentRun.value = status
    if (status.status === 'completed') {
      summary.value = (await backtestApi.getSummary(runId)).data ?? null
    } else if (['running', 'settling', 'pending'].includes(status.status)) {
      startPolling()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '查询失败')
  }
}

onMounted(() => {
  loadRuns()
  // 恢复中断的回测轮询
  const active = runs.value.find((r) => ['pending', 'running', 'settling'].includes(r.status))
  if (active) {
    viewRun(active.run_id)
  }
})

onUnmounted(stopPolling)
</script>

<style scoped lang="scss">
.backtest-page {
  padding: 16px;

  .card-header {
    display: flex;
    align-items: center;
    gap: 6px;
    justify-content: space-between;
  }

  .grid-preview {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-top: 4px;
  }

  .progress-card .progress-meta {
    margin-top: 8px;
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }

  .summary-text {
    background: var(--el-fill-color-light);
    padding: 12px;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.7;
    white-space: pre-wrap;
    margin: 0 0 12px 0;
  }

  .error-box {
    margin-top: 8px;
    color: var(--el-color-danger);
    font-size: 12px;
    white-space: pre-wrap;
  }

  .warn-box {
    margin-top: 8px;
    color: var(--el-color-warning);
    font-size: 12px;

    .fail-item {
      margin-right: 10px;
    }
  }

  .positive {
    color: var(--el-color-success);
  }

  .negative {
    color: var(--el-color-danger);
  }

  .history-card,
  .summary-card,
  .progress-card {
    margin-top: 16px;
  }
}
</style>
