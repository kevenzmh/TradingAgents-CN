<template>
  <div class="schedule-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><AlarmClock /></el-icon>
          定时分析
        </h1>
        <p class="page-description">
          按 CRON 时间自动分析「自选股分组」里的股票。每次触发会为分组内每只股票创建一个分析任务（消耗模型额度），
          结果通过站内消息通知，可选邮件。时区：{{ timezone }}。
        </p>
      </div>
      <el-button type="primary" :icon="Plus" :disabled="configs.length >= maxConfigs" @click="openCreate">新建配置</el-button>
    </div>

    <el-alert
      v-if="!loading && !groupOptions.length"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #title>
        还没有自选股分组，定时分析需要先有分组。
        <router-link to="/settings/watchlist-groups">前往创建分组</router-link>
      </template>
    </el-alert>

    <el-card shadow="never" class="block">
      <el-table :data="configs" v-loading="loading" stripe empty-text="还没有定时分析配置，点击右上角“新建配置”">
        <el-table-column type="expand">
          <template #default="{ row }">
            <el-table :data="row.time_slots" size="small" class="slot-table">
              <el-table-column prop="name" label="时间段" width="140" />
              <el-table-column label="CRON" width="150">
                <template #default="{ row: s }"><code>{{ s.cron_expression }}</code></template>
              </el-table-column>
              <el-table-column label="分组" min-width="200">
                <template #default="{ row: s }">
                  <el-tag v-for="id in effectiveGroups(row, s)" :key="id" size="small" effect="plain" class="gtag">{{ groupName(id) }}</el-tag>
                  <el-tag v-if="s.has_workflow" size="small" type="warning">通用研究流程</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="深度" width="80">
                <template #default="{ row: s }">{{ s.analysis_depth || row.default_analysis_depth }}</template>
              </el-table-column>
              <el-table-column label="下次执行" width="170">
                <template #default="{ row: s }">{{ s.enabled && row.enabled ? formatTime(s.next_run_at) : '—' }}</template>
              </el-table-column>
              <el-table-column label="状态" width="80">
                <template #default="{ row: s }">
                  <el-tag :type="s.enabled ? 'success' : 'info'" size="small">{{ s.enabled ? '启用' : '停用' }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }">
            <strong>{{ row.name }}</strong>
            <div v-if="row.description" class="muted">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="时间段" width="90">
          <template #default="{ row }">{{ row.time_slots.length }} 个</template>
        </el-table-column>
        <el-table-column label="通知" width="170">
          <template #default="{ row }">
            <el-tag v-if="row.notify_on_complete" size="small" type="info" class="gtag">站内</el-tag>
            <el-tag v-if="row.send_email" size="small" type="info" class="gtag">邮件</el-tag>
            <span v-if="!row.notify_on_complete && !row.send_email" class="muted">无</span>
          </template>
        </el-table-column>
        <el-table-column label="上次执行" width="170">
          <template #default="{ row }">{{ formatTime(row.last_run_at) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" :loading="busyId === row.id" @change="(v: any) => toggle(row, !!v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!row.enabled" @click="openRun(row)">立即执行</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm :title="`确认删除「${row.name}」？执行历史会保留。`" @confirm="remove(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>执行历史</span>
          <el-button size="small" :icon="Refresh" :loading="historyLoading" @click="loadHistory">刷新</el-button>
        </div>
      </template>
      <el-table :data="history" size="small" stripe v-loading="historyLoading" empty-text="暂无执行记录">
        <el-table-column label="时间" width="165">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="config_name" label="配置" min-width="150" />
        <el-table-column prop="time_slot_name" label="时间段" width="130" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="任务" width="170">
          <template #default="{ row }">共 {{ row.total_count }}，成功 {{ row.success_count }}，失败 {{ row.failed_count }}</template>
        </el-table-column>
        <el-table-column prop="result_summary" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="查看" width="90">
          <template #default="{ row }">
            <el-button v-if="row.task_ids?.length" link type="primary" size="small" @click="$router.push('/tasks/unified')">任务中心</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="formVisible" :title="editing ? '编辑定时分析' : '新建定时分析'" width="760px" top="4vh" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" @submit.prevent>
        <el-form-item label="配置名称" prop="name">
          <el-input v-model="form.name" maxlength="50" show-word-limit placeholder="如：每日早盘分析" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="200" />
        </el-form-item>
        <el-form-item label="默认分组">
          <el-select v-model="form.default_group_ids" multiple filterable clearable placeholder="时间段未单独选分组时使用" style="width: 100%">
            <el-option v-for="g in groupOptions" :key="g.id" :value="g.id" :label="`${g.name}（${g.stock_count}只）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="默认深度">
          <el-select v-model="form.default_analysis_depth" style="width: 200px">
            <el-option v-for="d in 5" :key="d" :value="d" :label="`${d} - ${DEPTH_LABELS[d - 1]}`" />
          </el-select>
        </el-form-item>
        <el-form-item label="通知">
          <el-checkbox v-model="form.notify_on_complete">完成后站内通知</el-checkbox>
          <el-checkbox v-model="form.send_email">完成后发邮件（需在「邮件通知」开启）</el-checkbox>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <el-divider content-position="left">时间段（最多 {{ maxSlots }} 个）</el-divider>
        <div v-for="(s, i) in form.time_slots" :key="i" class="slot-box">
          <div class="slot-head">
            <strong>时间段 {{ i + 1 }}</strong>
            <span>
              <el-switch v-model="s.enabled" size="small" style="margin-right: 8px" />
              <el-button link type="danger" size="small" :disabled="form.time_slots.length === 1" @click="removeSlot(i)">移除</el-button>
            </span>
          </div>
          <el-form-item label="名称" :prop="`time_slots.${i}.name`" :rules="[{ required: true, message: '请填写时间段名称', trigger: 'blur' }]">
            <el-input v-model="s.name" maxlength="30" style="width: 240px" placeholder="如：开盘前" />
          </el-form-item>
          <el-form-item label="CRON" :prop="`time_slots.${i}.cron_expression`" :rules="[{ required: true, message: '请填写 CRON', trigger: 'blur' }]">
            <el-input v-model="s.cron_expression" style="width: 200px" placeholder="分 时 日 月 周" @input="queuePreview(i)" />
            <el-select placeholder="常用" style="width: 150px; margin-left: 8px" @change="(v: string) => applyPreset(i, v)">
              <el-option v-for="p in PRESETS" :key="p.cron" :value="p.cron" :label="p.label" />
            </el-select>
            <div class="preview" :class="{ bad: previews[i] && !previews[i].valid }">
              <template v-if="previews[i]">
                <template v-if="previews[i].valid">接下来：{{ previews[i].next.slice(0, 3).map(formatTime).join('；') }}</template>
                <template v-else>{{ previews[i].message }}</template>
              </template>
              <template v-else>示例：0 9 * * 1-5 = 工作日 9:00（周 0/7=周日，1=周一）</template>
            </div>
          </el-form-item>
          <el-form-item label="分组">
            <el-select v-model="s.group_ids" multiple filterable clearable placeholder="留空则使用默认分组" style="width: 100%">
              <el-option v-for="g in groupOptions" :key="g.id" :value="g.id" :label="`${g.name}（${g.stock_count}只）`" />
            </el-select>
          </el-form-item>
          <el-form-item label="分析深度">
            <el-select v-model="s.analysis_depth" clearable placeholder="跟随分组/默认" style="width: 200px">
              <el-option v-for="d in 5" :key="d" :value="d" :label="`${d} - ${DEPTH_LABELS[d - 1]}`" />
            </el-select>
          </el-form-item>
        </div>
        <el-button :icon="Plus" :disabled="form.time_slots.length >= maxSlots" @click="addSlot">添加时间段</el-button>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- 立即执行 -->
    <el-dialog v-model="runVisible" title="立即执行" width="460px">
      <el-form label-width="80px" @submit.prevent>
        <el-form-item label="时间段">
          <el-select v-model="runSlot" style="width: 100%">
            <el-option
              v-for="(s, i) in runTarget?.time_slots || []"
              :key="i"
              :value="i"
              :label="`${s.name}${s.enabled ? '' : '（已停用）'}`"
              :disabled="!s.enabled"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <el-alert type="warning" :closable="false" show-icon :title="`将立即为所选时间段分组内的股票（约 ${runStockEstimate} 只）创建分析任务，会消耗模型额度。`" />
      <template #footer>
        <el-button @click="runVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmRun">确认执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { AlarmClock, Plus, Refresh } from '@element-plus/icons-vue'

import { listGroups, type WatchlistGroup } from '@/api/watchlistGroups'
import {
  createConfig,
  deleteConfig,
  getHistory,
  listConfigs,
  previewCron,
  runSlotNow,
  toggleConfig,
  updateConfig,
  type ConfigPayload,
  type CronPreview,
  type HistoryItem,
  type ScheduleConfig,
  type Slot
} from '@/api/scheduledAnalysis'

const DEPTH_LABELS = ['快速', '基础', '标准', '深度', '全面']
const PRESETS = [
  { label: '工作日 08:30', cron: '30 8 * * 1-5' },
  { label: '工作日 09:00', cron: '0 9 * * 1-5' },
  { label: '工作日 15:30（收盘后）', cron: '30 15 * * 1-5' },
  { label: '每日 20:00', cron: '0 20 * * *' },
  { label: '每周一 09:00', cron: '0 9 * * 1' }
]

const configs = ref<ScheduleConfig[]>([])
const groupOptions = ref<WatchlistGroup[]>([])
const timezone = ref('Asia/Shanghai')
const maxConfigs = ref(20)
const maxSlots = ref(10)
const loading = ref(false)
const saving = ref(false)
const busyId = ref('')

const groupName = (id: string) => groupOptions.value.find(g => g.id === id)?.name || '（已删除的分组）'
const effectiveGroups = (c: ScheduleConfig, s: Slot) => (s.group_ids.length ? s.group_ids : c.default_group_ids)

function formatTime(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    const [cfg, grp] = await Promise.all([listConfigs(), listGroups()])
    configs.value = cfg.items
    timezone.value = cfg.timezone
    maxConfigs.value = cfg.max_configs
    maxSlots.value = cfg.max_slots
    groupOptions.value = grp.items
  } finally {
    loading.value = false
  }
}

// ---- 表单 ----
interface SlotForm { name: string; cron_expression: string; enabled: boolean; group_ids: string[]; analysis_depth: number | null }
const formVisible = ref(false)
const editing = ref<ScheduleConfig | null>(null)
const formRef = ref<FormInstance>()
const blankSlot = (): SlotForm => ({ name: '', cron_expression: '0 9 * * 1-5', enabled: true, group_ids: [], analysis_depth: null })
const form = reactive({
  name: '', description: '', enabled: true, default_group_ids: [] as string[], default_analysis_depth: 3,
  notify_on_complete: true, notify_on_error: true, send_email: false, time_slots: [blankSlot()] as SlotForm[]
})
const rules: FormRules = { name: [{ required: true, message: '请填写配置名称', trigger: 'blur' }] }
const previews = ref<Record<number, CronPreview>>({})
const timers: Record<number, ReturnType<typeof setTimeout>> = {}

function queuePreview(i: number) {
  clearTimeout(timers[i])
  timers[i] = setTimeout(async () => {
    const expr = form.time_slots[i]?.cron_expression?.trim()
    if (!expr) {
      delete previews.value[i]
      return
    }
    try {
      previews.value[i] = await previewCron(expr, 3)
    } catch {
      /* 预览失败不打扰 */
    }
  }, 300)
}

function applyPreset(i: number, cron: string) {
  form.time_slots[i].cron_expression = cron
  queuePreview(i)
}

function addSlot() {
  form.time_slots.push({ ...blankSlot(), name: `时间段${form.time_slots.length + 1}` })
}

function removeSlot(i: number) {
  form.time_slots.splice(i, 1)
  previews.value = {}
  form.time_slots.forEach((_, idx) => queuePreview(idx))
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '', description: '', enabled: true, default_group_ids: [], default_analysis_depth: 3,
    notify_on_complete: true, notify_on_error: true, send_email: false, time_slots: [{ ...blankSlot(), name: '开盘前', cron_expression: '30 8 * * 1-5' }]
  })
  previews.value = {}
  queuePreview(0)
  formVisible.value = true
  formRef.value?.clearValidate()
}

function openEdit(c: ScheduleConfig) {
  editing.value = c
  Object.assign(form, {
    name: c.name, description: c.description, enabled: c.enabled, default_group_ids: [...c.default_group_ids],
    default_analysis_depth: c.default_analysis_depth, notify_on_complete: c.notify_on_complete,
    notify_on_error: c.notify_on_error, send_email: c.send_email,
    time_slots: c.time_slots.map(s => ({
      name: s.name, cron_expression: s.cron_expression, enabled: s.enabled, group_ids: [...s.group_ids], analysis_depth: s.analysis_depth
    }))
  })
  previews.value = {}
  form.time_slots.forEach((_, i) => queuePreview(i))
  formVisible.value = true
  formRef.value?.clearValidate()
}

async function save() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    const payload: ConfigPayload = {
      name: form.name.trim(), description: form.description, enabled: form.enabled,
      default_group_ids: form.default_group_ids, default_analysis_depth: form.default_analysis_depth,
      notify_on_complete: form.notify_on_complete, notify_on_error: form.notify_on_error, send_email: form.send_email,
      time_slots: form.time_slots.map(s => ({ ...s, name: s.name.trim(), cron_expression: s.cron_expression.trim() }))
    }
    if (editing.value) await updateConfig(editing.value.id, payload)
    else await createConfig(payload)
    ElMessage.success(editing.value ? '已保存并重新挂载调度' : '已创建，到点将自动执行')
    formVisible.value = false
    await load()
  } catch {
    /* 校验错误（CRON、分组等）由全局拦截器提示 */
  } finally {
    saving.value = false
  }
}

async function toggle(c: ScheduleConfig, enabled: boolean) {
  busyId.value = c.id
  try {
    await toggleConfig(c.id, enabled)
    await load()
  } catch {
    /* 已提示 */
  } finally {
    busyId.value = ''
  }
}

async function remove(c: ScheduleConfig) {
  try {
    await deleteConfig(c.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 已提示 */
  }
}

// ---- 立即执行 ----
const runVisible = ref(false)
const runTarget = ref<ScheduleConfig | null>(null)
const runSlot = ref(0)
const runStockEstimate = computed(() => {
  const c = runTarget.value
  const s = c?.time_slots[runSlot.value]
  if (!c || !s) return 0
  const ids = effectiveGroups(c, s)
  return groupOptions.value.filter(g => ids.includes(g.id) && g.is_active).reduce((n, g) => n + g.stock_count, 0)
})

function openRun(c: ScheduleConfig) {
  runTarget.value = c
  runSlot.value = Math.max(0, c.time_slots.findIndex(s => s.enabled))
  runVisible.value = true
}

async function confirmRun() {
  saving.value = true
  try {
    await runSlotNow(runTarget.value!.id, runSlot.value)
    ElMessage.success('已开始执行，稍后在“执行历史”查看结果')
    runVisible.value = false
    setTimeout(() => loadHistory(), 4000)
  } catch {
    /* 429 / 409 等由全局拦截器提示 */
  } finally {
    saving.value = false
  }
}

// ---- 历史 ----
const history = ref<HistoryItem[]>([])
const historyLoading = ref(false)

async function loadHistory() {
  historyLoading.value = true
  try {
    history.value = await getHistory(undefined, 20)
  } finally {
    historyLoading.value = false
  }
}

const statusLabel = (s: string) => (({ success: '成功', partial: '部分成功', failed: '失败' } as Record<string, string>)[s] || s)
const statusTag = (s: string) => (({ success: 'success', partial: 'warning', failed: 'danger' } as Record<string, any>)[s] || 'info')

onMounted(() => {
  load()
  loadHistory()
})
onBeforeUnmount(() => Object.values(timers).forEach(clearTimeout))
</script>

<style scoped lang="scss">
.schedule-page {
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;

  .page-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 0 8px;
    font-size: 24px;
    font-weight: 600;
  }

  .page-description {
    margin: 0;
    max-width: 760px;
    font-size: 14px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }
}

.block {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.slot-table {
  margin: 4px 24px 8px 48px;
  width: calc(100% - 72px);
}

.gtag {
  margin: 0 4px 2px 0;
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.slot-box {
  margin-bottom: 12px;
  padding: 10px 14px 2px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-extra-light);

  .slot-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
}

.preview {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);

  &.bad {
    color: var(--el-color-danger);
  }
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
}
</style>
