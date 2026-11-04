<template>
  <div class="workshop-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><MagicStick /></el-icon>
          Agent 工坊
        </h1>
        <p class="page-description">
          创建专属分析 Agent：描述单一职责 → 检查能力缺口 → 构建版本 → 用真实行情测试 → 评估通过后发布，发布后即可在分析流中使用。
        </p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建 Agent</el-button>
    </div>

    <el-card shadow="never">
      <el-input v-model="keyword" clearable placeholder="按 ID / 名称 / 职责搜索" :prefix-icon="Search" style="width: 320px; margin-bottom: 12px" @keyup.enter="load" @clear="load" />
      <el-table :data="specs" v-loading="loading" stripe empty-text="还没有自定义 Agent，点击右上角“新建 Agent”">
        <el-table-column label="Agent" min-width="220">
          <template #default="{ row }">
            <a class="link" @click="openDetail(row.spec_id)"><strong>{{ row.name }}</strong></a>
            <div class="muted mono">{{ row.spec_id }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="primary_goal" label="职责" min-width="280" show-overflow-tooltip />
        <el-table-column label="最新版本" width="140">
          <template #default="{ row }">
            <template v-if="row.latest_version">
              v{{ row.latest_version.version_number }}
              <el-tag :type="phaseType(row.latest_version.phase)" size="small">{{ row.latest_version.phase_label }}</el-tag>
            </template>
            <span v-else class="muted">未构建</span>
          </template>
        </el-table-column>
        <el-table-column label="运行" width="90">
          <template #default="{ row }">
            <el-tag :type="row.runtime?.enabled ? 'success' : 'info'" size="small" effect="plain">{{ row.runtime?.enabled ? '已启用' : '未启用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本数" width="80" prop="version_count" />
        <el-table-column label="更新" width="150">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.spec_id)">管理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建 -->
    <el-dialog v-model="createVisible" title="新建 Agent" width="680px" top="5vh" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" @submit.prevent>
        <el-form-item label="Agent ID" prop="agent_id">
          <el-input v-model="form.agent_id" placeholder="如 my_valuation_agent" maxlength="48" />
          <div class="help">字母开头，字母/数字/下划线/短横线，3–48 位；创建后不可改。不能与系统内置 Agent 重名。</div>
        </el-form-item>
        <el-form-item label="显示名称" prop="agent_name">
          <el-input v-model="form.agent_name" maxlength="50" show-word-limit placeholder="如 估值分析师" />
        </el-form-item>
        <el-form-item label="单一职责" prop="target_responsibility">
          <el-input v-model="form.target_responsibility" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="一句话说清它只负责什么，如：基于 PE/PB 历史分位判断当前估值高低，并给出风险提示" />
        </el-form-item>
        <el-form-item label="输出字段">
          <el-input v-model="form.output_field" style="width: 260px" />
        </el-form-item>
        <el-form-item label="候选工具">
          <el-select v-model="form.tool_ids" multiple filterable remote :remote-method="searchTools" :loading="toolsLoading" placeholder="可选：搜索并选择要绑定的工具" style="width: 100%">
            <el-option v-for="t in toolOptions" :key="t.tool_id" :value="t.tool_id" :label="t.tool_id">
              <span class="mono">{{ t.tool_id }}</span><span class="muted"> · {{ t.name }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="非目标">
          <el-select v-model="form.non_goals" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入后回车：它明确不做什么" style="width: 100%" />
        </el-form-item>
        <el-form-item label="方法偏好">
          <el-select v-model="form.preferred_methods" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="可选：偏好的分析方法" style="width: 100%" />
        </el-form-item>
        <el-form-item label="约束">
          <el-select v-model="form.constraints" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="可选：必须遵守的约束" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建并进入构建</el-button>
      </template>
    </el-dialog>

    <AgentDetail v-model="detailVisible" :spec-id="detailId" @changed="load" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { MagicStick, Plus, Search } from '@element-plus/icons-vue'

import AgentDetail from '@/components/AgentWorkshop/AgentDetail.vue'
import { createDraft, listAvailableTools, listSpecs, WorkshopError, type ToolInfo, type WorkshopSpec } from '@/api/agentWorkshop'

const specs = ref<WorkshopSpec[]>([])
const keyword = ref('')
const loading = ref(false)

const phaseType = (p: string) => (({ published: 'success', 'publish-ready': 'warning', evaluation: 'danger', testing: 'warning', draft: 'info', archived: 'info' } as Record<string, any>)[p] || 'info')

function formatTime(iso?: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    specs.value = await listSpecs(keyword.value.trim())
  } catch (e) {
    ElMessage.error(e instanceof WorkshopError ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

// ---- 详情 ----
const detailVisible = ref(false)
const detailId = ref<string | null>(null)

function openDetail(id: string) {
  detailId.value = id
  detailVisible.value = true
}

// ---- 新建 ----
const createVisible = ref(false)
const creating = ref(false)
const formRef = ref<FormInstance>()
const toolOptions = ref<ToolInfo[]>([])
const toolsLoading = ref(false)
const blank = () => ({
  agent_id: '', agent_name: '', target_responsibility: '', output_field: 'analysis_report',
  tool_ids: [] as string[], non_goals: [] as string[], preferred_methods: [] as string[], constraints: [] as string[]
})
const form = reactive(blank())
const rules: FormRules = {
  agent_id: [
    { required: true, message: '请填写 Agent ID', trigger: 'blur' },
    { pattern: /^[A-Za-z][A-Za-z0-9_-]{2,47}$/, message: '字母开头，字母/数字/下划线/短横线，3–48 位', trigger: 'blur' }
  ],
  agent_name: [{ required: true, message: '请填写显示名称', trigger: 'blur' }],
  target_responsibility: [{ required: true, message: '请描述它的单一职责', trigger: 'blur' }]
}

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

function openCreate() {
  Object.assign(form, blank())
  createVisible.value = true
  formRef.value?.clearValidate()
  searchTools('')
}

async function create() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  creating.value = true
  try {
    const res = await createDraft({ ...form, agent_id: form.agent_id.trim(), agent_name: form.agent_name.trim(), target_responsibility: form.target_responsibility.trim() })
    ElMessage.success(res.message || '草稿已创建')
    createVisible.value = false
    await load()
    openDetail(res.spec_id)
  } catch (e) {
    ElMessage.error({ message: e instanceof WorkshopError ? e.message : '创建失败', duration: 6000 })
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.workshop-page {
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

.link {
  color: var(--el-color-primary);
  cursor: pointer;
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.help {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
