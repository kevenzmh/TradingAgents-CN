<template>
  <el-dialog
    :model-value="modelValue"
    title="执行轨迹"
    width="860px"
    top="4vh"
    @update:model-value="emit('update:modelValue', $event)"
    @open="loadTraces"
  >
    <div v-loading="loading" class="trace-body">
      <!-- 空态：该任务未开启轨迹采集 -->
      <el-empty
        v-if="!loading && traces.length === 0"
        description="该任务未记录执行轨迹"
      >
        <template #default>
          <p style="margin: 0 0 12px; color: #909399; font-size: 13px">
            提交单股分析时勾选「记录执行轨迹」，轨迹会记录每步分析的 Prompt、LLM 输出与工具调用。
          </p>
        </template>
      </el-empty>

      <template v-else-if="traces.length > 0">
        <!-- 任务级汇总 -->
        <div class="trace-summary">
          <span>共 <b>{{ traces.length }}</b> 步</span>
          <el-divider direction="vertical" />
          <span>LLM 调用 <b>{{ totalLlmCalls }}</b> 次</span>
          <el-divider direction="vertical" />
          <span>Token <b>{{ formatNumber(summary.total_tokens) }}</b></span>
          <el-divider direction="vertical" />
          <span>工具调用 <b>{{ summary.tool_calls_count }}</b> 次</span>
          <el-divider direction="vertical" />
          <span>
            失败节点
            <b :style="summary.failed_count > 0 ? 'color:#f56c6c' : ''">{{ summary.failed_count }}</b>
          </span>
        </div>

        <!-- 轨迹时间线 -->
        <el-timeline class="trace-timeline">
          <el-timeline-item
            v-for="trace in traces"
            :key="trace.trace_id"
            :type="trace.success === false ? 'danger' : 'primary'"
            :timestamp="formatTime(trace.started_at) + ' · ' + formatDuration(trace.execution_duration_ms)"
            placement="top"
          >
            <div class="trace-item" @click="toggleExpand(trace.trace_id)">
              <div class="trace-item-head">
                <span class="agent-name">{{ agentLabel(trace) }}</span>
                <el-tag v-if="trace.success === false" type="danger" size="small">失败</el-tag>
                <el-tag
                  v-if="trace.validation && trace.validation.validation_status"
                  :type="trace.validation.validation_status === 'passed' ? 'success' : 'warning'"
                  size="small"
                >
                  校验{{ trace.validation.validation_status === 'passed' ? '通过' : trace.validation.validation_status }}
                </el-tag>
                <span class="meta">
                  LLM×{{ trace.llm_call_count || 0 }}
                  <template v-if="trace.token_usage && trace.token_usage.total_tokens">
                    · {{ formatNumber(trace.token_usage.total_tokens) }} tokens
                  </template>
                  <template v-if="trace.llm_config && trace.llm_config.model_name">
                    · {{ trace.llm_config.model_name }}
                  </template>
                  <template v-if="trace.tool_calls && trace.tool_calls.length">
                    · 工具×{{ trace.tool_calls.length }}
                  </template>
                </span>
                <el-icon class="expand-icon" :class="{ 'is-open': expanded[trace.trace_id] }">
                  <ArrowDown />
                </el-icon>
              </div>
              <div v-if="trace.error_message" class="trace-error">{{ trace.error_message }}</div>
            </div>

            <!-- 展开的详情 -->
            <div v-if="expanded[trace.trace_id]" class="trace-detail" @click.stop>
              <div v-if="detailLoading[trace.trace_id]" class="detail-loading">
                <el-icon class="is-loading"><Loading /></el-icon> 加载中...
              </div>
              <template v-else-if="details[trace.trace_id]">
                <el-collapse>
                  <el-collapse-item
                    v-if="getPromptText(details[trace.trace_id], 'system')"
                    :title="`System Prompt（${details[trace.trace_id].prompts?.system_prompt_length || 0} 字）`"
                    name="system"
                  >
                    <pre class="trace-pre">{{ getPromptText(details[trace.trace_id], 'system') }}</pre>
                  </el-collapse-item>
                  <el-collapse-item
                    v-if="getPromptText(details[trace.trace_id], 'user')"
                    :title="`User Prompt（${details[trace.trace_id].prompts?.user_prompt_length || 0} 字）`"
                    name="user"
                  >
                    <pre class="trace-pre">{{ getPromptText(details[trace.trace_id], 'user') }}</pre>
                  </el-collapse-item>
                  <el-collapse-item
                    v-if="getOutputText(details[trace.trace_id])"
                    title="LLM 输出"
                    name="output"
                  >
                    <pre class="trace-pre">{{ getOutputText(details[trace.trace_id]) }}</pre>
                  </el-collapse-item>
                  <el-collapse-item
                    v-if="details[trace.trace_id].tool_calls && details[trace.trace_id].tool_calls!.length"
                    :title="`工具调用（${details[trace.trace_id].tool_calls!.length}）`"
                    name="tools"
                  >
                    <div
                      v-for="(call, idx) in details[trace.trace_id].tool_calls"
                      :key="idx"
                      class="tool-call"
                    >
                      <div class="tool-call-head">
                        <el-tag :type="call.success === false ? 'danger' : 'success'" size="small">
                          {{ call.tool_name }}
                        </el-tag>
                        <span v-if="call.duration_ms" class="meta">{{ call.duration_ms }}ms</span>
                      </div>
                      <div v-if="call.error" class="trace-error">{{ call.error }}</div>
                      <pre v-if="call.result_preview" class="trace-pre trace-pre-sm">{{ call.result_preview }}</pre>
                    </div>
                  </el-collapse-item>
                  <el-collapse-item
                    v-if="details[trace.trace_id].inputs && details[trace.trace_id].inputs?.input_keys?.length"
                    title="输入字段"
                    name="inputs"
                  >
                    <div class="input-keys">
                      <el-tag
                        v-for="key in details[trace.trace_id].inputs!.input_keys"
                        :key="key"
                        type="info"
                        size="small"
                        class="input-key"
                      >
                        {{ key }}
                      </el-tag>
                    </div>
                  </el-collapse-item>
                </el-collapse>
              </template>
            </div>
          </el-timeline-item>
        </el-timeline>
      </template>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Loading } from '@element-plus/icons-vue'
import {
  getTaskTraces,
  getTaskTraceDetail,
  type ExecutionTraceSummary,
  type ExecutionTraceDetail,
  type ExecutionTraceListData
} from '@/api/unifiedTasks'

const props = defineProps<{ modelValue: boolean; taskId: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: boolean): void }>()

const loading = ref(false)
const summary = ref<ExecutionTraceListData>({
  task_id: '',
  count: 0,
  total_tokens: 0,
  failed_count: 0,
  tool_calls_count: 0,
  traces: []
})
const traces = computed(() => summary.value.traces)

// 展开状态与详情缓存（trace_id → 详情）
const expanded = reactive<Record<string, boolean>>({})
const details = reactive<Record<string, ExecutionTraceDetail>>({})
const detailLoading = reactive<Record<string, boolean>>({})

const totalLlmCalls = computed(() =>
  traces.value.reduce((sum, t) => sum + (t.llm_call_count || 0), 0)
)

// ==================== 加载 ====================

const loadTraces = async () => {
  if (!props.taskId) return
  loading.value = true
  try {
    const res = await getTaskTraces(props.taskId)
    const data = (res as any)?.data?.data || (res as any)?.data || {}
    summary.value = {
      task_id: props.taskId,
      count: data.count ?? 0,
      total_tokens: data.total_tokens ?? 0,
      failed_count: data.failed_count ?? 0,
      tool_calls_count: data.tool_calls_count ?? 0,
      traces: data.traces ?? []
    }
    // 重置展开状态
    Object.keys(expanded).forEach(k => delete expanded[k])
    Object.keys(details).forEach(k => delete details[k])
  } catch (e: any) {
    ElMessage.error(e?.message || '获取执行轨迹失败')
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (open) => {
  if (open && props.taskId) {
    loadTraces()
  }
})

// ==================== 交互 ====================

const toggleExpand = async (traceId: string) => {
  const next = !expanded[traceId]
  expanded[traceId] = next
  // 首次展开：懒加载详情（含 Prompt/输出全文）
  if (next && !details[traceId] && !detailLoading[traceId]) {
    detailLoading[traceId] = true
    try {
      const res = await getTaskTraceDetail(props.taskId, traceId)
      const data = (res as any)?.data?.data || (res as any)?.data || null
      if (data) {
        details[traceId] = data as ExecutionTraceDetail
      } else {
        ElMessage.error('轨迹详情加载失败')
        expanded[traceId] = false
      }
    } catch (e: any) {
      ElMessage.error(e?.message || '轨迹详情加载失败')
      expanded[traceId] = false
    } finally {
      detailLoading[traceId] = false
    }
  }
}

// ==================== 展示辅助 ====================

/** agent 角色 → 中文标签（engine.py node_mapping 风格的简化版） */
const AGENT_LABELS: Record<string, string> = {
  analyst: '分析师',
  researcher: '研究员',
  trader: '研究整合',
  manager: '决策层',
  post_processor: '后处理'
}

const agentLabel = (trace: ExecutionTraceSummary): string => {
  const typeLabel = AGENT_LABELS[trace.agent_type || ''] || trace.agent_type || 'Agent'
  // agent_id 形如 fundamentals_analyst_v2 / bull_researcher_v2
  const id = trace.agent_id || ''
  const nameMap: Record<string, string> = {
    market: '市场分析师',
    fundamentals: '基本面分析师',
    news: '新闻分析师',
    social: '社媒分析师',
    chip_distribution: '筹码分析师',
    bull: '乐观研究员',
    bear: '审慎研究员',
    risky: '高弹性研究员',
    safe: '防御研究员',
    neutral: '中性研究员',
    research: '研究经理',
    trader: '研究整合员',
    risk: '风险评估师',
    timing: '择时分析师',
    position: '持仓分析师',
    emotion: '情绪分析师',
    attribution: '归因分析师',
    review: '复盘经理'
  }
  const parts = id.replace(/_v\d+$/, '').split('_')
  const known = parts.find(p => nameMap[p])
  const friendly = known ? nameMap[known] : ''
  const prefix = trace.manager_type ? `[${typeLabel}] ` : `${typeLabel} · `
  return prefix + (friendly || id)
}

const formatTime = (iso?: string): string => {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

const formatDuration = (ms?: number | null): string => {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

const formatNumber = (n?: number): string => {
  if (!n) return '0'
  return n.toLocaleString('zh-CN')
}

const getPromptText = (detail: ExecutionTraceDetail, which: 'system' | 'user'): string => {
  return which === 'system'
    ? detail.prompts?.system_prompt_full || ''
    : detail.prompts?.user_prompt_full || ''
}

const getOutputText = (detail: ExecutionTraceDetail): string => {
  return detail.output?.output_raw_full || detail.output?.output_raw_preview || detail.error_message || ''
}
</script>

<style scoped>
.trace-body {
  min-height: 200px;
  max-height: 68vh;
  overflow-y: auto;
  padding-right: 8px;
}

.trace-summary {
  margin-bottom: 16px;
  font-size: 13px;
  color: #606266;
}

.trace-summary b {
  color: #303133;
}

.trace-timeline {
  padding-left: 4px;
}

.trace-item {
  cursor: pointer;
  user-select: none;
}

.trace-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.agent-name {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.expand-icon {
  margin-left: auto;
  color: #909399;
  transition: transform 0.2s;
}

.expand-icon.is-open {
  transform: rotate(180deg);
}

.trace-error {
  margin-top: 6px;
  padding: 6px 10px;
  background: #fef0f0;
  color: #f56c6c;
  border-radius: 4px;
  font-size: 12px;
}

.trace-detail {
  margin-top: 10px;
}

.detail-loading {
  color: #909399;
  font-size: 13px;
  padding: 8px 0;
}

.trace-pre {
  margin: 0;
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
}

.trace-pre-sm {
  max-height: 120px;
}

.tool-call {
  padding: 8px 0;
  border-bottom: 1px dashed #ebeef5;
}

.tool-call:last-child {
  border-bottom: none;
}

.tool-call-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.input-keys {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
