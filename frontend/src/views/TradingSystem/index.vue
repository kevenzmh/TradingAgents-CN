<template>
  <div class="systems-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><Tickets /></el-icon>
          交易计划
        </h1>
        <p class="page-description">
          把选股、择时、仓位、风控与纪律写成可检查的规则。持仓复盘 / 操作复盘时关联交易计划，AI 会逐条核对你是否违反了自己的规则。
        </p>
      </div>
      <div class="header-actions">
        <el-button :icon="CopyDocument" @click="openTemplates">从模板创建</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建交易计划</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && !systems.length" description="还没有交易计划，可以从模板一键创建，或自己新建" />

    <el-row v-loading="loading" :gutter="16">
      <el-col v-for="s in systems" :key="s.id" :xs="24" :md="12" :xl="8">
        <el-card class="system-card" shadow="hover" :class="{ active: s.is_active }">
          <template #header>
            <div class="card-header">
              <strong>{{ s.name }}</strong>
              <el-tag v-if="s.is_active" type="success" size="small">默认</el-tag>
            </div>
          </template>
          <div class="tags">
            <el-tag size="small" effect="plain">{{ styleLabel(s.style) }}</el-tag>
          </div>
          <p class="desc">{{ s.description || '（无描述）' }}</p>
          <div class="actions">
            <el-button size="small" type="primary" @click="openEdit(s.id!)">编辑规则</el-button>
            <el-button size="small" :disabled="s.is_active" @click="setDefault(s)">设为默认</el-button>
            <el-popconfirm :title="`确认删除「${s.name}」？已关联它的历史复盘不受影响。`" @confirm="remove(s)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 模板 -->
    <el-dialog v-model="tplVisible" title="从模板创建" width="720px">
      <el-row :gutter="12">
        <el-col v-for="t in templates" :key="t.id" :span="8">
          <el-card shadow="hover" class="tpl-card" :class="{ picked: picked === t.id }" @click="picked = t.id">
            <strong>{{ t.name }}</strong>
            <div class="tags">
              <el-tag size="small" effect="plain">{{ styleLabel(t.style) }}</el-tag>
              <el-tag size="small" type="info" effect="plain">{{ riskLabel(t.risk_profile) }}</el-tag>
            </div>
            <p class="desc">{{ t.description }}</p>
          </el-card>
        </el-col>
      </el-row>
      <template v-if="pickedTemplate">
        <el-divider content-position="left">规则预览</el-divider>
        <ul class="preview">
          <li v-for="line in previewLines(pickedTemplate.data)" :key="line">{{ line }}</li>
        </ul>
      </template>
      <template #footer>
        <el-button @click="tplVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!picked" :loading="saving" @click="createFromTpl">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑器 -->
    <el-drawer
      v-model="editorVisible"
      :title="editingId ? '编辑交易计划' : '新建交易计划'"
      size="70%"
      :close-on-click-modal="false"
      :before-close="beforeClose"
      destroy-on-close
    >
      <el-form label-width="90px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="14">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" maxlength="50" show-word-limit placeholder="如：我的短线趋势系统" />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item label="风格">
              <el-select v-model="form.style" style="width: 100%">
                <el-option v-for="o in STYLE_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="300" show-word-limit />
        </el-form-item>
        <el-form-item v-if="!editingId" label="设为默认">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>

      <el-tabs type="border-card">
        <el-tab-pane label="选股">
          <RuleList v-model="data.stock_selection.must_have" title="必须满足" field="rule" placeholder="如：市值 > 50亿" />
          <RuleList v-model="data.stock_selection.exclude" title="排除条件" field="rule" placeholder="如：ST 股票" />
        </el-tab-pane>
        <el-tab-pane label="择时">
          <RuleList v-model="data.timing.entry_signals" title="入场信号" field="signal" placeholder="如：MACD 金叉" />
        </el-tab-pane>
        <el-tab-pane label="仓位">
          <el-form label-width="130px" @submit.prevent>
            <el-form-item label="单只上限（%）">
              <el-input-number v-model="maxPerStockPct" :min="1" :max="100" :step="5" />
            </el-form-item>
            <el-form-item label="最多持股（只）">
              <el-input-number v-model="data.position.max_holdings" :min="1" :max="100" />
            </el-form-item>
            <el-form-item label="最少持股（只）">
              <el-input-number v-model="data.position.min_holdings" :min="0" :max="100" />
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="持仓管理">
          <el-form label-width="100px" @submit.prevent>
            <el-form-item label="复盘频率">
              <el-select v-model="data.holding.review_frequency" style="width: 160px">
                <el-option v-for="o in FREQ_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
              </el-select>
            </el-form-item>
          </el-form>
          <RuleList v-model="data.holding.add_conditions" title="加仓条件" field="condition" placeholder="如：盈利 5% 以上且趋势未破坏" />
          <RuleList v-model="data.holding.reduce_conditions" title="减仓条件" field="condition" placeholder="如：跌破 5 日均线" />
        </el-tab-pane>
        <el-tab-pane label="风控">
          <el-form label-width="100px" @submit.prevent>
            <el-divider content-position="left">止损</el-divider>
            <el-form-item label="方式">
              <el-select v-model="data.risk_management.stop_loss.type" allow-create filterable style="width: 180px">
                <el-option v-for="o in EXIT_TYPES" :key="o" :value="o" :label="o" />
              </el-select>
            </el-form-item>
            <el-form-item label="止损比例（%）">
              <el-input-number v-model="stopLossPct" :min="0" :max="100" :step="1" :precision="1" />
            </el-form-item>
            <el-form-item label="说明">
              <el-input v-model="data.risk_management.stop_loss.description" />
            </el-form-item>
            <el-divider content-position="left">止盈</el-divider>
            <el-form-item label="方式">
              <el-select v-model="data.risk_management.take_profit.type" allow-create filterable style="width: 180px">
                <el-option v-for="o in EXIT_TYPES" :key="o" :value="o" :label="o" />
              </el-select>
            </el-form-item>
            <el-form-item label="说明">
              <el-input v-model="data.risk_management.take_profit.description" />
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="复盘">
          <el-form label-width="100px" @submit.prevent>
            <el-form-item label="复盘节奏">
              <el-input v-model="data.review.frequency" placeholder="如：每月一次" />
            </el-form-item>
            <el-form-item label="关注重点">
              <el-input v-model="data.review.focus" type="textarea" :rows="3" placeholder="如：执行纪律与止损及时性" />
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="纪律">
          <RuleList v-model="data.discipline.must_do" title="必须做到" field="rule" placeholder="如：每日收盘后复盘持仓" />
          <RuleList v-model="data.discipline.forbidden" title="严禁操作" field="rule" placeholder="如：情绪化加仓摊平成本" />
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <div class="footer">
          <span v-if="dirty" class="muted">有未保存的修改</span>
          <el-button @click="requestClose">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">{{ editingId ? '保存' : '创建' }}</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Plus, Tickets } from '@element-plus/icons-vue'

import RuleList from '@/components/TradingSystem/RuleList.vue'
import {
  activateTradingSystem,
  createTradingSystem,
  createTradingSystemFromTemplate,
  deleteTradingSystem,
  getTradingSystem,
  getTradingSystemTemplates,
  getTradingSystems,
  updateTradingSystem,
  type TradingSystem,
  type TradingSystemTemplateInfo
} from '@/api/tradingSystem'

const STYLE_OPTIONS = [
  { value: 'short_term', label: '短线' },
  { value: 'medium_term', label: '中线' },
  { value: 'long_term', label: '长线' }
]
const FREQ_OPTIONS = [
  { value: 'daily', label: '每日' }, { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' }, { value: 'quarterly', label: '每季' }
]
const EXIT_TYPES = ['percentage', 'trailing', 'staged', 'technical', 'valuation', 'logic']
const styleLabel = (s: string) => STYLE_OPTIONS.find(o => o.value === s)?.label || s
const riskLabel = (r: string) => (({ conservative: '稳健', balanced: '均衡', aggressive: '激进' } as Record<string, string>)[r] || r)

const systems = ref<TradingSystem[]>([])
const loading = ref(false)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    systems.value = (await getTradingSystems()).data.systems
  } finally {
    loading.value = false
  }
}

async function setDefault(s: TradingSystem) {
  try {
    await activateTradingSystem(s.id!)
    ElMessage.success(`「${s.name}」已设为默认`)
    await load()
  } catch {
    /* 已提示 */
  }
}

async function remove(s: TradingSystem) {
  try {
    await deleteTradingSystem(s.id!)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 已提示 */
  }
}

// ---- 模板 ----
const tplVisible = ref(false)
const templates = ref<TradingSystemTemplateInfo[]>([])
const picked = ref('')
const pickedTemplate = computed(() => templates.value.find(t => t.id === picked.value))

async function openTemplates() {
  picked.value = ''
  tplVisible.value = true
  if (!templates.value.length) templates.value = await getTradingSystemTemplates()
}

function previewLines(d: Record<string, any>): string[] {
  const text = (arr: any[] | undefined) => (arr || []).map(i => (typeof i === 'string' ? i : i.rule || i.signal || i.condition || '')).filter(Boolean)
  const out: string[] = []
  const must = text(d.stock_selection?.must_have)
  if (must.length) out.push(`选股：${must.slice(0, 3).join('；')}${must.length > 3 ? ` 等 ${must.length} 条` : ''}`)
  const entry = text(d.timing?.entry_signals)
  if (entry.length) out.push(`择时：${entry.slice(0, 2).join('；')}${entry.length > 2 ? ` 等 ${entry.length} 条` : ''}`)
  if (d.position) out.push(`仓位：单只不超过 ${Math.round((d.position.max_per_stock || 0) * 100)}%，最多 ${d.position.max_holdings} 只`)
  if (d.risk_management?.stop_loss?.description) out.push(`止损：${d.risk_management.stop_loss.description}`)
  if (d.risk_management?.take_profit?.description) out.push(`止盈：${d.risk_management.take_profit.description}`)
  return out
}

async function createFromTpl() {
  saving.value = true
  try {
    await createTradingSystemFromTemplate(picked.value)
    ElMessage.success('已创建，可以继续按你的习惯调整规则')
    tplVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

// ---- 编辑器 ----
const blankData = () => ({
  stock_selection: { must_have: [] as any[], exclude: [] as any[] },
  timing: { entry_signals: [] as any[] },
  position: { max_per_stock: 0.2 as number, max_holdings: 6 as number | undefined, min_holdings: 0 as number | undefined },
  holding: { review_frequency: 'weekly', add_conditions: [] as any[], reduce_conditions: [] as any[] },
  risk_management: {
    stop_loss: { type: 'percentage', percentage: 0.08 as number | undefined, description: '' },
    take_profit: { type: 'staged', description: '' }
  },
  review: { frequency: '', focus: '' },
  discipline: { must_do: [] as any[], forbidden: [] as any[] }
})

const editorVisible = ref(false)
const editingId = ref('')
const form = reactive({ name: '', description: '', style: 'medium_term', is_active: false })
const data = reactive<ReturnType<typeof blankData>>(blankData())
const snapshot = ref('')

const maxPerStockPct = computed({
  get: () => Math.round((data.position.max_per_stock || 0) * 100),
  set: v => { data.position.max_per_stock = (v || 0) / 100 }
})
const stopLossPct = computed({
  get: () => Math.round((data.risk_management.stop_loss.percentage || 0) * 1000) / 10,
  set: v => { data.risk_management.stop_loss.percentage = (v || 0) / 100 }
})

const current = () => JSON.stringify([form, data])
const dirty = computed(() => current() !== snapshot.value)

/** 把后端文档合并进表单骨架：缺失的分区保持默认，保证每个绑定路径都存在 */
function merge(target: any, src: any) {
  if (!src || typeof src !== 'object') return
  for (const k of Object.keys(src)) {
    if (src[k] && typeof src[k] === 'object' && !Array.isArray(src[k]) && target[k] && typeof target[k] === 'object' && !Array.isArray(target[k])) merge(target[k], src[k])
    else target[k] = src[k]
  }
}

/** 旧数据里的条目可能是纯字符串：统一成 { [field]: 文本, description } 以便编辑 */
function normalizeItems() {
  const fix = (arr: any[], field: string) => arr.map(i => (typeof i === 'string' ? { [field]: i, description: '' } : i))
  data.stock_selection.must_have = fix(data.stock_selection.must_have || [], 'rule')
  data.stock_selection.exclude = fix(data.stock_selection.exclude || [], 'rule')
  data.timing.entry_signals = fix(data.timing.entry_signals || [], 'signal')
  data.holding.add_conditions = fix(data.holding.add_conditions || [], 'condition')
  data.holding.reduce_conditions = fix(data.holding.reduce_conditions || [], 'condition')
  data.discipline.must_do = fix(data.discipline.must_do || [], 'rule')
  data.discipline.forbidden = fix(data.discipline.forbidden || [], 'rule')
}

function resetForm() {
  Object.assign(form, { name: '', description: '', style: 'medium_term', is_active: false })
  Object.assign(data, blankData())
}

function openCreate() {
  editingId.value = ''
  resetForm()
  snapshot.value = current()
  editorVisible.value = true
}

async function openEdit(id: string) {
  try {
    const doc = await getTradingSystem(id)
    resetForm()
    editingId.value = id
    Object.assign(form, { name: doc.name, description: doc.description || '', style: doc.style, is_active: doc.is_active })
    merge(data, doc.data)
    normalizeItems()
    snapshot.value = current()
    editorVisible.value = true
  } catch {
    /* 已提示 */
  }
}

/** 去掉空条目，避免把空规则写进提示词 */
function compact(items: any[], field: string) {
  return items.filter(i => String(i?.[field] || '').trim()).map(i => ({ [field]: String(i[field]).trim(), description: String(i.description || '').trim() }))
}

function buildPayloadData() {
  const d = JSON.parse(JSON.stringify(data))
  d.stock_selection.must_have = compact(d.stock_selection.must_have, 'rule')
  d.stock_selection.exclude = compact(d.stock_selection.exclude, 'rule')
  d.timing.entry_signals = compact(d.timing.entry_signals, 'signal')
  d.holding.add_conditions = compact(d.holding.add_conditions, 'condition')
  d.holding.reduce_conditions = compact(d.holding.reduce_conditions, 'condition')
  d.discipline.must_do = compact(d.discipline.must_do, 'rule')
  d.discipline.forbidden = compact(d.discipline.forbidden, 'rule')
  return d
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写交易计划名称')
    return
  }
  saving.value = true
  try {
    const payload = { name: form.name.trim(), description: form.description, style: form.style, data: buildPayloadData() }
    if (editingId.value) await updateTradingSystem(editingId.value, payload)
    else await createTradingSystem({ ...payload, is_active: form.is_active })
    ElMessage.success('已保存')
    snapshot.value = current()
    editorVisible.value = false
    await load()
  } catch {
    /* 重名 / 校验错误由全局拦截器提示 */
  } finally {
    saving.value = false
  }
}

async function beforeClose(done: () => void) {
  if (!dirty.value) return done()
  try {
    await ElMessageBox.confirm('有未保存的修改，确定放弃吗？', '提示', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' })
    done()
  } catch {
    /* 取消 */
  }
}

function requestClose() {
  beforeClose(() => (editorVisible.value = false))
}

onMounted(load)
</script>

<style scoped lang="scss">
.systems-page {
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
    max-width: 720px;
    font-size: 14px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }
}

.system-card {
  margin-bottom: 16px;

  &.active {
    border-color: var(--el-color-success-light-5);
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .desc {
    min-height: 40px;
    margin: 10px 0 14px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }
}

.tags {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.tpl-card {
  cursor: pointer;
  height: 100%;

  &.picked {
    border-color: var(--el-color-primary);
    box-shadow: 0 0 0 1px var(--el-color-primary) inset;
  }

  .desc {
    margin: 10px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--el-text-color-secondary);
  }
}

.preview {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
}

.footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
