/**
 * 工作流管理页：列表 + 模板/复制创建 + 设默认 + 删除 + 入口到编辑器
 *
 * 社区版重建的工作流管理界面（原 Pro 剔除）：后端 /api/workflows 全套 CRUD
 * 一直可用，本页面提供其管理 UI；编辑跳转 WorkflowEditor。
 */
<template>
  <div class="workflow-list">
    <div class="page-header">
      <div class="header-content">
        <h1 class="page-title">
          <el-icon><SetUp /></el-icon>
          分析流管理
        </h1>
        <p class="page-description">管理分析工作流：从模板创建、复制调整、设为默认。各分析页的「分析流程」下拉即使用这里管理的流程。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" :icon="Plus" @click="showCreateDialog = true">从模板创建</el-button>
        <el-button :icon="Refresh" @click="loadWorkflows">刷新</el-button>
      </div>
    </div>

    <!-- 工作流卡片列表 -->
    <div v-loading="loading" class="workflow-grid">
      <el-empty v-if="!loading && workflows.length === 0" description="暂无工作流，点击右上角「从模板创建」开始" />

      <el-card
        v-for="wf in workflows"
        :key="wf.id"
        shadow="hover"
        class="workflow-card"
        :class="{ 'is-default': wf.is_default }"
      >
        <div class="card-head">
          <span class="name">{{ wf.name }}</span>
          <div class="badges">
            <el-tag v-if="wf.is_default" type="success" size="small">默认</el-tag>
            <el-tag v-if="wf.is_template" type="info" size="small">模板</el-tag>
            <el-tag size="small" type="warning">{{ typeLabel(wf.workflow_type) }}</el-tag>
          </div>
        </div>
        <div class="card-meta">
          <span :title="wf.id" class="id">{{ wf.id }}</span>
          <span class="version">v{{ wf.version }}</span>
        </div>
        <p class="desc">{{ wf.description || '暂无描述' }}</p>
        <div class="card-foot">
          <span class="updated">更新于 {{ formatTime(wf.updated_at) }}</span>
          <div class="actions">
            <el-button text :icon="EditPen" @click="openEditor(wf.id)">编辑</el-button>
            <el-button text :icon="DocumentCopy" @click="openDuplicate(wf)">复制</el-button>
            <el-button
              v-if="!wf.is_default && !wf.is_template"
              text
              :icon="Star"
              @click="setDefault(wf)"
            >设默认</el-button>
            <el-button
              v-if="!wf.is_template && !isProtected(wf.id)"
              text
              :icon="Delete"
              class="danger"
              @click="removeWorkflow(wf)"
            >删除</el-button>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 从模板创建对话框 -->
    <el-dialog v-model="showCreateDialog" title="从模板创建分析流" width="560px">
      <el-form label-position="top">
        <el-form-item label="选择模板" required>
          <el-select v-model="createForm.templateId" placeholder="选择预置模板" style="width: 100%">
            <el-option
              v-for="tpl in templates"
              :key="tpl.id"
              :label="`${tpl.name}（${typeLabel(tpl.workflow_type)}）`"
              :value="tpl.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="新流程名称" required>
          <el-input v-model="createForm.name" placeholder="如：我的分析流 - 消费股版" />
        </el-form-item>
        <p v-if="selectedTemplate" class="tpl-desc">{{ selectedTemplate.description }}</p>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!createForm.templateId || !createForm.name" @click="createFromTemplate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 复制对话框 -->
    <el-dialog v-model="showDuplicateDialog" title="复制分析流" width="440px">
      <el-form label-position="top">
        <el-form-item label="新流程名称" required>
          <el-input v-model="duplicateForm.name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDuplicateDialog = false">取消</el-button>
        <el-button type="primary" :loading="duplicating" :disabled="!duplicateForm.name" @click="doDuplicate">
          复制
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { SetUp, Plus, Refresh, EditPen, DocumentCopy, Delete, Star } from '@element-plus/icons-vue'
import { workflowApi, type WorkflowSummary, type WorkflowDefinition } from '@/api/workflow'

const router = useRouter()
const loading = ref(false)
const workflows = ref<WorkflowSummary[]>([])
const templates = ref<WorkflowDefinition[]>([])

const showCreateDialog = ref(false)
const creating = ref(false)
const createForm = reactive({ templateId: '', name: '' })

const showDuplicateDialog = ref(false)
const duplicating = ref(false)
const duplicateForm = reactive({ sourceId: '', name: '' })

const selectedTemplate = computed(() =>
  templates.value.find(t => t.id === createForm.templateId) || null
)

/** 系统预置流程（registry 内置，删除会破坏默认链路） */
const PROTECTED_IDS = new Set([
  'default_analysis', 'simple_analysis', 'blank_workflow',
  'v2_stock_analysis', 'v2_etf_analysis', 'trade_review', 'trade_review_v2',
  'position_analysis', 'position_analysis_v2',
  'v2_index_analysis', 'sector_rotation_analysis',
])
const isProtected = (id: string) => PROTECTED_IDS.has(id)

const TYPE_LABELS: Record<string, string> = {
  stock_analysis: '股票分析',
  etf_analysis: 'ETF 分析',
  position_analysis: '持仓研究',
  trade_review: '交易复盘',
  portfolio_health: '组合体检',
  general: '通用研究',
}
const typeLabel = (t?: string) => TYPE_LABELS[t || ''] || t || '未分类'

const formatTime = (iso?: string) => {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

const loadWorkflows = async () => {
  loading.value = true
  try {
    const [list, tpls] = await Promise.all([
      workflowApi.listAll(),
      workflowApi.getTemplates().catch(() => [] as WorkflowDefinition[]),
    ])
    workflows.value = list
    templates.value = tpls
  } catch (e: any) {
    ElMessage.error(e?.message || '加载工作流失败')
  } finally {
    loading.value = false
  }
}

const openEditor = (id: string) => {
  router.push(`/workflow/editor?id=${id}`)
}

const createFromTemplate = async () => {
  creating.value = true
  try {
    const res = await workflowApi.createFromTemplate(createForm.templateId, createForm.name)
    if (res.success) {
      ElMessage.success('创建成功，即将打开编辑器')
      showCreateDialog.value = false
      createForm.templateId = ''
      createForm.name = ''
      await loadWorkflows()
      const created = (res.data as any)?.id
      if (created) {
        router.push(`/workflow/editor?id=${created}`)
      }
    } else {
      ElMessage.error((res as any).message || '创建失败')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

const openDuplicate = (wf: WorkflowSummary) => {
  duplicateForm.sourceId = wf.id
  duplicateForm.name = `${wf.name} - 副本`
  showDuplicateDialog.value = true
}

const doDuplicate = async () => {
  duplicating.value = true
  try {
    const res = await workflowApi.duplicate(duplicateForm.sourceId, duplicateForm.name)
    if (res.success) {
      ElMessage.success('复制成功，即将打开编辑器')
      showDuplicateDialog.value = false
      await loadWorkflows()
      const created = (res.data as any)?.id
      if (created) {
        router.push(`/workflow/editor?id=${created}`)
      }
    } else {
      ElMessage.error((res as any).message || '复制失败')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '复制失败')
  } finally {
    duplicating.value = false
  }
}

const setDefault = async (wf: WorkflowSummary) => {
  try {
    const res: any = await workflowApi.setAsDefault(wf.id)
    if (res.success) {
      ElMessage.success(`已将「${wf.name}」设为默认分析流`)
      await loadWorkflows()
    } else {
      ElMessage.error(res.message || '设置失败')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '设置失败')
  }
}

const removeWorkflow = async (wf: WorkflowSummary) => {
  try {
    await ElMessageBox.confirm(
      `确定删除分析流「${wf.name}」吗？此操作不可恢复。`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    const res = await workflowApi.delete(wf.id)
    if ((res as any).success !== false) {
      ElMessage.success('已删除')
      await loadWorkflows()
    } else {
      ElMessage.error((res as any).error || '删除失败')
    }
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e?.message || '删除失败')
    }
  }
}

onMounted(loadWorkflows)
</script>

<style scoped lang="scss">
.workflow-list {
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;

  .page-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 20px;
    margin: 0 0 8px;
  }
  .page-description {
    color: #909399;
    font-size: 13px;
    margin: 0;
    max-width: 600px;
  }
}

.workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  min-height: 200px;
}

.workflow-card {
  &.is-default {
    border-color: var(--el-color-success-light-5);
  }

  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;

    .name {
      font-weight: 600;
      font-size: 15px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .badges {
      display: flex;
      gap: 4px;
      flex-shrink: 0;
    }
  }

  .card-meta {
    display: flex;
    gap: 8px;
    margin-top: 4px;
    font-size: 12px;
    color: #909399;

    .id {
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .desc {
    margin: 10px 0;
    font-size: 13px;
    color: #606266;
    min-height: 38px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .card-foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid #ebeef5;
    padding-top: 10px;

    .updated {
      font-size: 12px;
      color: #c0c4cc;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 2px;

      .danger {
        color: #f56c6c;
      }
    }
  }
}

.tpl-desc {
  font-size: 12px;
  color: #909399;
  margin: 0;
}
</style>
