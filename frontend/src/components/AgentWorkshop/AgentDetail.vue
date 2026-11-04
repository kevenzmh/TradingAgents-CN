<template>
  <el-drawer v-model="visible" :title="spec ? `${spec.name}（${spec.spec_id}）` : 'Agent 详情'" size="72%" destroy-on-close @closed="emit('changed')">
    <div v-loading="loading">
      <template v-if="spec">
        <el-descriptions :column="2" border size="small" class="block">
          <el-descriptions-item label="职责" :span="2">{{ spec.primary_goal }}</el-descriptions-item>
          <el-descriptions-item label="运行状态">
            <el-tag :type="spec.runtime?.enabled ? 'success' : 'info'" size="small">{{ spec.runtime?.enabled ? '已启用' : '未启用' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="当前版本">{{ spec.current_version_id || '—' }}</el-descriptions-item>
          <el-descriptions-item v-if="spec.non_goals?.length" label="非目标" :span="2">{{ spec.non_goals.join('；') }}</el-descriptions-item>
        </el-descriptions>

        <!-- 工作台：围绕当前会话推进 -->
        <el-card shadow="never" class="block">
          <template #header>
            <div class="card-header">
              <span>构建流程</span>
              <span class="muted" v-if="session">会话 {{ session.session_id }} · {{ session.status }}</span>
            </div>
          </template>

          <el-steps :active="activeStep" finish-status="success" align-center class="steps">
            <el-step title="缺口分析" />
            <el-step title="工具计划" />
            <el-step title="构建版本" />
            <el-step title="真数据测试" />
            <el-step title="评估" />
            <el-step title="发布" />
          </el-steps>

          <el-alert v-if="!session" type="info" :closable="false" show-icon title="当前没有进行中的构建会话。可在下方版本列表中选择一个版本“基于反馈迭代”来开启新一轮。" />

          <template v-else>
            <!-- 1. 缺口分析 -->
            <div class="step">
              <div class="step-head">
                <strong>1. 缺口分析</strong>
                <el-button size="small" :loading="busy === 'analyze'" @click="doAnalyze">{{ gap ? '重新分析' : '开始分析' }}</el-button>
              </div>
              <div v-if="gap" class="gap">
                <div>完整度 <el-progress :percentage="Math.round(gap.completeness_score * 100)" :stroke-width="8" style="width: 240px; display: inline-flex" /></div>
                <div v-if="gap.blocking_gaps.length" class="gap-list bad">
                  <div class="gap-title">阻塞缺口（{{ gap.blocking_gaps.length }}）：能力索引里找不到可复用的工具，构建前需要补齐或调整职责</div>
                  <div v-for="g in gap.blocking_gaps" :key="g.gap_id">· {{ g.description }}</div>
                </div>
                <div v-if="gap.non_blocking_gaps.length" class="gap-list ok">
                  <div class="gap-title">已匹配到现有能力（{{ gap.non_blocking_gaps.length }}）</div>
                  <div v-for="g in gap.non_blocking_gaps" :key="g.gap_id">· {{ g.description }}</div>
                </div>
                <div v-if="gap.available_tools.length" class="tools">
                  可用工具：<el-tag v-for="t in gap.available_tools" :key="t" size="small" effect="plain" class="tag">{{ t }}</el-tag>
                </div>
              </div>
            </div>

            <!-- 2. 工具计划 -->
            <div class="step">
              <div class="step-head">
                <strong>2. 工具计划</strong>
                <el-button size="small" :disabled="!gap" :loading="busy === 'plan'" @click="doPlan">生成</el-button>
              </div>
              <div v-if="plan" class="muted">
                方案：{{ plan.plan === 'prompt_only' ? '仅提示词（无工具）' : '工具 + 提示词' }}；
                绑定工具：{{ plan.required_tools?.length ? plan.required_tools.join('、') : '无' }}
              </div>
            </div>

            <!-- 3. 构建 -->
            <div class="step">
              <div class="step-head">
                <strong>3. 构建版本</strong>
                <el-button size="small" type="primary" :loading="busy === 'build'" :disabled="!!session.current_version_id && !canRebuild" @click="doBuild">构建草稿版本</el-button>
              </div>
              <el-alert v-if="buildBlocked.length" type="error" :closable="false" show-icon class="blocked"
                :title="`存在 ${buildBlocked.length} 条未解决的阻塞缺口，无法构建`">
                <div v-for="g in buildBlocked" :key="g">· {{ g }}</div>
                <div class="muted" style="margin-top: 6px">这些能力在能力索引中没有现成工具。可以调整职责描述，或接受缺口——由提示词（模型自身能力）承担，不绑定对应工具。</div>
                <el-button size="small" type="warning" style="margin-top: 8px" :loading="busy === 'ack'" @click="doAcknowledge">接受缺口，仅靠提示词实现</el-button>
              </el-alert>
              <div v-if="session.current_version_id" class="muted">已构建版本：{{ session.current_version_id }}</div>
            </div>
          </template>
        </el-card>

        <!-- 版本 -->
        <el-card shadow="never" class="block">
          <template #header><span>版本（{{ spec.versions.length }}）</span></template>
          <el-empty v-if="!spec.versions.length" description="还没有版本，完成上面的步骤后构建第一个版本" :image-size="60" />
          <div v-for="v in spec.versions" :key="v.version_id" class="version" :class="{ current: v.version_id === spec.current_version_id }">
            <div class="v-head">
              <span>
                <strong>v{{ v.version_number }}</strong>
                <span class="mono vid">{{ v.version_id }}</span>
                <el-tag :type="phaseType(v.phase)" size="small" class="tag">{{ v.phase_label }}</el-tag>
                <el-tag v-if="v.latest_evaluation" :type="v.latest_evaluation.decision === 'pass' ? 'success' : 'warning'" size="small" effect="plain" class="tag">
                  评估：{{ decisionLabel(v.latest_evaluation.decision) }}
                </el-tag>
              </span>
              <span class="v-actions">
                <el-button v-if="canTest(v)" size="small" @click="openTest(v)">真数据测试</el-button>
                <el-button v-if="canEvaluate(v)" size="small" :loading="busy === 'eval:' + v.version_id" @click="doEvaluate(v)">评估</el-button>
                <el-button v-if="v.phase === 'publish-ready'" size="small" type="success" :loading="busy === 'pub:' + v.version_id" @click="doPublish(v)">发布</el-button>
                <el-button v-if="v.phase !== 'archived'" size="small" @click="openIterate(v)">基于反馈迭代</el-button>
                <el-button v-if="v.phase !== 'archived'" size="small" @click="openTools(v)">工具绑定</el-button>
                <el-popconfirm :title="v.phase === 'published' ? '该版本已发布并在使用，强制删除将使 Agent 不可用，确认？' : '确认删除该版本？'" @confirm="doDelete(v)">
                  <template #reference><el-button size="small" link type="danger">删除</el-button></template>
                </el-popconfirm>
              </span>
            </div>
            <div class="muted">工具：{{ v.required_tools?.length ? v.required_tools.join('、') : '无' }} · 输出字段：{{ v.output_field || '—' }}</div>
            <el-collapse v-if="v.last_debug_report" class="report-collapse">
              <el-collapse-item title="最近一次测试输出">
                <pre class="report">{{ v.last_debug_report }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-card>
      </template>
    </div>

    <!-- 测试 -->
    <el-dialog v-model="testVisible" title="真数据测试" width="560px" :close-on-click-modal="!testing">
      <el-alert type="warning" :closable="false" show-icon title="将用系统默认推理模型对真实行情数据运行一次该 Agent，会消耗模型额度，通常需要 1–3 分钟。" style="margin-bottom: 14px" />
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="股票代码"><el-input v-model="testForm.symbol" placeholder="如 600519" style="width: 200px" /></el-form-item>
        <el-form-item label="市场">
          <el-select v-model="testForm.market_type" style="width: 140px">
            <el-option v-for="m in ['A股', '港股', '美股']" :key="m" :value="m" :label="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="分析日期"><el-date-picker v-model="testForm.analysis_date" type="date" value-format="YYYY-MM-DD" placeholder="默认今天" style="width: 200px" /></el-form-item>
        <el-form-item label="测试目标"><el-input v-model="testForm.user_goal" type="textarea" :rows="2" placeholder="可选：本次希望它重点回答什么" /></el-form-item>
      </el-form>
      <template v-if="testResult">
        <el-divider content-position="left">输出（{{ testResult.report_length }} 字）</el-divider>
        <pre class="report">{{ testResult.report || '（空输出）' }}</pre>
      </template>
      <template #footer>
        <el-button :disabled="testing" @click="testVisible = false">关闭</el-button>
        <el-button type="primary" :loading="testing" :disabled="!testForm.symbol.trim()" @click="doTest">开始测试</el-button>
      </template>
    </el-dialog>

    <!-- 迭代 -->
    <el-dialog v-model="iterVisible" title="基于反馈迭代" width="520px">
      <el-input v-model="iterFeedback" type="textarea" :rows="4" placeholder="说明哪里不满意、希望怎么改（会带入新一轮会话）" />
      <template #footer>
        <el-button @click="iterVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy === 'iter'" @click="doIterate">开启新一轮</el-button>
      </template>
    </el-dialog>

    <!-- 工具绑定 -->
    <el-dialog v-model="toolsVisible" title="工具绑定" width="620px">
      <el-select v-model="toolsSelected" multiple filterable remote :remote-method="searchTools" :loading="toolsLoading" placeholder="搜索并选择工具" style="width: 100%">
        <el-option v-for="t in toolOptions" :key="t.tool_id" :value="t.tool_id" :label="t.tool_id">
          <span class="mono">{{ t.tool_id }}</span><span class="muted"> · {{ t.name }}</span>
        </el-option>
      </el-select>
      <div class="muted" style="margin-top: 8px">保存后写入该版本与运行时配置；建议保存后重新测试。</div>
      <template #footer>
        <el-button @click="toolsVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy === 'tools'" @click="doSyncTools">保存</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  acknowledgeGaps, analyzeGaps, buildVersion, deleteVersion, evaluateVersion, getSession, getSpec, iterateVersion, listAvailableTools,
  publishVersion, syncTools, testVersion, toolingPlan, WorkshopError,
  type GapReport, type ToolInfo, type WorkshopSession, type WorkshopSpec, type WorkshopVersion
} from '@/api/agentWorkshop'

const props = defineProps<{ modelValue: boolean; specId: string | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'changed'): void }>()
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })

const spec = ref<WorkshopSpec | null>(null)
const session = ref<WorkshopSession | null>(null)
const loading = ref(false)
const busy = ref('')
const buildBlocked = ref<string[]>([])

const gap = computed<GapReport | null>(() => session.value?.gap_report || null)
const plan = computed(() => session.value?.builder_contracts?.tooling_plan || null)
// 已有 draft 版本且尚未测试时，允许重新构建会产生第二个版本，这里只在没有版本或上一版已进入评估阶段后放开
const canRebuild = computed(() => !spec.value?.versions.some(v => v.phase === 'draft'))

const activeStep = computed(() => {
  const v = spec.value?.versions[0]
  if (v?.phase === 'published') return 6
  if (v?.phase === 'publish-ready') return 5
  if (v && ['evaluation', 'testing'].includes(v.phase)) return 4
  if (session.value?.current_version_id) return 3
  if (plan.value) return 2
  if (gap.value) return 1
  return 0
})

const phaseType = (p: string) => (({ published: 'success', 'publish-ready': 'warning', evaluation: 'danger', testing: 'warning', draft: 'info', archived: 'info' } as Record<string, any>)[p] || 'info')
const decisionLabel = (d: string) => (({ pass: '通过', needs_iteration: '需迭代', fail: '未通过' } as Record<string, string>)[d] || d)
const canTest = (v: WorkshopVersion) => ['draft', 'testing', 'evaluation', 'publish-ready'].includes(v.phase)
const canEvaluate = (v: WorkshopVersion) => ['evaluation', 'testing'].includes(v.phase) && !!v.last_debug_report

function fail(e: unknown, fallback = '操作失败') {
  ElMessage.error({ message: e instanceof WorkshopError ? e.message : fallback, duration: 6000 })
}

async function load() {
  if (!props.specId) return
  loading.value = true
  try {
    spec.value = await getSpec(props.specId)
    const active = (spec.value.sessions || []).find(s => !['published', 'abandoned'].includes(s.status))
    session.value = active ? await getSession(active.session_id) : null
    buildBlocked.value = []
  } catch (e) {
    fail(e, '加载失败')
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, open => open && load())

async function run(key: string, fn: () => Promise<unknown>, okMsg?: string) {
  busy.value = key
  try {
    await fn()
    if (okMsg) ElMessage.success(okMsg)
    await load()
  } catch (e) {
    fail(e)
  } finally {
    busy.value = ''
  }
}

const doAnalyze = () => run('analyze', () => analyzeGaps(session.value!.session_id))
const doPlan = () => run('plan', () => toolingPlan(session.value!.session_id))

async function doBuild() {
  busy.value = 'build'
  buildBlocked.value = []
  try {
    await buildVersion(session.value!.session_id)
    ElMessage.success('草稿版本已构建，可以开始真数据测试')
    await load()
  } catch (e) {
    if (e instanceof WorkshopError && e.status === 409) buildBlocked.value = e.detail?.blocking_gaps || [e.message]
    else fail(e)
  } finally {
    busy.value = ''
  }
}

async function doAcknowledge() {
  busy.value = 'ack'
  try {
    await acknowledgeGaps(session.value!.session_id)
    buildBlocked.value = []
    ElMessage.success('已接受缺口，现在可以构建')
    await load()
  } catch (e) {
    fail(e)
  } finally {
    busy.value = ''
  }
}

// ---- 测试 ----
const testVisible = ref(false)
const testing = ref(false)
const testTarget = ref<WorkshopVersion | null>(null)
const testResult = ref<{ report: string; report_length: number } | null>(null)
const testForm = reactive({ symbol: '', market_type: 'A股', analysis_date: '', user_goal: '' })

function openTest(v: WorkshopVersion) {
  testTarget.value = v
  testResult.value = null
  testVisible.value = true
}

async function doTest() {
  testing.value = true
  testResult.value = null
  try {
    const res = await testVersion(testTarget.value!.version_id, {
      symbol: testForm.symbol.trim(), market_type: testForm.market_type,
      analysis_date: testForm.analysis_date || undefined, user_goal: testForm.user_goal || undefined
    })
    testResult.value = res
    ElMessage[res.success ? 'success' : 'warning'](res.success ? '测试完成，请查看输出后点击“评估”' : '测试完成但输出为空')
    await load()
  } catch (e) {
    fail(e, '测试失败')
  } finally {
    testing.value = false
  }
}

async function doEvaluate(v: WorkshopVersion) {
  busy.value = 'eval:' + v.version_id
  try {
    const res = await evaluateVersion(v.version_id)
    ElMessage[res.decision === 'pass' ? 'success' : 'warning'](res.decision === 'pass' ? '评估通过，可以发布' : '评估未通过：输出过短或为空，请调整后重新测试')
    await load()
  } catch (e) {
    fail(e)
  } finally {
    busy.value = ''
  }
}

async function doPublish(v: WorkshopVersion) {
  try {
    await ElMessageBox.confirm('发布后该 Agent 立即可在分析流中使用。确认发布？', '发布', { type: 'warning', confirmButtonText: '发布', cancelButtonText: '取消' })
  } catch {
    return
  }
  await run('pub:' + v.version_id, () => publishVersion(v.version_id), '已发布')
}

async function doDelete(v: WorkshopVersion) {
  await run('del', () => deleteVersion(v.version_id, v.phase === 'published' || v.phase === 'publish-ready'), '已删除')
}

// ---- 迭代 ----
const iterVisible = ref(false)
const iterFeedback = ref('')
const iterTarget = ref<WorkshopVersion | null>(null)

function openIterate(v: WorkshopVersion) {
  iterTarget.value = v
  iterFeedback.value = ''
  iterVisible.value = true
}

async function doIterate() {
  await run('iter', () => iterateVersion(iterTarget.value!.version_id, iterFeedback.value), '已开启新一轮，请从“缺口分析”继续')
  iterVisible.value = false
}

// ---- 工具绑定 ----
const toolsVisible = ref(false)
const toolsTarget = ref<WorkshopVersion | null>(null)
const toolsSelected = ref<string[]>([])
const toolOptions = ref<ToolInfo[]>([])
const toolsLoading = ref(false)

async function searchTools(q: string) {
  toolsLoading.value = true
  try {
    toolOptions.value = (await listAvailableTools(q)).slice(0, 80)
  } catch {
    toolOptions.value = []
  } finally {
    toolsLoading.value = false
  }
}

async function openTools(v: WorkshopVersion) {
  toolsTarget.value = v
  toolsSelected.value = [...(v.required_tools || [])]
  await searchTools('')
  // 已选工具即使不在前 80 条里也要有选项，否则标签会显示成 ID 之外的空白
  for (const id of toolsSelected.value) {
    if (!toolOptions.value.some(t => t.tool_id === id)) toolOptions.value.push({ tool_id: id, name: '', description: '', category: '' })
  }
  toolsVisible.value = true
}

async function doSyncTools() {
  await run('tools', () => syncTools(toolsTarget.value!.version_id, toolsSelected.value), '工具绑定已保存')
  toolsVisible.value = false
}
</script>

<style scoped lang="scss">
.block {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.steps {
  margin-bottom: 20px;
}

.step {
  padding: 12px 0;
  border-top: 1px dashed var(--el-border-color-lighter);

  .step-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
}

.gap {
  font-size: 13px;
  line-height: 1.8;

  .gap-list {
    margin-top: 8px;
    padding: 8px 12px;
    border-radius: 6px;

    &.bad {
      background: var(--el-color-danger-light-9);
    }

    &.ok {
      background: var(--el-color-success-light-9);
    }

    .gap-title {
      font-weight: 600;
    }
  }

  .tools {
    margin-top: 8px;
  }
}

.tag {
  margin: 0 4px 2px;
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.vid {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
}

.version {
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;

  &.current {
    border-color: var(--el-color-success-light-5);
  }

  .v-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 4px;
  }
}

.report-collapse {
  margin-top: 6px;
}

.report {
  margin: 0;
  padding: 10px 12px;
  max-height: 320px;
  overflow: auto;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.blocked {
  margin: 8px 0;
}
</style>
