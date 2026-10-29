<template>
  <div class="trading-core">
    <!-- 标题栏 -->
    <div class="header">
      <div class="title">
        <el-icon style="margin-right: 8px"><Warning /></el-icon>
        <span>交易信号中心</span>
      </div>
      <div class="actions">
        <el-button :icon="Refresh" text size="small" @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <!-- 风控状态条 -->
    <el-alert
      v-if="riskStatus && riskStatus.kill_switch?.active"
      type="error"
      :closable="false"
      show-icon
      style="margin-bottom: 12px;"
    >
      <template #title>
        <div style="display: flex; align-items: center; gap: 12px; width: 100%;">
          <span style="font-weight: 700;">交易已全局熔断：{{ riskStatus.kill_switch?.reason || '手动触发' }}</span>
          <el-button
            v-if="authStore.isAdmin"
            size="small"
            type="danger"
            @click="handleKillSwitchOff"
            :loading="killSwitchLoading"
          >解除熔断</el-button>
        </div>
      </template>
    </el-alert>

    <el-card v-if="riskStatus" shadow="never" class="risk-bar">
      <div class="risk-bar-content">
        <div class="risk-item">
          <span class="risk-label">交易模式</span>
          <el-tag :type="modeTagType" effect="dark">{{ modeText }}</el-tag>
        </div>
        <div class="risk-item">
          <span class="risk-label">实盘开关</span>
          <el-tag :type="riskStatus.live_trading_enabled ? 'danger' : 'info'" size="small">
            {{ riskStatus.live_trading_enabled ? '开启' : '关闭' }}
          </el-tag>
        </div>
        <div class="risk-item">
          <span class="risk-label">今日订单</span>
          <el-progress
            :percentage="dailyOrderPct"
            :status="dailyOrderPct >= 100 ? 'exception' : undefined"
            style="width: 140px;"
          />
          <span class="risk-numbers">{{ todayOrders }}/{{ riskStatus.limits?.max_daily_orders ?? '—' }}</span>
        </div>
        <div class="risk-item">
          <span class="risk-label">今日名义额</span>
          <el-progress
            :percentage="dailyNotionalPct"
            :status="dailyNotionalPct >= 100 ? 'exception' : undefined"
            style="width: 140px;"
          />
          <span class="risk-numbers">¥{{ fmtAmount(todayNotional) }}/¥{{ fmtAmount(riskStatus.limits?.max_daily_notional ?? 0) }}</span>
        </div>
        <div class="risk-item" v-if="authStore.isAdmin && !riskStatus.kill_switch?.active">
          <el-button size="small" type="danger" plain @click="handleKillSwitchOn">触发熔断</el-button>
        </div>
      </div>
    </el-card>

    <!-- 待确认订单（核心操作区） -->
    <el-card shadow="never" class="pending-card">
      <template #header>
        <div class="card-hd">
          <el-icon><Bell /></el-icon>
          <span>待确认订单</span>
          <el-badge v-if="pendingOrders.length" :value="pendingOrders.length" type="warning" style="margin-left: 8px;" />
          <span class="hd-sub">dry_run 模式下需人工确认后才会撮合</span>
        </div>
      </template>
      <el-table
        v-if="pendingOrders.length"
        :data="pendingOrders"
        row-key="order_id"
        size="default"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-content">
              <el-descriptions :column="2" border size="small" title="风控检查">
                <el-descriptions-item label="结论">
                  <el-tag :type="row.risk_check?.approved ? 'success' : 'danger'" size="small">
                    {{ row.risk_check?.approved ? '通过' : '拒绝' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="委托名义额">¥{{ fmtAmount(row.notional) }}</el-descriptions-item>
              </el-descriptions>
              <div v-if="row.risk_check?.rejections?.length" class="reject-list">
                <div class="reject-title">拒绝原因：</div>
                <ul>
                  <li v-for="r in row.risk_check.rejections" :key="r">{{ r }}</li>
                </ul>
              </div>
              <div v-if="row.fill" class="fill-detail">
                成交明细：<pre>{{ JSON.stringify(row.fill, null, 2) }}</pre>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="代码" prop="symbol" width="100" />
        <el-table-column label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'buy' ? 'success' : 'danger'" size="small">
              {{ row.side === 'buy' ? '买入' : '卖出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数量" prop="quantity" width="90" />
        <el-table-column label="委托价" width="110">
          <template #default="{ row }">¥{{ fmtPrice(row.limit_price) }}</template>
        </el-table-column>
        <el-table-column label="名义额" width="130">
          <template #default="{ row }">¥{{ fmtAmount(row.notional) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              :loading="actingOrderId === row.order_id"
              @click="handleConfirm(row)"
            >确认</el-button>
            <el-button
              size="small"
              type="warning"
              plain
              :loading="actingOrderId === row.order_id"
              @click="handleCancel(row)"
            >取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无待确认订单" :image-size="80" />
    </el-card>

    <!-- 全部订单 + 信号 -->
    <el-card shadow="never" class="tabs-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="订单" name="orders">
          <div class="filter-row">
            <el-select v-model="orderStatusFilter" size="small" style="width: 200px;" clearable placeholder="全部状态">
              <el-option
                v-for="s in orderStatusOptions"
                :key="s.value"
                :label="s.label"
                :value="s.value"
              />
            </el-select>
          </div>
          <el-table :data="allOrders" row-key="order_id" size="small">
            <el-table-column label="代码" prop="symbol" width="100" />
            <el-table-column label="方向" width="70">
              <template #default="{ row }">
                <el-tag :type="row.side === 'buy' ? 'success' : 'danger'" size="small">
                  {{ row.side === 'buy' ? '买' : '卖' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="数量" prop="quantity" width="80" />
            <el-table-column label="委托价" width="100">
              <template #default="{ row }">¥{{ fmtPrice(row.limit_price) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="柜台委托号" width="160">
              <template #default="{ row }">
                <span v-if="row.broker_order_id" class="mono">{{ row.broker_order_id }}</span>
                <span v-else style="color: #c0c4cc;">—</span>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="信号" name="signals">
          <el-table :data="signals" row-key="signal_id" size="small">
            <el-table-column label="代码" prop="symbol" width="100" />
            <el-table-column label="倾向" width="100">
              <template #default="{ row }">
                <el-tag :type="row.action === '乐观' ? 'success' : 'warning'" size="small">{{ row.action }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="方向" width="70">
              <template #default="{ row }">
                <el-tag :type="row.side === 'buy' ? 'success' : 'danger'" size="small">
                  {{ row.side === 'buy' ? '买' : '卖' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="置信度" width="100">
              <template #default="{ row }">{{ (row.confidence * 100).toFixed(0) }}%</template>
            </el-table-column>
            <el-table-column label="分析日期" prop="analysis_date" width="120" />
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button
                  v-if="row.analysis_id"
                  size="small"
                  text
                  type="primary"
                  @click="goReport(row.analysis_id)"
                >查看报告</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Warning, Bell } from '@element-plus/icons-vue'
import tradingApi from '@/api/tradingCore'
import type {
  TradingSignal,
  TradingOrder,
  RiskStatus,
  TradingOrderStatus,
  OrderActionResult
} from '@/api/tradingCore'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

// ── 数据状态 ──
const riskStatus = ref<RiskStatus | null>(null)
const pendingOrders = ref<TradingOrder[]>([])
const allOrders = ref<TradingOrder[]>([])
const signals = ref<TradingSignal[]>([])
const activeTab = ref('orders')
const orderStatusFilter = ref('')
const actingOrderId = ref('')
const killSwitchLoading = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// ── 计算属性 ──
const modeText = computed(() => {
  const m = riskStatus.value?.trading_mode
  if (m === 'dry_run') return 'dry_run 模拟撮合'
  if (m === 'live') return 'live 实盘'
  return 'signal_only 仅记录'
})

const modeTagType = computed(() => {
  const m = riskStatus.value?.trading_mode
  if (m === 'live') return 'danger'
  if (m === 'dry_run') return 'warning'
  return 'info'
})

const todayOrders = computed(() => Number(riskStatus.value?.today?.orders ?? 0))
const todayNotional = computed(() => Number(riskStatus.value?.today?.notional ?? 0))

const dailyOrderPct = computed(() => {
  const max = riskStatus.value?.limits?.max_daily_orders
  if (!max || max <= 0) return 0
  return Math.min(100, Math.round((todayOrders.value / max) * 100))
})

const dailyNotionalPct = computed(() => {
  const max = riskStatus.value?.limits?.max_daily_notional
  if (!max || max <= 0) return 0
  return Math.min(100, Math.round((todayNotional.value / max) * 100))
})

// ── 状态展示 ──
const statusMap: Record<string, { text: string; type: 'success' | 'danger' | 'warning' | 'info' | 'primary' }> = {
  pending_confirmation: { text: '待确认', type: 'warning' },
  risk_rejected: { text: '风控拒绝', type: 'danger' },
  recorded: { text: '已记录', type: 'info' },
  filled: { text: '已成交', type: 'success' },
  failed: { text: '失败', type: 'danger' },
  submitted: { text: '柜台已受理', type: 'primary' },
  needs_review: { text: '待人工复核', type: 'danger' },
  cancelled: { text: '已取消', type: 'info' },
  draft: { text: '草稿', type: 'info' }
}

const statusText = (s: TradingOrderStatus | string) => statusMap[s]?.text ?? s
const statusTagType = (s: TradingOrderStatus | string) => statusMap[s]?.type ?? 'info'

const orderStatusOptions = Object.entries(statusMap).map(([value, v]) => ({ value, label: v.text }))

// ── 格式化 ──
const fmtAmount = (n: unknown) => {
  const v = Number(n ?? 0)
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

const fmtPrice = (n: unknown) => Number(n ?? 0).toFixed(2)

const formatDateTime = (iso?: string) => {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

// ── 数据加载 ──
const loadRiskStatus = async () => {
  try {
    const res = await tradingApi.getRiskStatus()
    if (res.success) riskStatus.value = res.data
  } catch {
    // 静默：风控状态加载失败不打断页面
  }
}

const loadOrders = async () => {
  try {
    const res = await tradingApi.getOrders(undefined, 100)
    if (res.success) allOrders.value = res.data.orders ?? []
    pendingOrders.value = allOrders.value.filter(o => o.status === 'pending_confirmation')
  } catch {
    // 静默：轮询失败保留上次数据
  }
}

const loadSignals = async () => {
  try {
    const res = await tradingApi.getSignals({ limit: 100 })
    if (res.success) signals.value = res.data.signals ?? []
  } catch {
    // 静默
  }
}

const refreshAll = async () => {
  await Promise.all([loadRiskStatus(), loadOrders(), loadSignals()])
}

// ── 操作 ──
const handleConfirm = async (order: TradingOrder) => {
  try {
    await ElMessageBox.confirm(
      `确认执行 ${order.symbol} ${order.side === 'buy' ? '买入' : '卖出'} ${order.quantity} 股 @ ¥${fmtPrice(order.limit_price)}（名义额 ¥${fmtAmount(order.notional)}）？确认后将重跑风控再撮合。`,
      '确认订单',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  actingOrderId.value = order.order_id
  try {
    const res = await tradingApi.confirmOrder(order.order_id)
    if (res.success) {
      showActionResult(res.data, '确认成功')
      await refreshAll()
    }
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '确认失败')
  } finally {
    actingOrderId.value = ''
  }
}

const handleCancel = async (order: TradingOrder) => {
  try {
    await ElMessageBox.confirm(
      `取消 ${order.symbol} 的这笔待确认订单？`,
      '取消订单',
      { type: 'warning', confirmButtonText: '取消订单', cancelButtonText: '保留' }
    )
  } catch {
    return
  }
  actingOrderId.value = order.order_id
  try {
    const res = await tradingApi.cancelOrder(order.order_id)
    if (res.success) {
      showActionResult(res.data, '已取消')
      await refreshAll()
    }
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '取消失败')
  } finally {
    actingOrderId.value = ''
  }
}

/** 确认后按响应实际状态提示（confirm 不保证变 filled：可能重跑风控被拒） */
const showActionResult = (result: OrderActionResult, prefix: string) => {
  const s = statusText(result.status)
  if (result.status === 'filled') {
    ElMessage.success(`${prefix}：已成交 @ ¥${fmtPrice(result.fill?.price)}`)
  } else if (result.status === 'failed') {
    ElMessage.error(`${prefix}后失败：${result.fill?.error || '未知原因'}`)
  } else if (result.status === 'risk_rejected') {
    const reasons = result.risk?.rejections?.join('；') ?? ''
    ElMessage.warning(`${prefix}后重跑风控被拒：${reasons}`)
  } else if (result.status === 'submitted') {
    ElMessage.info(`${prefix}：柜台已受理（${s}），成交待回报同步`)
  } else {
    ElMessage.info(`${prefix}：当前状态 ${s}`)
  }
}

const goReport = (analysisId: string) => {
  router.push({ name: 'ReportDetail', params: { id: analysisId } })
}

// ── kill switch ──
const handleKillSwitchOn = async () => {
  let reason = '页面手动触发'
  try {
    const { value } = await ElMessageBox.prompt('触发全局交易熔断，所有执行请求将立即拒绝。填写原因：', '触发熔断', {
      type: 'error',
      inputValue: reason,
      confirmButtonText: '触发熔断'
    })
    reason = value || reason
  } catch {
    return
  }
  killSwitchLoading.value = true
  try {
    const res = await tradingApi.setKillSwitch(reason, false)
    if (res.success) {
      ElMessage.error('熔断已触发')
      await loadRiskStatus()
    }
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '触发失败（需管理员权限）')
  } finally {
    killSwitchLoading.value = false
  }
}

const handleKillSwitchOff = async () => {
  try {
    await ElMessageBox.confirm('解除全局交易熔断，恢复执行通道？', '解除熔断', { type: 'warning' })
  } catch {
    return
  }
  killSwitchLoading.value = true
  try {
    const res = await tradingApi.setKillSwitch('页面解除', true)
    if (res.success) {
      ElMessage.success('熔断已解除')
      await loadRiskStatus()
    }
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    ElMessage.error(msg || '解除失败')
  } finally {
    killSwitchLoading.value = false
  }
}

// ── 生命周期：30s 轮询（低频场景足够） ──
onMounted(() => {
  refreshAll()
  pollTimer = setInterval(refreshAll, 30_000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style lang="scss" scoped>
.trading-core {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;

  .title {
    display: flex;
    align-items: center;
    font-size: 20px;
    font-weight: 600;
  }
}

.risk-bar-content {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 24px;
}

.risk-item {
  display: flex;
  align-items: center;
  gap: 8px;

  .risk-label {
    color: #909399;
    font-size: 13px;
  }

  .risk-numbers {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
  }
}

.pending-card,
.tabs-card {
  margin-bottom: 16px;

  .card-hd {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;

    .hd-sub {
      font-size: 12px;
      color: #909399;
      font-weight: normal;
      margin-left: 4px;
    }
  }
}

.filter-row {
  margin-bottom: 12px;
}

.expand-content {
  padding: 8px 16px;

  .reject-list {
    margin-top: 10px;
    color: #f56c6c;
    font-size: 13px;

    ul {
      margin: 4px 0 0;
      padding-left: 20px;
    }
  }

  .fill-detail {
    margin-top: 10px;

    pre {
      margin: 4px 0 0;
      font-size: 12px;
      background: #f5f7fa;
      padding: 8px;
      border-radius: 4px;
    }
  }
}

.mono {
  font-family: monospace;
  font-size: 12px;
}
</style>
