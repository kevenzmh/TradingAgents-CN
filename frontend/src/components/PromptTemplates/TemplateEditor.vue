<template>
  <el-drawer
    v-model="visible"
    :title="title"
    size="72%"
    :close-on-click-modal="false"
    :before-close="beforeClose"
    destroy-on-close
  >
    <el-form label-width="84px" @submit.prevent>
      <el-row :gutter="16">
        <el-col :span="14">
          <el-form-item label="模板名称" required>
            <el-input v-model="name" :disabled="readonly" maxlength="100" show-word-limit placeholder="如：保守型估值-市场分析师" />
          </el-form-item>
        </el-col>
        <el-col :span="10">
          <el-form-item label="适用 Agent">
            <el-tag>{{ agentType }}</el-tag>
            <el-tag type="info" style="margin-left: 6px">{{ agentName }}</el-tag>
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="备注">
        <el-input v-model="remark" :disabled="readonly" maxlength="500" show-word-limit placeholder="可选：用途、修改要点" />
      </el-form-item>
      <el-alert
        v-if="readonly && template?.is_system"
        type="info"
        :closable="false"
        show-icon
        title="系统模板为只读。点击右上角“复制为我的模板”后即可修改。"
        style="margin-bottom: 12px"
      />
      <el-alert
        v-else-if="mode === 'create' && baseName"
        type="info"
        :closable="false"
        show-icon
        :title="`已载入「${baseName}」作为起点，修改后保存为你自己的草稿模板。`"
        style="margin-bottom: 12px"
      />
    </el-form>

    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane v-for="f in CONTENT_FIELDS" :key="f.key" :name="f.key">
        <template #label>
          {{ f.label }}
          <span v-if="content[f.key]" class="dot" />
        </template>
        <div class="field-hint">{{ f.hint }}</div>
        <el-input
          v-model="content[f.key]"
          type="textarea"
          :rows="f.rows"
          :disabled="readonly"
          class="mono"
          spellcheck="false"
          :maxlength="50000"
          show-word-limit
        />
      </el-tab-pane>

      <el-tab-pane name="preview" label="变量与预览">
        <div class="field-hint">
          模板中的 <code v-text="'{变量}'"></code> 与 <code v-text="'{{变量.路径}}'"></code> 会在运行时替换；缺失的变量渲染为空。
        </div>
        <div class="vars">
          <span class="label">检测到的变量：</span>
          <el-tag v-for="v in variables" :key="v" size="small" effect="plain" class="var-tag">{{ v }}</el-tag>
          <span v-if="!variables.length" class="muted">无</span>
        </div>
        <div class="preview-toolbar">
          <span class="label">示例变量（JSON）</span>
          <el-button size="small" @click="buildSkeleton">按变量生成骨架</el-button>
          <el-button size="small" type="primary" :loading="previewing" @click="runPreview">渲染预览</el-button>
        </div>
        <el-input v-model="varsText" type="textarea" :rows="6" class="mono" spellcheck="false" />

        <template v-if="preview">
          <el-alert
            v-if="preview.empty_variables.length"
            type="warning"
            :closable="false"
            show-icon
            style="margin-top: 12px"
            :title="`以下变量在示例中为空，运行时若同样缺失将渲染为空：${preview.empty_variables.join('、')}`"
          />
          <div v-for="f in CONTENT_FIELDS" v-show="preview.rendered[f.key]" :key="f.key" class="rendered">
            <div class="rendered-title">{{ f.label }}</div>
            <pre>{{ preview.rendered[f.key] }}</pre>
          </div>
        </template>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <div class="footer">
        <span v-if="dirty && !readonly" class="muted">有未保存的修改</span>
        <el-button @click="requestClose">{{ readonly ? '关闭' : '取消' }}</el-button>
        <el-button v-if="!readonly" type="primary" :loading="saving" :disabled="!dirty && mode === 'edit'" @click="save">
          {{ mode === 'create' ? '创建草稿' : '保存' }}
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  CONTENT_FIELDS,
  createTemplate,
  emptyContent,
  extractVariables,
  previewTemplate,
  updateTemplate,
  type PreviewResult,
  type PromptTemplate,
  type TemplateContent
} from '@/api/promptTemplates'

type Mode = 'create' | 'edit' | 'view'

const props = defineProps<{
  modelValue: boolean
  mode: Mode
  /** edit / view：目标模板（含 content） */
  template?: PromptTemplate | null
  /** create：目标 Agent，以及可选的起点模板 */
  agentType?: string
  agentName?: string
  base?: PromptTemplate | null
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved', t: PromptTemplate): void }>()

const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const readonly = computed(() => props.mode === 'view' || !!props.template?.is_system)

const agentType = computed(() => props.template?.agent_type || props.agentType || '')
const agentName = computed(() => props.template?.agent_name || props.agentName || '')
const baseName = computed(() => props.base?.template_name || '')
const title = computed(() =>
  props.mode === 'create' ? '新建提示词模板' : readonly.value ? `查看模板 · ${props.template?.template_name}` : `编辑模板 · ${props.template?.template_name}`
)

const name = ref('')
const remark = ref('')
const content = reactive<TemplateContent>(emptyContent())
const activeTab = ref<string>('system_prompt')
const saving = ref(false)
const previewing = ref(false)
const preview = ref<PreviewResult | null>(null)
const varsText = ref('{}')
const snapshot = ref('')

const variables = computed(() => extractVariables(content))
const current = () => JSON.stringify([name.value, remark.value, content])
const dirty = computed(() => current() !== snapshot.value)

function load() {
  const src = props.mode === 'create' ? props.base : props.template
  name.value = props.mode === 'create' ? (props.base ? `${props.base.template_name}（自定义）` : '') : src?.template_name || ''
  remark.value = props.mode === 'create' ? '' : src?.remark || ''
  Object.assign(content, emptyContent(), src?.content || {})
  activeTab.value = 'system_prompt'
  preview.value = null
  varsText.value = '{}'
  snapshot.value = current()
}

watch(() => props.modelValue, open => open && load(), { immediate: true })

function buildSkeleton() {
  const obj: Record<string, any> = {}
  for (const v of variables.value) {
    const parts = v.split('.')
    let cur = obj
    parts.forEach((p, i) => {
      if (i === parts.length - 1) cur[p] = cur[p] ?? ''
      else cur = typeof cur[p] === 'object' && cur[p] ? cur[p] : (cur[p] = {})
    })
  }
  varsText.value = JSON.stringify(obj, null, 2)
}

async function runPreview() {
  let vars: Record<string, unknown>
  try {
    vars = JSON.parse(varsText.value || '{}')
    if (typeof vars !== 'object' || vars === null || Array.isArray(vars)) throw new Error('必须是 JSON 对象')
  } catch (e: any) {
    ElMessage.error(`示例变量不是有效的 JSON 对象：${e.message}`)
    return
  }
  previewing.value = true
  try {
    // 用编辑中的内容预览（不落库）；至少要有一个提示词才合法
    preview.value = await previewTemplate({ content: { ...content }, variables: vars })
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    previewing.value = false
  }
}

async function save() {
  if (!name.value.trim()) {
    ElMessage.warning('请填写模板名称')
    return
  }
  if (!content.system_prompt.trim() && !content.user_prompt.trim()) {
    ElMessage.warning('系统提示词与用户提示词至少填写一个')
    return
  }
  saving.value = true
  try {
    const saved =
      props.mode === 'create'
        ? await createTemplate({
            agent_type: agentType.value,
            agent_name: agentName.value,
            template_name: name.value,
            content: { ...content },
            remark: remark.value,
            base_template_id: props.base?.id || null
          })
        : await updateTemplate(props.template!.id, {
            template_name: name.value,
            content: { ...content },
            remark: remark.value
          })
    ElMessage.success(props.mode === 'create' ? '已创建草稿，发布并设为生效后才会被 Agent 使用' : '已保存')
    snapshot.value = current()
    emit('saved', saved)
    visible.value = false
  } catch {
    /* 全局拦截器已提示（含 409 同名/并发冲突） */
  } finally {
    saving.value = false
  }
}

async function beforeClose(done: () => void) {
  if (!dirty.value || readonly.value) return done()
  try {
    await ElMessageBox.confirm('有未保存的修改，确定放弃吗？', '提示', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' })
    done()
  } catch {
    /* 取消 */
  }
}

function requestClose() {
  beforeClose(() => (visible.value = false))
}
</script>

<style scoped lang="scss">
.dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--el-color-primary);
  vertical-align: middle;
}

.field-hint {
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;

  code {
    background: var(--el-fill-color);
    padding: 1px 5px;
    border-radius: 3px;
  }
}

.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}

.vars {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.label {
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;

  .label {
    margin-right: auto;
  }
}

.rendered {
  margin-top: 14px;

  .rendered-title {
    font-weight: 600;
    margin-bottom: 4px;
  }

  pre {
    margin: 0;
    padding: 10px 12px;
    border: 1px solid var(--el-border-color-lighter);
    border-radius: 6px;
    background: var(--el-fill-color-light);
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 12.5px;
    line-height: 1.6;
    max-height: 320px;
    overflow: auto;
  }
}

.footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}
</style>
