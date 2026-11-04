<template>
  <div class="trading-observability">
    <div class="header">
      <div class="title">
        <el-icon style="margin-right: 8px"><Monitor /></el-icon>
        <span>交易监控</span>
      </div>
      <div class="actions">
        <el-button :icon="Refresh" text size="small" :loading="loading" @click="refreshAll">
          刷新
        </el-button>
        <el-button size="small" :icon="Bell" @click="handlePatrol" :loading="patrolling">
          立即巡检
        </el-button>
      </div>
    </div>

    <!-- 顶部状态概览 -->
    <el-row :gutter="12" class="stat-row">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :class="card.cls">{{ card.value }}</div>
          <div class="stat-hint">{{ card.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 告警区 -->
    <el-card shadow="never" class="section">
      <template #header>
        <div class="section-header">
          <span class="section-title">
            交易告警
            <el-badge
              v-if="alertStats.open > 0"
              :value="alertStats.open"
              type="danger"
              style="margin-left: 8px;"
            />
          </span>
          <div class="section-actions">
            <el-radio-group v-model="alertFilter" size="small" @change="loadAlerts">
              <el-radio-button label="open">未处理</el-radio-button>
              <el-radio-button label="resolved">已处理</el-radio-button>
              <el-radio-button label="all">全部</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <el-empty v-if="!alerts.length" description="暂无告警 —— 系统运行正常" :image-size="80" />
      <el-table v-else :data="alerts" size="small" style="width: 100%">
        <el-table-column label="类型" width="200">
          <template #default="{ row }">
            <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">
              {{ humanizeType(row.alert_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标的" width="110">
          <template #default="{ row }">
            <span v-if="row.symbol">{{ row.symbol }}</span>
            <span v-else class="text-gray">—</span>
          </template>
        </el-table-column>
        <el-table-column label="方向/数量" width="110">
          <template #default="{ row }">
            <span v-if="row.side">
              <el-tag size="small" :type="row.side === 'buy' ? 'success' : 'info'">
                {{ row.side === 'buy' ? '买入' : '卖出' }}
              </el-tag>
              ×{{ row.quantity ?? '—' }}
            </span>
            <span v-else class="text-gray">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            <span class="text-gray">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.resolved"
              size="small"
              type="primary"
              text
              @click="handleResolve(row)"
            >确认处理</el-button>
            <span v-else class="text-gray">已处理</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 指标区 -->
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card shadow="never" class="section">
          <template #header>
            <span class="section-title">订单与风控</span>
          </template>
          <el-empty
            v-if="!orderMetrics.length && !rejectMetrics.length"
            description="暂无数据（尚无交易发生）"
            :image-size="60"
          />
          <template v-else>
            <div v-if="orderMetrics.length" class="metric-block">
              <div class="metric-title">订单统计</div>
              <el-table :data="orderMetrics" size="small">
                <el-table-column label="市场" width="80">
                  <template #default="{ row }">{{ row.labels.market || '—' }}</template>
                </el-table-column>
                <el-table-column label="方向" width="80">
                  <template #default="{ row }">
                    {{ row.labels.side === 'buy' ? '买入' : row.labels.side === 'sell' ? '卖出' : '—' }}
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag size="small" :type="statusTagType(row.labels.status)">
                      {{ humanizeStatus(row.labels.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="数量" align="right">
                  <template #default="{ row }">{{ row.value }}</template>
                </el-table-column>
              </el-table>
            </div>

            <div v-if="rejectMetrics.length" class="metric-block">
              <div class="metric-title">风控拒绝（按闸门）</div>
              <div v-for="m in rejectMetrics" :key="m.labels.gate" class="reject-row">
                <span class="reject-name">{{ humanizeGate(m.labels.gate) }}</span>
                <el-progress
                  :percentage="rejectPercent(m.value)"
                  :show-text="false"
                  :stroke-width="8"
                  status="exception"
                  style="flex: 1; margin: 0 12px;"
                />
                <span class="reject-count">{{ m.value }}</span>
              </div>
            </div>
          </template>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never" class="section">
          <template #header>
            <span class="section-title">结算与对账</span>
          </template>
          <el-empty
            v-if="!settleMetrics.length && !reconcileMetrics.length && !latencyMetrics.length"
            description="暂无数据"
            :image-size="60"
          />
          <template v-else>
            <div v-if="settleMetrics.length" class="metric-block">
              <div class="metric-title">结算结果</div>
              <div class="chip-row">
                <el-tag
                  v-for="m in settleMetrics"
                  :key="m.labels.result"
                  :type="m.labels.result === 'done' ? 'success' : 'danger'"
                  size="small"
                >
                  {{ humanizeSettlement(m.labels.result) }}: {{ m.value }}
                </el-tag>
              </div>
            </div>
            <div v-if="reconcileMetrics.length" class="metric-block">
              <div class="metric-title">对账结果</div>
              <div class="chip-row">
                <el-tag
                  v-for="m in reconcileMetrics"
                  :key="m.labels.result"
                  :type="m.labels.result === 'reconciled' ? 'success' : 'warning'"
                  size="small"
                >
                  {{ humanizeReconcile(m.labels.result) }}: {{ m.value }}
                </el-tag>
              </div>
            </div>
            <div v-if="latencyMetrics.length" class="metric-block">
              <div class="metric-title">订单耗时</div>
              <el-table :data="latencyMetrics" size="small">
                <el-table-column label="状态" width="120">
                  <template #default="{ row }">{{ humanizeStatus(row.labels.status) }}</template>
                </el-table-column>
                <el-table-column label="笔数" width="80" align="right">
                  <template #default="{ row }">{{ row.count }}</template>
                </el-table-column>
                <el-table-column label="平均耗时" align="right">
                  <template #default="{ row }">{{ formatSeconds(row.avg_seconds) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <!-- 风控限额热更新 -->
    <el-card shadow="never" class="section">
      <template #header>
        <div class="section-header">
          <span class="section-title">风控限额（修改后立即生效）</span>
          <el-button text size="small" :icon="Refresh" @click="loadConfig">重新读取</el-button>
        </div>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px;"
        title="此处修改会立即作用于后续所有交易信号，并持久化到数据库（重启后仍生效）。"
      />
      <el-table :data="configFields" size="small" style="width: 100%">
        <el-table-column prop="description" label="配置项" min-width="220" />
        <el-table-column label="当前值" width="160">
          <template #default="{ row }">
            <el-tag v-if="row.isOverridden" type="warning" size="small">已覆盖</el-tag>
            <span style="margin-left: 6px;">{{ formatConfigValue(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openEdit(row)">修改</el-button>
            <el-button
              v-if="row.isOverridden"
              size="small"
              text
              @click="handleResetConfig(row)"
            >恢复默认</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialogVisible" title="修改风控限额" width="480px">
      <el-form :model="editForm" label-width="120px">
        <el-form-item label="配置项">
          <span>{{ editForm.description }}</span>
        </el-form-item>
        <el-form-item label="新值">
          <el-switch v-if="editForm.type === 'bool'" v-model="editForm.value" />
          <el-input-number
            v-else
            v-model="editForm.value"
            :min="editForm.min ?? undefined"
            :max="editForm.max ?? undefined"
            :step="editForm.type === 'float' && (editForm.max ?? 1) <= 1 ? 0.01 : 1"
            style="width: 100%;"
          />
        </el-form-item>
        <el-alert
          v-if="editForm.min !== null || editForm.max !== null"
          type="info"
          :closable="false"
          size="small"
          :title="`允许范围：${editForm.min ?? '不限'} ~ ${editForm.max ?? '不限'}`"
        />
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveConfig">保存并生效</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Monitor, Refresh, Bell } from '@element-plus/icons-vue'

import {
  getAlerts,
  getAlertStats,
  getMetricsSnapshot,
  getConfigOverrides,
  getConfigSchema,
  resolveAlert,
  runPatrol,
  updateConfig,
  resetConfig,
  type AlertStats,
  type ConfigOverride,
  type MetricSeries,
  type TradingAlert
} from '@/api/tradingObservability'

// ==================== 状态 ====================

const loading = ref(false)
const patrolling = ref(false)
const saving = ref(false)
const alerts = ref<TradingAlert[]>([])
const alertFilter = ref<'open' | 'resolved' | 'all'>('open')
const alertStats = ref<AlertStats>({ open: 0, resolved: 0, by_type: {}, by_severity: {} })
const snapshot = ref<Record<string, any>>({})
const configFields = ref<Array<ConfigOverride & { type?: string; min?: number | null; max?: number | null }>>([])

// 编辑对话框
const editDialogVisible = ref(false)
const editForm = ref<any>({ field: '', description: '', value: 0, type: 'int', min: null, max: null })

let timer: number | undefined

// ==================== 派生数据 ====================

function seriesOf(name: string): MetricSeries[] {
  return snapshot.value?.[name]?.series || []
}

const orderMetrics = computed(() => seriesOf('trading_order_total').filter(s => s.value))
const rejectMetrics = computed(() =>
  seriesOf('trading_risk_rejected_total').filter(s => s.value).sort((a, b) => (b.value || 0) - (a.value || 0))
)
const settleMetrics = computed(() => seriesOf('trading_settlement_total').filter(s => s.value))
const reconcileMetrics = computed(() => seriesOf('trading_reconcile_total').filter(s => s.value))
const latencyMetrics = computed(() => seriesOf('trading_order_latency_seconds').filter(s => s.count))

const totalOrders = computed(() =>
  orderMetrics.value.reduce((sum, s) => sum + (s.value || 0), 0)
)
const filledOrders = computed(() =>
  orderMetrics.value.filter(s => s.labels.status === 'filled').reduce((sum, s) => sum + (s.value || 0), 0)
)
const totalRejects = computed(() =>
  rejectMetrics.value.reduce((sum, s) => sum + (s.value || 0), 0)
)
const maxReject = computed(() =>
  Math.max(1, ...rejectMetrics.value.map(s => s.value || 0))
)

const statCards = computed(() => [
  {
    label: '未处理告警',
    value: alertStats.value.open,
    cls: alertStats.value.open > 0 ? 'value-danger' : 'value-ok',
    hint: alertStats.value.open > 0 ? '需人工介入' : '系统正常'
  },
  { label: '订单总数', value: totalOrders.value, cls: '', hint: '本次运行累计' },
  { label: '成交订单', value: filledOrders.value, cls: 'value-ok', hint: '状态 filled' },
  {
    label: '风控拒绝',
    value: totalRejects.value,
    cls: totalRejects.value > 0 ? 'value-warning' : '',
    hint: '各闸门累计'
  }
])

// ==================== 文案映射 ====================

const ALERT_TYPE_LABELS: Record<string, string> = {
  order_status_unknown: '委托状态未知',
  order_reconcile_missing: '柜台失联',
  settlement_incomplete: '结算中断'
}
const STATUS_LABELS: Record<string, string> = {
  filled: '已成交',
  submitted: '已报',
  failed: '失败',
  unknown: '状态未知',
  pending_sync: '待同步',
  risk_rejected: '风控拒绝',
  pending_confirmation: '待确认',
  recorded: '已记录'
}
const GATE_LABELS: Record<string, string> = {
  kill_switch: '全局熔断',
  live_disabled: '实盘未开启',
  whitelist: '用户白名单',
  low_confidence: '置信度不足',
  signal_stale: '信号过期',
  order_notional: '单笔金额超限',
  position_pct: '单票仓位超限',
  daily_orders: '每日笔数超限',
  daily_notional: '每日金额超限',
  bad_price: '成交价非法',
  other: '其他'
}
const SETTLEMENT_LABELS: Record<string, string> = {
  done: '完成',
  failed: '失败',
  repaired: '已补偿',
  needs_review: '待核查'
}
const RECONCILE_LABELS: Record<string, string> = {
  reconciled: '已补偿',
  missing: '失联',
  still_open: '仍在途',
  error: '异常'
}

function humanizeType(t: string) {
  return ALERT_TYPE_LABELS[t] || '未分类告警'
}
function humanizeStatus(s?: string) {
  return s ? STATUS_LABELS[s] || s : '—'
}
function humanizeGate(g?: string) {
  return g ? GATE_LABELS[g] || g : '—'
}
function humanizeSettlement(r?: string) {
  return r ? SETTLEMENT_LABELS[r] || r : '—'
}
function humanizeReconcile(r?: string) {
  return r ? RECONCILE_LABELS[r] || r : '—'
}

function statusTagType(status?: string) {
  if (status === 'filled') return 'success'
  if (status === 'failed' || status === 'unknown') return 'danger'
  if (status === 'risk_rejected') return 'warning'
  return 'info'
}

// ==================== 格式化 ====================

function formatTime(iso?: string) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

function formatSeconds(sec?: number | null) {
  if (sec === null || sec === undefined) return '—'
  if (sec < 1) return `${Math.round(sec * 1000)} ms`
  return `${sec.toFixed(2)} s`
}

function rejectPercent(v?: number) {
  return Math.round(((v || 0) / maxReject.value) * 100)
}

function formatConfigValue(row: any) {
  if (row.type === 'bool' || typeof row.runtime_value === 'boolean') {
    return row.runtime_value ? '开启' : '关闭'
  }
  return row.runtime_value
}

// ==================== 数据加载 ====================

async function loadAlerts() {
  const resolved = alertFilter.value === 'all' ? undefined : alertFilter.value === 'resolved'
  try {
    alerts.value = await getAlerts({ resolved, limit: 100 })
  } catch (e: any) {
    ElMessage.error(e?.message || '加载告警失败')
  }
}

async function loadStats() {
  try {
    alertStats.value = await getAlertStats()
  } catch {
    /* 静默：统计失败不影响主视图 */
  }
}

async function loadMetrics() {
  try {
    snapshot.value = await getMetricsSnapshot()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载指标失败')
  }
}

async function loadConfig() {
  try {
    const [overrides, schema] = await Promise.all([getConfigOverrides(), getConfigSchema()])
    // schema 提供 type/min/max；overrides 提供当前值与覆盖标记
    const schemaMap = new Map(schema.map(s => [s.field, s]))
    configFields.value = overrides.map(o => {
      const s = schemaMap.get(o.field)
      return {
        ...o,
        type: s?.type,
        min: s?.min ?? null,
        max: s?.max ?? null
      }
    })
  } catch (e: any) {
    ElMessage.error(e?.message || '加载配置失败')
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([loadAlerts(), loadStats(), loadMetrics(), loadConfig()])
  } finally {
    loading.value = false
  }
}

// ==================== 交互 ====================

async function handlePatrol() {
  patrolling.value = true
  try {
    const res = await runPatrol()
    if (res.patrolled > 0) {
      ElMessage.warning(`巡检发现 ${res.patrolled} 条未处理告警`)
    } else {
      ElMessage.success('巡检完成，无待处理告警')
    }
    await refreshAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '巡检失败')
  } finally {
    patrolling.value = false
  }
}

async function handleResolve(row: TradingAlert) {
  const id = row.order_id || row.settlement_id
  if (!id) {
    ElMessage.error('该告警缺少订单标识，无法确认')
    return
  }
  try {
    const { value } = await ElMessageBox.prompt(
      '请填写处理说明（可留空）',
      '确认告警已处理',
      { confirmButtonText: '确认', cancelButtonText: '取消', inputPlaceholder: '如：已人工核对柜台，确认成交' }
    )
    await resolveAlert(id, value || '')
    ElMessage.success('已标记为处理完成')
    await refreshAll()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e?.message || '操作失败')
    }
  }
}

function openEdit(row: any) {
  editForm.value = {
    field: row.field,
    description: row.description,
    value: row.runtime_value,
    type: row.type,
    min: row.min,
    max: row.max
  }
  editDialogVisible.value = true
}

async function handleSaveConfig() {
  saving.value = true
  try {
    await updateConfig(editForm.value.field, editForm.value.value)
    ElMessage.success('已保存并立即生效')
    editDialogVisible.value = false
    await loadConfig()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleResetConfig(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定恢复「${row.description}」为 .env 默认值？（下次启动生效）`,
      '恢复默认',
      { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' }
    )
    await resetConfig([row.field])
    ElMessage.success('已恢复默认')
    await loadConfig()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e?.message || '操作失败')
    }
  }
}

// ==================== 生命周期 ====================

onMounted(async () => {
  await refreshAll()
  // 30 秒自动刷新（告警与指标随时变化）
  timer = window.setInterval(() => {
    loadAlerts()
    loadStats()
    loadMetrics()
  }, 30000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.trading-observability {
  padding: 16px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.title {
  display: flex;
  align-items: center;
  font-size: 18px;
  font-weight: 600;
}

.actions {
  display: flex;
  gap: 8px;
}

.stat-row {
  margin-bottom: 12px;
}

.stat-card {
  text-align: center;
}

.stat-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
}

.value-danger {
  color: #f56c6c;
}

.value-warning {
  color: #e6a23c;
}

.value-ok {
  color: #67c23a;
}

.stat-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 6px;
}

.section {
  margin-bottom: 12px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section-title {
  font-weight: 600;
  display: inline-flex;
  align-items: center;
}

.section-actions {
  display: flex;
  gap: 8px;
}

.metric-block {
  margin-bottom: 16px;
}

.metric-block:last-child {
  margin-bottom: 0;
}

.metric-title {
  font-size: 13px;
  color: #606266;
  font-weight: 600;
  margin-bottom: 8px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.reject-row {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.reject-name {
  width: 120px;
  font-size: 13px;
  color: #606266;
}

.reject-count {
  width: 48px;
  text-align: right;
  font-weight: 600;
  font-size: 13px;
}

.text-gray {
  color: #909399;
}
</style>
