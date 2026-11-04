<template>
  <div class="templates-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><Document /></el-icon>
          提示词模板
        </h1>
        <p class="page-description">
          查看各 Agent 的系统默认提示词，复制后自定义；发布并“设为生效”后，该 Agent 的分析将使用自定义版本（不再区分激进/中性/保守偏好），随时可恢复系统默认。
        </p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建模板</el-button>
    </div>

    <!-- Agent 生效情况 -->
    <el-card shadow="never" class="agents-card" v-loading="agentsLoading">
      <template #header>
        <div class="card-header">
          <span>Agent 生效情况</span>
          <span class="muted">共 {{ agents.length }} 个 Agent，{{ customCount }} 个正在使用自定义模板</span>
        </div>
      </template>
      <div v-if="!agents.length && !agentsLoading" class="muted">暂无 Agent 模板数据</div>
      <div class="agent-chips">
        <el-tag
          v-for="a in agents"
          :key="a.agent_type + '/' + a.agent_name"
          :type="a.using === 'custom' ? 'success' : 'info'"
          :effect="filters.agent_name === a.agent_name && filters.agent_type === a.agent_type ? 'dark' : 'plain'"
          class="agent-chip"
          @click="toggleAgentFilter(a)"
        >
          {{ a.agent_name }}
          <span class="chip-sub">{{ a.using === 'custom' ? '自定义' : '系统默认' }}</span>
          <a v-if="a.using === 'custom'" class="chip-reset" @click.stop="resetAgent(a)">恢复默认</a>
        </el-tag>
      </div>
    </el-card>

    <!-- 列表 -->
    <el-card shadow="never">
      <el-form :inline="true" class="filters" @submit.prevent="search">
        <el-form-item label="来源">
          <el-radio-group v-model="filters.scope" size="small" @change="search">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="system">系统</el-radio-button>
            <el-radio-button value="mine">我的</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部" style="width: 110px" @change="search">
            <el-option label="已发布" value="active" />
            <el-option label="草稿" value="draft" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" clearable placeholder="名称 / Agent / 备注" style="width: 200px" @keyup.enter="search" @clear="search" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="search">查询</el-button>
          <el-button v-if="filters.agent_name" @click="clearAgentFilter">
            取消 Agent 筛选：{{ filters.agent_name }}
          </el-button>
        </el-form-item>
      </el-form>

      <el-table :data="items" v-loading="loading" stripe size="small" empty-text="没有符合条件的模板">
        <el-table-column label="模板名称" min-width="220">
          <template #default="{ row }">
            <a class="link" @click="openView(row)">{{ row.template_name }}</a>
            <el-tag v-if="row.is_active_for_me" type="success" size="small" class="inline-tag">生效中</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Agent" min-width="190">
          <template #default="{ row }">
            <div>{{ row.agent_name }}</div>
            <div class="muted">{{ row.agent_type }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_system ? 'info' : 'primary'" size="small">{{ row.is_system ? '系统' : '我的' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="86">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'warning'" size="small" effect="plain">
              {{ row.status === 'active' ? '已发布' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="70">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="150">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <template v-if="row.is_system">
              <el-button link type="primary" size="small" @click="openView(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="copyAsMine(row)">复制为我的模板</el-button>
            </template>
            <template v-else>
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button v-if="row.status === 'draft'" link type="success" size="small" @click="publish(row)">发布</el-button>
              <template v-else>
                <el-button v-if="!row.is_active_for_me" link type="success" size="small" @click="activate(row)">设为生效</el-button>
                <el-button link type="warning" size="small" @click="unpublish(row)">撤回</el-button>
              </template>
              <el-button link type="primary" size="small" @click="copyAsMine(row)">复制</el-button>
              <el-popconfirm
                :title="row.is_active_for_me ? '该模板正在生效，删除后将恢复系统默认，确认删除？' : '确认删除该模板？'"
                @confirm="remove(row)"
              >
                <template #reference>
                  <el-button link type="danger" size="small">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        class="pager"
        layout="total, sizes, prev, pager, next"
        :page-sizes="[20, 50, 100]"
        :total="total"
        @current-change="loadList"
        @size-change="search"
      />
    </el-card>

    <!-- 新建：先选 Agent -->
    <el-dialog v-model="pickerVisible" title="新建模板：选择 Agent" width="520px">
      <el-form label-width="90px">
        <el-form-item label="Agent">
          <el-select v-model="pickedAgent" filterable placeholder="选择要自定义提示词的 Agent" style="width: 100%">
            <el-option
              v-for="a in agents"
              :key="a.agent_type + '/' + a.agent_name"
              :label="`${a.agent_name}（${a.agent_type}）`"
              :value="a.agent_type + '/' + a.agent_name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="起点">
          <el-radio-group v-model="startFrom">
            <el-radio value="system">基于系统默认模板（推荐）</el-radio>
            <el-radio value="blank">空白模板</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pickerVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!pickedAgent" :loading="pickerLoading" @click="confirmCreate">下一步</el-button>
      </template>
    </el-dialog>

    <TemplateEditor
      v-model="editorVisible"
      :mode="editorMode"
      :template="editorTemplate"
      :agent-type="createAgent.type"
      :agent-name="createAgent.name"
      :base="createBase"
      @saved="onSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Plus, Search } from '@element-plus/icons-vue'

import TemplateEditor from '@/components/PromptTemplates/TemplateEditor.vue'
import {
  activateTemplate,
  deactivateAgent,
  deleteTemplate,
  duplicateTemplate,
  getTemplate,
  listAgents,
  listTemplates,
  publishTemplate,
  unpublishTemplate,
  type AgentRow,
  type PromptTemplate
} from '@/api/promptTemplates'

const items = ref<PromptTemplate[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const agents = ref<AgentRow[]>([])
const agentsLoading = ref(false)

const filters = reactive<{ scope: 'all' | 'system' | 'mine'; status?: 'draft' | 'active'; keyword: string; agent_type?: string; agent_name?: string }>({
  scope: 'all', status: undefined, keyword: '', agent_type: undefined, agent_name: undefined
})

const customCount = computed(() => agents.value.filter(a => a.using === 'custom').length)

function formatTime(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function loadList() {
  loading.value = true
  try {
    const res = await listTemplates({
      scope: filters.scope,
      status: filters.status || undefined,
      keyword: filters.keyword.trim() || undefined,
      agent_type: filters.agent_type,
      agent_name: filters.agent_name,
      skip: (page.value - 1) * pageSize.value,
      limit: pageSize.value
    })
    items.value = res.items
    total.value = res.total
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  agentsLoading.value = true
  try {
    agents.value = await listAgents()
  } finally {
    agentsLoading.value = false
  }
}

function search() {
  page.value = 1
  loadList()
}

const refreshAll = () => Promise.all([loadList(), loadAgents()])

function toggleAgentFilter(a: AgentRow) {
  const same = filters.agent_name === a.agent_name && filters.agent_type === a.agent_type
  filters.agent_type = same ? undefined : a.agent_type
  filters.agent_name = same ? undefined : a.agent_name
  search()
}

function clearAgentFilter() {
  filters.agent_type = filters.agent_name = undefined
  search()
}

// ---- 编辑器 ----
const editorVisible = ref(false)
const editorMode = ref<'create' | 'edit' | 'view'>('view')
const editorTemplate = ref<PromptTemplate | null>(null)
const createAgent = reactive({ type: '', name: '' })
const createBase = ref<PromptTemplate | null>(null)

async function openWith(row: PromptTemplate, mode: 'edit' | 'view') {
  try {
    editorTemplate.value = await getTemplate(row.id)
    editorMode.value = mode
    editorVisible.value = true
  } catch {
    /* 全局拦截器已提示 */
  }
}
const openView = (row: PromptTemplate) => openWith(row, row.is_system ? 'view' : 'edit')
const openEdit = (row: PromptTemplate) => openWith(row, 'edit')

async function onSaved() {
  await refreshAll()
}

// ---- 新建 ----
const pickerVisible = ref(false)
const pickerLoading = ref(false)
const pickedAgent = ref('')
const startFrom = ref<'system' | 'blank'>('system')

function openCreate() {
  pickedAgent.value = filters.agent_type && filters.agent_name ? `${filters.agent_type}/${filters.agent_name}` : ''
  startFrom.value = 'system'
  pickerVisible.value = true
}

async function confirmCreate() {
  const [type, ...rest] = pickedAgent.value.split('/')
  const name = rest.join('/')
  pickerLoading.value = true
  try {
    let base: PromptTemplate | null = null
    if (startFrom.value === 'system') {
      // 系统模板按偏好（激进/中性/保守）有多份，默认以中性为起点
      const res = await listTemplates({ scope: 'system', agent_type: type, agent_name: name, status: 'active', limit: 20 })
      const pick = res.items.find(t => t.preference_type === 'neutral') || res.items[0]
      if (pick) base = await getTemplate(pick.id)
      else ElMessage.info('该 Agent 暂无系统模板，已改为空白模板')
    }
    createAgent.type = type
    createAgent.name = name
    createBase.value = base
    editorTemplate.value = null
    editorMode.value = 'create'
    pickerVisible.value = false
    editorVisible.value = true
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    pickerLoading.value = false
  }
}

// ---- 行操作 ----
async function copyAsMine(row: PromptTemplate) {
  try {
    const copy = await duplicateTemplate(row.id)
    ElMessage.success(`已创建草稿「${copy.template_name}」`)
    await refreshAll()
    await openEdit(copy)
  } catch {
    /* 全局拦截器已提示 */
  }
}

async function publish(row: PromptTemplate) {
  try {
    await publishTemplate(row.id)
    ElMessage.success('已发布，可点击“设为生效”')
    await refreshAll()
  } catch {
    /* 已提示 */
  }
}

async function unpublish(row: PromptTemplate) {
  try {
    await unpublishTemplate(row.id)
    ElMessage.success(row.is_active_for_me ? '已撤回，并恢复该 Agent 的系统默认模板' : '已撤回为草稿')
    await refreshAll()
  } catch {
    /* 已提示 */
  }
}

async function activate(row: PromptTemplate) {
  try {
    await activateTemplate(row.id)
    ElMessage.success(`「${row.agent_name}」已切换为自定义模板，后续分析生效`)
    await refreshAll()
  } catch {
    /* 已提示 */
  }
}

async function remove(row: PromptTemplate) {
  try {
    await deleteTemplate(row.id)
    ElMessage.success('已删除')
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await refreshAll()
  } catch {
    /* 已提示 */
  }
}

async function resetAgent(a: AgentRow) {
  try {
    await ElMessageBox.confirm(`「${a.agent_name}」将恢复使用系统默认提示词（你的自定义模板会保留）。`, '恢复系统默认', {
      type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    await deactivateAgent(a.agent_type, a.agent_name)
    ElMessage.success('已恢复系统默认')
    await refreshAll()
  } catch {
    /* 已提示 */
  }
}

onMounted(refreshAll)
</script>

<style scoped lang="scss">
.templates-page {
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
    font-size: 14px;
    color: var(--el-text-color-secondary);
    max-width: 760px;
    line-height: 1.6;
  }
}

.agents-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.agent-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.agent-chip {
  cursor: pointer;

  .chip-sub {
    margin-left: 6px;
    font-size: 11px;
    opacity: 0.75;
  }

  .chip-reset {
    margin-left: 8px;
    font-size: 11px;
    text-decoration: underline;
  }
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.link {
  color: var(--el-color-primary);
  cursor: pointer;
}

.inline-tag {
  margin-left: 6px;
}

.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
