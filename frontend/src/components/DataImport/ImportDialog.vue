<template>
  <el-dialog
    v-model="visible"
    title="在线导入"
    width="780px"
    top="5vh"
    :close-on-click-modal="false"
    @closed="resetAll"
  >
    <el-form label-width="96px" @submit.prevent>
      <el-form-item label="数据类型">
        <el-select v-if="kindOptions.length > 1" v-model="selectedKey" style="width: 260px" @change="onKindChange">
          <el-option v-for="k in kindOptions" :key="k.key" :label="k.label" :value="k.key" />
        </el-select>
        <el-tag v-else type="primary">{{ kind.label }}</el-tag>
      </el-form-item>

      <el-alert :title="kind.hint" type="info" :closable="false" show-icon class="hint" />

      <el-form-item v-if="kind.symbol !== 'none'" label="股票代码" :required="kind.symbol === 'required'">
        <el-input v-model="symbol" placeholder="如 600519" clearable style="width: 260px" />
        <span class="field-tip">{{ kind.symbol === 'required' ? '必填' : '可选，关联到每条数据' }}</span>
      </el-form-item>

      <el-form-item v-for="e in kind.extra || []" :key="e.key" :label="e.label">
        <el-select v-model="extra[e.key]" style="width: 160px">
          <el-option v-for="o in e.options" :key="o" :label="o" :value="o" />
        </el-select>
      </el-form-item>

      <el-form-item label="覆盖已存在">
        <el-switch v-model="overwrite" />
        <span class="field-tip">{{ overwrite ? '已存在的数据将被更新' : '已存在的数据将被跳过' }}</span>
      </el-form-item>

      <el-form-item label="数据">
        <div class="toolbar">
          <el-button size="small" @click="fillTemplate">填入示例</el-button>
          <el-button size="small" @click="fileInput?.click()">选择文件</el-button>
          <el-button size="small" :disabled="!text.trim()" @click="toPrettyJson">转为 JSON 预览</el-button>
          <el-button size="small" text :disabled="!text" @click="text = ''">清空</el-button>
          <span class="parse-info" :class="parseInfo.state">{{ parseInfo.message }}</span>
          <input ref="fileInput" type="file" accept=".json,.ndjson,.csv,.tsv,.txt" class="file-input" @change="onFile" />
        </div>
        <el-input
          v-model="text"
          type="textarea"
          :rows="13"
          class="mono"
          placeholder="粘贴 JSON 数组、NDJSON（每行一个对象）或 CSV（首行为表头）"
          spellcheck="false"
        />
      </el-form-item>
    </el-form>

    <div v-if="progress.total > 1 && submitting" class="progress">
      <el-progress :percentage="progress.percent" :stroke-width="10" />
      <span>第 {{ progress.done }} / {{ progress.total }} 批</span>
    </div>

    <template v-if="result">
      <el-alert
        :type="result.failed ? 'warning' : 'success'"
        :closable="false"
        show-icon
        :title="resultTitle"
      />
      <el-table v-if="result.errors.length" :data="result.errors" size="small" max-height="200" class="errors">
        <el-table-column label="序号" width="90">
          <template #default="{ row }">{{ row.index === null ? '—' : row.index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="error" label="原因" show-overflow-tooltip />
      </el-table>
      <div v-if="result.errors.length >= 50" class="more-tip">仅显示前 {{ result.errors.length }} 条错误</div>
    </template>

    <template #footer>
      <el-button @click="visible = false">{{ result ? '关闭' : '取消' }}</el-button>
      <el-button type="primary" :loading="submitting" :disabled="!text.trim()" @click="handleSubmit">
        开始导入
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { IMPORT_KINDS, submitImport, type ImportKind, type ImportKindKey } from '@/api/dataImport'
import { chunk, parseImportText } from '@/utils/importParse'

interface AggregatedResult {
  total: number
  saved: number
  updated: number
  skipped: number
  failed: number
  errors: Array<{ index: number | null; error: string }>
}

const props = defineProps<{ modelValue: boolean; kinds: ImportKindKey[] }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'imported', kind: ImportKindKey): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v)
})

const kindOptions = computed(() => IMPORT_KINDS.filter(k => props.kinds.includes(k.key)))
const selectedKey = ref<ImportKindKey>(props.kinds[0])
const kind = computed<ImportKind>(() => IMPORT_KINDS.find(k => k.key === selectedKey.value) || IMPORT_KINDS[0])

const text = ref('')
const symbol = ref('')
const overwrite = ref(false)
const extra = reactive<Record<string, string>>({})
const submitting = ref(false)
const result = ref<AggregatedResult | null>(null)
const progress = reactive({ done: 0, total: 0, percent: 0 })
const fileInput = ref<HTMLInputElement>()

const BATCH_SIZE: Record<ImportKindKey, number> = {
  basic: 500, quotes: 500, financial: 500, news: 500, historical: 5000, social: 1000
}
const MAX_FILE_BYTES = 20 * 1024 * 1024

function applyKindDefaults() {
  overwrite.value = kind.value.overwriteDefault
  for (const k of Object.keys(extra)) delete extra[k]
  for (const e of kind.value.extra || []) extra[e.key] = e.default
}

function onKindChange() {
  text.value = ''
  result.value = null
  applyKindDefaults()
}

watch(
  () => props.modelValue,
  open => {
    if (open) {
      selectedKey.value = props.kinds.includes(selectedKey.value) ? selectedKey.value : props.kinds[0]
      applyKindDefaults()
    }
  },
  { immediate: true }
)

function resetAll() {
  text.value = ''
  symbol.value = ''
  result.value = null
  progress.done = progress.total = progress.percent = 0
}

// ---- 实时解析预览（防抖，避免大文本逐键解析）----
const parseInfo = reactive<{ state: 'empty' | 'ok' | 'error'; message: string }>({ state: 'empty', message: '' })
let parseTimer: ReturnType<typeof setTimeout> | undefined
watch(text, value => {
  clearTimeout(parseTimer)
  parseTimer = setTimeout(() => {
    if (!value.trim()) {
      parseInfo.state = 'empty'
      parseInfo.message = ''
      return
    }
    try {
      const items = parseImportText(value, kind.value.arrayKey)
      parseInfo.state = 'ok'
      parseInfo.message = `已识别 ${items.length} 条`
    } catch (e: any) {
      parseInfo.state = 'error'
      parseInfo.message = e?.message || '解析失败'
    }
  }, 250)
})
onBeforeUnmount(() => clearTimeout(parseTimer))

function fillTemplate() {
  text.value = kind.value.template
}

function toPrettyJson() {
  try {
    text.value = JSON.stringify(parseImportText(text.value, kind.value.arrayKey), null, 2)
  } catch (e: any) {
    ElMessage.error(e?.message || '解析失败')
  }
}

async function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > MAX_FILE_BYTES) {
    ElMessage.warning('文件超过 20MB，请拆分后导入')
    return
  }
  text.value = await file.text()
  ElMessage.success(`已载入 ${file.name}`)
}

const resultTitle = computed(() => {
  const r = result.value
  if (!r) return ''
  return `共 ${r.total} 条：新增 ${r.saved}，更新 ${r.updated}，跳过 ${r.skipped}，失败 ${r.failed}`
})

async function handleSubmit() {
  let items: Record<string, unknown>[]
  try {
    items = parseImportText(text.value, kind.value.arrayKey)
  } catch (e: any) {
    ElMessage.error(e?.message || '数据解析失败')
    return
  }
  if (kind.value.symbol === 'required' && !symbol.value.trim()) {
    ElMessage.warning('请填写股票代码')
    return
  }

  const batches = chunk(items, BATCH_SIZE[kind.value.key])
  const agg: AggregatedResult = { total: items.length, saved: 0, updated: 0, skipped: 0, failed: 0, errors: [] }
  result.value = null
  submitting.value = true
  progress.total = batches.length
  progress.done = 0
  progress.percent = 0

  try {
    let offset = 0
    for (const batch of batches) {
      const res = await submitImport(kind.value, batch, {
        overwrite: overwrite.value,
        symbol: symbol.value,
        extra: { ...extra }
      })
      const r = res.result || {}
      const isValidationReply = r.total !== undefined
      if (!res.ok && !isValidationReply) {
        // 网络 / 鉴权 / 参数错误：后续批次必然同样失败，停止并如实记录
        agg.failed += items.length - offset
        agg.errors.push({ index: offset, error: `第 ${offset + 1}–${offset + batch.length} 条起中止：${res.message}` })
        break
      }
      agg.saved += r.saved || 0
      agg.updated += r.updated || 0
      agg.skipped += r.skipped || 0
      agg.failed += r.failed || 0
      for (const err of r.errors || []) {
        if (agg.errors.length >= 50) break
        agg.errors.push({
          index: typeof err.index === 'number' ? err.index + offset : null,
          error: String(err.error ?? '')
        })
      }
      offset += batch.length
      progress.done += 1
      progress.percent = Math.round((progress.done / progress.total) * 100)
    }
    result.value = agg
    if (agg.failed === 0) {
      ElMessage.success(`导入完成：新增 ${agg.saved}，更新 ${agg.updated}，跳过 ${agg.skipped}`)
    } else {
      ElMessage.warning(`导入完成，但有 ${agg.failed} 条失败，详见下方`)
    }
    if (agg.saved + agg.updated > 0) emit('imported', kind.value.key)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.hint {
  margin: 0 0 16px 96px;
  width: calc(100% - 96px);
}

.field-tip {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  width: 100%;
  margin-bottom: 8px;
}

.file-input {
  display: none;
}

.parse-info {
  margin-left: auto;
  font-size: 12px;

  &.ok {
    color: var(--el-color-success);
  }

  &.error {
    color: var(--el-color-danger);
    max-width: 360px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
}

.progress {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;

  .el-progress {
    flex: 1;
  }
}

.errors {
  margin-top: 8px;
}

.more-tip {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
