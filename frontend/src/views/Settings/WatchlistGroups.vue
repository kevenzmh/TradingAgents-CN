<template>
  <div class="groups-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><FolderOpened /></el-icon>
          自选股分组
        </h1>
        <p class="page-description">
          把关注的股票按主题分组，定时分析会按分组批量创建分析任务。分组与「股票关注列表」相互独立，也可以从关注列表一键导入。
        </p>
      </div>
      <el-button type="primary" :icon="Plus" :disabled="groups.length >= maxGroups" @click="openCreate">新建分组</el-button>
    </div>

    <el-empty v-if="!loading && !groups.length" description="还没有分组，点击右上角“新建分组”" />

    <el-row v-loading="loading" :gutter="16">
      <el-col v-for="(g, idx) in groups" :key="g.id" :xs="24" :md="12" :xl="8">
        <el-card class="group-card" shadow="hover" :class="{ inactive: !g.is_active }">
          <template #header>
            <div class="card-header">
              <span class="title">
                <i class="dot" :style="{ background: g.color }" />
                <strong>{{ g.name }}</strong>
                <el-tag size="small" type="info" class="count">{{ g.stock_count }} 只</el-tag>
                <el-tag v-if="g.analysis_depth" size="small" effect="plain" class="count">深度 {{ g.analysis_depth }}</el-tag>
              </span>
              <span class="ops">
                <el-button link size="small" :disabled="idx === 0" :icon="ArrowUp" @click="move(idx, -1)" />
                <el-button link size="small" :disabled="idx === groups.length - 1" :icon="ArrowDown" @click="move(idx, 1)" />
                <el-switch :model-value="g.is_active" size="small" @change="(v: any) => setActive(g, !!v)" />
              </span>
            </div>
          </template>

          <p v-if="g.description" class="desc">{{ g.description }}</p>
          <div class="stocks">
            <el-tag
              v-for="s in g.stocks"
              :key="s.code"
              closable
              size="small"
              effect="plain"
              class="stock-tag"
              @close="removeOne(g, s.code)"
            >
              {{ s.name ? `${s.name} ` : '' }}<span class="code">{{ s.code }}</span>
            </el-tag>
            <span v-if="!g.stocks.length" class="muted">暂无股票</span>
          </div>
          <div class="actions">
            <el-button size="small" :icon="Plus" @click="openAdd(g)">添加股票</el-button>
            <el-button size="small" @click="openImport(g)">从自选股导入</el-button>
            <el-button size="small" link type="primary" @click="openEdit(g)">编辑</el-button>
            <el-popconfirm :title="`确认删除分组「${g.name}」？（不会删除股票本身）`" @confirm="remove(g)">
              <template #reference>
                <el-button size="small" link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="formVisible" :title="editing ? '编辑分组' : '新建分组'" width="540px" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px" @submit.prevent>
        <el-form-item label="分组名称" prop="name">
          <el-input v-model="form.name" maxlength="30" show-word-limit placeholder="如：银行股、待研究" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="200" show-word-limit />
        </el-form-item>
        <el-form-item label="颜色">
          <el-color-picker v-model="form.color" :predefine="PALETTE" />
        </el-form-item>
        <el-form-item label="分析深度">
          <el-select v-model="form.analysis_depth" style="width: 200px">
            <el-option :value="0" label="跟随定时分析默认" />
            <el-option v-for="d in 5" :key="d" :value="d" :label="`${d} - ${DEPTH_LABELS[d - 1]}`" />
          </el-select>
          <span class="help">优先级：时间段设置 &gt; 分组设置 &gt; 配置默认</span>
        </el-form-item>
        <el-form-item v-if="!editing" label="初始股票">
          <el-input v-model="form.codesText" type="textarea" :rows="3" placeholder="可选。股票代码，用逗号、空格或换行分隔，如 600519, 000001" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- 添加股票 -->
    <el-dialog v-model="addVisible" :title="`添加股票到「${target?.name}」`" width="480px">
      <el-input v-model="addText" type="textarea" :rows="4" placeholder="股票代码，用逗号、空格或换行分隔，如 600519 000001.SZ" />
      <div class="help" style="margin-top: 8px">单个分组最多 {{ maxStocks }} 只；A 股的 .SH/.SZ 后缀会自动去掉。</div>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!addText.trim()" @click="confirmAdd">添加</el-button>
      </template>
    </el-dialog>

    <!-- 从自选股导入 -->
    <el-dialog v-model="importVisible" :title="`从自选股导入到「${target?.name}」`" width="440px">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="按标签">
          <el-input v-model="importTag" clearable placeholder="留空导入全部自选股；填标签名只导入该标签" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { ArrowDown, ArrowUp, FolderOpened, Plus } from '@element-plus/icons-vue'

import {
  addStocks,
  createGroup,
  deleteGroup,
  importFavorites,
  listGroups,
  removeStocks,
  reorderGroups,
  updateGroup,
  type AddResult,
  type WatchlistGroup
} from '@/api/watchlistGroups'

const PALETTE = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399', '#8E44AD', '#16A085', '#D35400']
const DEPTH_LABELS = ['快速', '基础', '标准', '深度', '全面']

const groups = ref<WatchlistGroup[]>([])
const maxGroups = ref(50)
const maxStocks = ref(100)
const loading = ref(false)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await listGroups()
    groups.value = res.items
    maxGroups.value = res.max_groups
    maxStocks.value = res.max_stocks
  } finally {
    loading.value = false
  }
}

const parseCodes = (text: string) => text.split(/[\s,，;；]+/).map(s => s.trim()).filter(Boolean)

function reportAdd(res: AddResult) {
  const parts = [`新增 ${res.added.length} 只`]
  if (res.skipped.length) parts.push(`已存在 ${res.skipped.length} 只`)
  if (res.invalid.length) parts.push(`无效 ${res.invalid.length} 个（${res.invalid.slice(0, 3).join('、')}）`)
  ;(res.invalid.length ? ElMessage.warning : ElMessage.success)(parts.join('，'))
}

// ---- 新建 / 编辑 ----
const formVisible = ref(false)
const editing = ref<WatchlistGroup | null>(null)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', description: '', color: '#409EFF', analysis_depth: 0, codesText: '' })
const rules: FormRules = { name: [{ required: true, message: '请填写分组名称', trigger: 'blur' }] }

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', color: '#409EFF', analysis_depth: 0, codesText: '' })
  formVisible.value = true
  formRef.value?.clearValidate()
}

function openEdit(g: WatchlistGroup) {
  editing.value = g
  Object.assign(form, { name: g.name, description: g.description, color: g.color, analysis_depth: g.analysis_depth || 0, codesText: '' })
  formVisible.value = true
  formRef.value?.clearValidate()
}

async function save() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    const base = { name: form.name.trim(), description: form.description, color: form.color || '#409EFF', analysis_depth: form.analysis_depth }
    if (editing.value) {
      await updateGroup(editing.value.id, base)
      ElMessage.success('已保存')
    } else {
      await createGroup({ ...base, analysis_depth: form.analysis_depth || null, stock_codes: parseCodes(form.codesText) })
      ElMessage.success('已创建')
    }
    formVisible.value = false
    await load()
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// ---- 股票 ----
const target = ref<WatchlistGroup | null>(null)
const addVisible = ref(false)
const addText = ref('')
const importVisible = ref(false)
const importTag = ref('')

function openAdd(g: WatchlistGroup) {
  target.value = g
  addText.value = ''
  addVisible.value = true
}

async function confirmAdd() {
  saving.value = true
  try {
    reportAdd(await addStocks(target.value!.id, parseCodes(addText.value)))
    addVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

function openImport(g: WatchlistGroup) {
  target.value = g
  importTag.value = ''
  importVisible.value = true
}

async function confirmImport() {
  saving.value = true
  try {
    reportAdd(await importFavorites(target.value!.id, importTag.value.trim() || undefined))
    importVisible.value = false
    await load()
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function removeOne(g: WatchlistGroup, code: string) {
  try {
    await removeStocks(g.id, [code])
    await load()
  } catch {
    /* 已提示 */
  }
}

// ---- 分组 ----
async function setActive(g: WatchlistGroup, v: boolean) {
  try {
    await updateGroup(g.id, { is_active: v })
    g.is_active = v
    if (!v) ElMessage.info('已停用：定时分析会跳过该分组')
  } catch {
    /* 已提示 */
  }
}

async function remove(g: WatchlistGroup) {
  try {
    await deleteGroup(g.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 被定时分析引用时后端会拒绝并提示 */
  }
}

async function move(idx: number, delta: number) {
  const next = [...groups.value]
  ;[next[idx], next[idx + delta]] = [next[idx + delta], next[idx]]
  try {
    await reorderGroups(next.map(g => g.id))
    groups.value = next
  } catch {
    /* 已提示 */
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.groups-page {
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

.group-card {
  margin-bottom: 16px;

  &.inactive {
    opacity: 0.6;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .title {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }

  .count {
    margin-left: 2px;
  }

  .ops {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .desc {
    margin: 0 0 10px;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }

  .stocks {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-height: 56px;
    max-height: 160px;
    overflow: auto;
    margin-bottom: 12px;
  }

  .code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }
}

.muted {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.help {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
