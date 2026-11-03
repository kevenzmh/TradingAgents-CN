/**
 * 工作流编辑器（社区版重建）
 *
 * 无第三方画布库（未引入 vue-flow），以 SVG + 原生拖拽实现节点画布：
 * - 左侧节点面板：结构节点（开始/并行/汇总/结束）+ agent 节点（从现有流程与预置清单提取）
 * - 中央画布：拖拽移动节点、点击选中、从节点端口拖出连线
 * - 右侧属性抽屉：节点标签/agent 配置(focus)/条件、连线删除
 * - 顶部：保存（PUT nodes+edges+position）、校验、运行跳转到分析页
 */
<template>
  <div class="wf-editor">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-button :icon="Back" text @click="goBack">返回</el-button>
        <el-input v-model="name" class="name-input" placeholder="流程名称" />
        <el-tag size="small">{{ typeLabel(workflow?.workflow_type) }}</el-tag>
      </div>
      <div class="toolbar-right">
        <el-button :icon="MagicStick" @click="autoLayout">整理布局</el-button>
        <el-button :icon="Checked" @click="validateWorkflow">校验</el-button>
        <el-button type="primary" :icon="DocumentChecked" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <div v-loading="loading" class="editor-body">
      <!-- 左侧节点面板 -->
      <div class="palette">
        <div class="palette-title">添加节点</div>
        <div class="palette-section">结构</div>
        <div
          v-for="cat in STRUCT_NODES"
          :key="cat.type"
          class="palette-item"
          draggable="true"
          @dragstart="onPaletteDragStart($event, { type: cat.type, label: cat.label })"
        >
          <span class="dot" :class="`dot-${TYPE_COLORS[cat.type] || 'blue'}`" />
          {{ cat.label }}
        </div>

        <div class="palette-section">智能体</div>
        <el-input v-model="agentFilter" placeholder="搜索智能体" size="small" clearable class="agent-filter" />
        <div class="palette-agents">
          <div
            v-for="agent in filteredAgents"
            :key="agent.agent_id"
            class="palette-item"
            draggable="true"
            @dragstart="onPaletteDragStart($event, { type: agent.type, agent_id: agent.agent_id, label: agent.label })"
          >
            <span class="dot" :class="`dot-${TYPE_COLORS[agent.type] || 'blue'}`" />
            {{ agent.label }}
          </div>
          <div v-if="!filteredAgents.length" class="palette-empty">无匹配智能体</div>
        </div>

        <div class="palette-hint">拖拽节点到画布；从节点右侧圆点拖出连线到目标节点</div>
      </div>

      <!-- 画布 -->
      <div
        ref="canvasRef"
        class="canvas"
        @dragover.prevent
        @drop="onCanvasDrop"
        @mousedown="onCanvasMouseDown"
        @mousemove="onCanvasMouseMove"
        @mouseup="onCanvasMouseUp"
        @wheel="onWheel"
      >
        <!-- 连线层 -->
        <svg class="edges-svg">
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L0,6 L7,3 z" fill="#909399" />
            </marker>
          </defs>
          <path
            v-for="edge in renderedEdges"
            :key="edge.id"
            :d="edge.path"
            class="edge-path"
            :class="{ 'edge-selected': selectedEdgeId === edge.id }"
            marker-end="url(#arrow)"
            @mousedown.stop="selectEdge(edge.id)"
          />
          <!-- 正在拖出的连线 -->
          <path v-if="linkingPath" :d="linkingPath" class="edge-linking" marker-end="url(#arrow)" />
        </svg>

        <!-- 节点层 -->
        <div
          v-for="node in nodes"
          :key="node.id"
          class="node"
          :class="[
            `node-${TYPE_COLORS[node.type] || 'blue'}`,
            { 'node-selected': selectedNodeId === node.id, 'node-linking': linking && linking.source === node.id }
          ]"
          :style="{ left: `${node.position.x + pan.x}px`, top: `${node.position.y + pan.y}px` }"
          @mousedown.stop="onNodeMouseDown($event, node)"
        >
          <div class="node-label">{{ node.label || node.id }}</div>
          <div class="node-type">{{ NODE_TYPE_LABELS[node.type] || node.type }}</div>
          <div
            class="port port-in"
            title="输入"
            @mousedown.stop
            @mouseup.stop="onPortUp(node)"
          />
          <div
            class="port port-out"
            title="拖出连线"
            @mousedown.stop="onPortDown($event, node)"
          />
        </div>

        <div v-if="!nodes.length && !loading" class="canvas-empty">从左侧拖入节点开始编排</div>
      </div>

      <!-- 右侧属性抽屉 -->
      <div class="inspector" v-if="selectedNode || selectedEdge">
        <template v-if="selectedNode">
          <div class="inspector-title">节点属性</div>
          <el-form label-position="top" size="small">
            <el-form-item label="ID（唯一标识）">
              <el-input :model-value="selectedNode.id" disabled />
            </el-form-item>
            <el-form-item label="显示名称">
              <el-input v-model="selectedNode.label" placeholder="节点名称" />
            </el-form-item>
            <el-form-item label="类型">
              <el-input :model-value="NODE_TYPE_LABELS[selectedNode.type] || selectedNode.type" disabled />
            </el-form-item>
            <el-form-item v-if="selectedNode.agent_id" label="关联智能体">
              <el-input :model-value="selectedNode.agent_id" disabled />
            </el-form-item>
            <el-form-item v-if="selectedNode.agent_id" label="分析重点（focus）">
              <el-input
                v-model="selectedNode.config!.focus"
                type="textarea"
                :rows="3"
                placeholder="该智能体的分析侧重点，注入 Prompt"
              />
            </el-form-item>
            <el-form-item v-if="selectedNode.type === 'debate'" label="辩论轮数">
              <el-input-number v-model="selectedNode.config!.rounds" :min="1" :max="5" />
            </el-form-item>
            <el-form-item v-if="selectedNode.type === 'condition'" label="条件表达式">
              <el-input v-model="selectedNode.condition" placeholder="如：state.confidence > 0.7" />
            </el-form-item>
            <el-divider />
            <el-button type="danger" plain size="small" :icon="Delete" @click="removeNode(selectedNode.id)">
              删除节点
            </el-button>
          </el-form>
        </template>
        <template v-else-if="selectedEdge">
          <div class="inspector-title">连线属性</div>
          <div class="edge-info">
            {{ nodeById(selectedEdge.source)?.label || selectedEdge.source }}
            <el-icon style="margin: 0 4px"><Right /></el-icon>
            {{ nodeById(selectedEdge.target)?.label || selectedEdge.target }}
          </div>
          <el-form label-position="top" size="small">
            <el-form-item label="类型">
              <el-radio-group v-model="selectedEdge.type">
                <el-radio value="normal">普通</el-radio>
                <el-radio value="conditional">条件</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="selectedEdge.type === 'conditional'" label="条件标签">
              <el-input v-model="selectedEdge.condition" placeholder="如 true / false" />
            </el-form-item>
            <el-divider />
            <el-button type="danger" plain size="small" :icon="Delete" @click="removeEdge(selectedEdge.id)">
              删除连线
            </el-button>
          </el-form>
        </template>
      </div>
      <div class="inspector inspector-empty" v-else>
        <div class="inspector-title">属性</div>
        <p class="inspector-hint">点击节点或连线查看/编辑属性</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Delete, Right, Checked, DocumentChecked, MagicStick } from '@element-plus/icons-vue'
import { workflowApi, type NodeDefinition, type EdgeDefinition, type WorkflowDefinition } from '@/api/workflow'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const workflow = ref<WorkflowDefinition | null>(null)
const name = ref('')
const nodes = ref<NodeDefinition[]>([])
const edges = ref<EdgeDefinition[]>([])

const selectedNodeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)

const canvasRef = ref<HTMLElement>()
const pan = reactive({ x: 0, y: 0 })
const dragging = ref<{ nodeId: string; offsetX: number; offsetY: number } | null>(null)
const panning = ref<{ startX: number; startY: number; panX: number; panY: number } | null>(null)
const linking = ref<{ source: string; x: number; y: number } | null>(null)

const agentFilter = ref('')

// ==================== 常量 ====================

const NODE_W = 170
const NODE_H = 56

const NODE_TYPE_LABELS: Record<string, string> = {
  start: '开始', end: '结束', parallel: '并行开始', merge: '汇总', debate: '辩论',
  analyst: '分析师', researcher: '研究员', trader: '研究整合', risk: '情景分析',
  manager: '决策层', condition: '条件', post_processor: '后处理', tool: '工具',
}

const TYPE_COLORS: Record<string, string> = {
  start: 'green', end: 'gray', parallel: 'purple', merge: 'purple', debate: 'orange',
  analyst: 'blue', researcher: 'cyan', trader: 'gold', risk: 'red',
  manager: 'violet', condition: 'pink', post_processor: 'teal', tool: 'gray',
}

const STRUCT_NODES = [
  { type: 'start', label: '开始' },
  { type: 'parallel', label: '并行开始' },
  { type: 'merge', label: '汇总' },
  { type: 'debate', label: '辩论' },
  { type: 'condition', label: '条件' },
  { type: 'end', label: '结束' },
] as const

/** 预置智能体清单（agent_id 与 AgentRegistry 一致） */
const AGENT_CATALOG: Array<{ agent_id: string; label: string; type: NodeDefinition['type'] }> = [
  { agent_id: 'index_analyst_v2', label: '大盘分析师', type: 'analyst' },
  { agent_id: 'sector_analyst_v2', label: '板块分析师', type: 'analyst' },
  { agent_id: 'market_analyst_v2', label: '市场分析师', type: 'analyst' },
  { agent_id: 'fundamentals_analyst_v2', label: '基本面分析师', type: 'analyst' },
  { agent_id: 'news_analyst_v2', label: '新闻分析师', type: 'analyst' },
  { agent_id: 'social_analyst_v2', label: '社媒分析师', type: 'analyst' },
  { agent_id: 'bull_researcher_v2', label: '乐观研究员', type: 'researcher' },
  { agent_id: 'bear_researcher_v2', label: '审慎研究员', type: 'researcher' },
  { agent_id: 'risky_analyst_v2', label: '高弹性情景研究员', type: 'risk' },
  { agent_id: 'safe_analyst_v2', label: '防御情景研究员', type: 'risk' },
  { agent_id: 'neutral_analyst_v2', label: '基准情景研究员', type: 'risk' },
  { agent_id: 'research_manager_v2', label: '研究经理', type: 'manager' },
  { agent_id: 'risk_manager_v2', label: '风险评估师', type: 'manager' },
  { agent_id: 'trader_v2', label: '研究整合员', type: 'trader' },
]

const TYPE_LABELS: Record<string, string> = {
  stock_analysis: '股票分析', etf_analysis: 'ETF 分析', position_analysis: '持仓研究',
  trade_review: '交易复盘', portfolio_health: '组合体检', general: '通用研究',
}
const typeLabel = (t?: string) => TYPE_LABELS[t || ''] || t || '未分类'

// ==================== 计算 ====================

const selectedNode = computed(() => nodes.value.find(n => n.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => edges.value.find(e => e.id === selectedEdgeId.value) || null)

const filteredAgents = computed(() => {
  const kw = agentFilter.value.trim().toLowerCase()
  if (!kw) return AGENT_CATALOG
  return AGENT_CATALOG.filter(a =>
    a.label.toLowerCase().includes(kw) || a.agent_id.includes(kw)
  )
})

const nodeById = (id: string) => nodes.value.find(n => n.id === id)

/** 连线路径：源右侧 → 目标左侧，三段贝塞尔 */
const renderedEdges = computed(() => {
  return edges.value.map(e => {
    const s = nodeById(e.source)
    const t = nodeById(e.target)
    if (!s || !t) return { ...e, path: '' }
    const sx = s.position.x + pan.x + NODE_W
    const sy = s.position.y + pan.y + NODE_H / 2
    const tx = t.position.x + pan.x
    const ty = t.position.y + pan.y + NODE_H / 2
    const dx = Math.max(40, Math.abs(tx - sx) / 2)
    return {
      ...e,
      path: `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`,
    }
  })
})

const linkingPath = computed(() => {
  if (!linking.value) return ''
  const s = nodeById(linking.value.source)
  if (!s) return ''
  const sx = s.position.x + pan.x + NODE_W
  const sy = s.position.y + pan.y + NODE_H / 2
  const { x, y } = linking.value
  const dx = Math.max(40, Math.abs(x - sx) / 2)
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${x - dx} ${y}, ${x} ${y}`
})

// ==================== 加载 ====================

const loadWorkflow = async () => {
  const id = route.query.id as string
  if (!id) {
    ElMessage.error('缺少流程 ID')
    goBack()
    return
  }
  loading.value = true
  try {
    const wf = await workflowApi.get(id)
    if (!wf) {
      ElMessage.error('工作流不存在')
      goBack()
      return
    }
    workflow.value = wf
    name.value = wf.name
    nodes.value = (wf.nodes || []).map(n => ({ ...n, config: { ...(n.config || {}) } }))
    edges.value = (wf.edges || []).map(e => ({ ...e }))
    fitView()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载工作流失败')
  } finally {
    loading.value = false
  }
}

const fitView = () => {
  if (!nodes.value.length) return
  const minX = Math.min(...nodes.value.map(n => n.position.x))
  const minY = Math.min(...nodes.value.map(n => n.position.y))
  pan.x = minX > 40 ? 40 - minX : 0
  pan.y = minY > 40 ? 40 - minY : 0
}

// ==================== 交互：拖入/拖动/连线/平移 ====================

const onPaletteDragStart = (ev: DragEvent, cat: { type: string; agent_id?: string; label: string }) => {
  ev.dataTransfer?.setData('application/wf-node', JSON.stringify(cat))
}

const onCanvasDrop = (ev: DragEvent) => {
  const raw = ev.dataTransfer?.getData('application/wf-node')
  if (!raw) return
  const cat = JSON.parse(raw)
  const rect = canvasRef.value!.getBoundingClientRect()
  const x = ev.clientX - rect.left - pan.x - NODE_W / 2
  const y = ev.clientY - rect.top - pan.y - NODE_H / 2
  addNode(cat.type, cat.agent_id, cat.label, { x: Math.max(0, x), y: Math.max(0, y) })
}

const addNode = (type: string, agentId: string | undefined, label: string, position: { x: number; y: number }) => {
  // agent 节点用 agent_id 作节点 ID（与内置流程一致）；结构节点生成唯一 ID
  const baseId = agentId || `${type}_${Date.now().toString(36)}`
  let id = baseId
  let i = 2
  while (nodes.value.some(n => n.id === id)) {
    id = `${baseId}_${i++}`
  }
  const node: NodeDefinition = {
    id,
    type: type as NodeDefinition['type'],
    agent_id: agentId,
    label: label || id,
    position,
    config: agentId ? { focus: '' } : {},
    condition: type === 'condition' ? '' : undefined,
  }
  nodes.value.push(node)
  selectNode(id)
}

const selectNode = (id: string) => {
  selectedNodeId.value = id
  selectedEdgeId.value = null
}

const selectEdge = (id: string) => {
  selectedEdgeId.value = id
  selectedNodeId.value = null
}

const onNodeMouseDown = (ev: MouseEvent, node: NodeDefinition) => {
  selectNode(node.id)
  dragging.value = {
    nodeId: node.id,
    offsetX: ev.clientX - (node.position.x + pan.x),
    offsetY: ev.clientY - (node.position.y + pan.y),
  }
}

const onCanvasMouseDown = (ev: MouseEvent) => {
  // 点在空白处：开始平移画布
  panning.value = { startX: ev.clientX, startY: ev.clientY, panX: pan.x, panY: pan.y }
  selectedNodeId.value = null
  selectedEdgeId.value = null
}

const onCanvasMouseMove = (ev: MouseEvent) => {
  if (dragging.value) {
    const node = nodeById(dragging.value.nodeId)
    if (node) {
      node.position.x = ev.clientX - dragging.value.offsetX - pan.x
      node.position.y = ev.clientY - dragging.value.offsetY - pan.y
    }
    return
  }
  if (panning.value) {
    pan.x = panning.value.panX + (ev.clientX - panning.value.startX)
    pan.y = panning.value.panY + (ev.clientY - panning.value.startY)
    return
  }
  if (linking.value) {
    const rect = canvasRef.value!.getBoundingClientRect()
    linking.value.x = ev.clientX - rect.left
    linking.value.y = ev.clientY - rect.top
  }
}

const onCanvasMouseUp = () => {
  dragging.value = null
  panning.value = null
  if (linking.value) {
    // 在空白处松开 → 取消
    linking.value = null
  }
}


const onPortDown = (ev: MouseEvent, node: NodeDefinition) => {
  const rect = canvasRef.value!.getBoundingClientRect()
  linking.value = {
    source: node.id,
    x: ev.clientX - rect.left,
    y: ev.clientY - rect.top,
  }
}

const onPortUp = (node: NodeDefinition) => {
  if (linking.value && linking.value.source !== node.id) {
    addEdge(linking.value.source, node.id)
  }
  linking.value = null
}

const addEdge = (source: string, target: string) => {
  // 去重 + 禁自环
  if (source === target) return
  if (edges.value.some(e => e.source === source && e.target === target)) return
  const edge: EdgeDefinition = {
    id: `e_${source}_${target}_${Date.now().toString(36)}`,
    source, target, type: 'normal',
  }
  edges.value.push(edge)
  selectEdge(edge.id)
}

const removeNode = (id: string) => {
  nodes.value = nodes.value.filter(n => n.id !== id)
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id)
  selectedNodeId.value = null
}

const removeEdge = (id: string) => {
  edges.value = edges.value.filter(e => e.id !== id)
  selectedEdgeId.value = null
}

const onWheel = (ev: WheelEvent) => {
  // 滚轮纵向平移（避免与浏览器缩放冲突）
  if (ev.ctrlKey) return
  pan.y -= ev.deltaY
}

// ==================== 布局 / 校验 / 保存 ====================

/** 按拓扑分层自动排版：入度为 0 的放最左，逐层向右 */
const autoLayout = () => {
  if (!nodes.value.length) return
  const inDeg = new Map(nodes.value.map(n => [n.id, 0]))
  edges.value.forEach(e => inDeg.set(e.target, (inDeg.get(e.target) || 0) + 1))
  const layers: string[][] = []
  let frontier = nodes.value.filter(n => (inDeg.get(n.id) || 0) === 0).map(n => n.id)
  const placed = new Set<string>()
  while (frontier.length) {
    layers.push(frontier)
    frontier.forEach(id => placed.add(id))
    const next: string[] = []
    edges.value.forEach(e => {
      if (frontier.includes(e.source) && !placed.has(e.target) && !next.includes(e.target)) {
        next.push(e.target)
      }
    })
    frontier = next
    if (layers.length > 40) break // 防环死循环
  }
  // 未入层的（环）追加到末层
  const rest = nodes.value.filter(n => !placed.has(n.id)).map(n => n.id)
  if (rest.length) layers.push(rest)

  layers.forEach((layer, li) => {
    layer.forEach((id, ni) => {
      const node = nodeById(id)
      if (node) {
        node.position.x = 40 + li * (NODE_W + 90)
        node.position.y = 40 + ni * (NODE_H + 40)
      }
    })
  })
  pan.x = 0
  pan.y = 0
}

const validateWorkflow = async () => {
  try {
    const res = await workflowApi.validate({
      id: workflow.value?.id,
      name: name.value,
      nodes: nodes.value,
      edges: edges.value,
      workflow_type: workflow.value?.workflow_type,
    } as any)
    const data = (res as any)?.data || res
    if (data?.is_valid) {
      ElMessage.success(`校验通过${data.warnings?.length ? `（${data.warnings.length} 条警告）` : ''}`)
    } else {
      const errs = (data?.errors || []).join('；')
      ElMessage.warning(`校验未通过：${errs || '未知错误'}`)
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '校验失败')
  }
}

const save = async () => {
  if (!workflow.value) return
  if (!name.value.trim()) {
    ElMessage.warning('请填写流程名称')
    return
  }
  // 本地快检
  if (!nodes.value.some(n => n.type === 'start') || !nodes.value.some(n => n.type === 'end')) {
    ElMessage.warning('流程需要至少一个开始节点和一个结束节点')
    return
  }
  saving.value = true
  try {
    const res: any = await workflowApi.update(workflow.value.id, {
      name: name.value.trim(),
      nodes: nodes.value,
      edges: edges.value,
    })
    if (res.success) {
      ElMessage.success('保存成功')
      workflow.value = { ...workflow.value!, name: name.value.trim(), nodes: nodes.value, edges: edges.value }
    } else {
      ElMessage.error(res.message || '保存失败')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const goBack = () => {
  router.push('/workflow')
}

// 事件代理：节点 mouseup 冒泡到画布会先触发连线逻辑——直接在节点层监听
onMounted(loadWorkflow)
onBeforeUnmount(() => {
  dragging.value = null
  panning.value = null
})
</script>

<style scoped lang="scss">
.wf-editor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
  min-height: 520px;
  padding: 12px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--el-bg-color);
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 10px;

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;

    .name-input {
      width: 260px;
    }
  }
  .toolbar-right {
    display: flex;
    gap: 8px;
  }
}

.editor-body {
  display: flex;
  gap: 10px;
  flex: 1;
  min-height: 0;
}

.palette {
  width: 200px;
  flex-shrink: 0;
  background: var(--el-bg-color);
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  overflow-y: auto;

  .palette-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 10px;
  }
  .palette-section {
    font-size: 12px;
    color: #909399;
    margin: 10px 0 6px;
  }
  .agent-filter {
    margin-bottom: 6px;
  }
  .palette-agents {
    max-height: 320px;
    overflow-y: auto;
  }
  .palette-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 8px;
    margin-bottom: 4px;
    border: 1px dashed #dcdfe6;
    border-radius: 6px;
    font-size: 13px;
    cursor: grab;
    user-select: none;

    &:hover {
      border-color: var(--el-color-primary);
      background: var(--el-color-primary-light-9);
    }
  }
  .palette-empty {
    font-size: 12px;
    color: #c0c4cc;
    text-align: center;
    padding: 8px;
  }
  .palette-hint {
    margin-top: 12px;
    font-size: 12px;
    color: #c0c4cc;
    line-height: 1.5;
  }
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #409eff;
}
.dot-green { background: #67c23a; }
.dot-gray { background: #909399; }
.dot-purple { background: #a855f7; }
.dot-orange { background: #e6a23c; }
.dot-blue { background: #409eff; }
.dot-cyan { background: #06b6d4; }
.dot-gold { background: #d4a017; }
.dot-red { background: #f56c6c; }
.dot-violet { background: #7c3aed; }
.dot-pink { background: #ec4899; }
.dot-teal { background: #14b8a6; }

.canvas {
  position: relative;
  flex: 1;
  min-width: 0;
  background: #f7f9fc;
  background-image: radial-gradient(#dcdfe6 1px, transparent 1px);
  background-size: 22px 22px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  cursor: default;
}

.edges-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;

  .edge-path {
    pointer-events: stroke;
    stroke: #909399;
    stroke-width: 2;
    fill: none;
    cursor: pointer;

    &:hover, &.edge-selected {
      stroke: var(--el-color-primary);
      stroke-width: 2.5;
    }
  }
  .edge-linking {
    stroke: var(--el-color-primary);
    stroke-dasharray: 6 4;
    stroke-width: 2;
    fill: none;
  }
}

.node {
  position: absolute;
  width: 170px;
  height: 56px;
  background: #fff;
  border: 2px solid #409eff;
  border-radius: 8px;
  padding: 8px 10px;
  cursor: move;
  user-select: none;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
  z-index: 2;

  &.node-green { border-color: #67c23a; }
  &.node-gray { border-color: #909399; }
  &.node-purple { border-color: #a855f7; }
  &.node-orange { border-color: #e6a23c; }
  &.node-cyan { border-color: #06b6d4; }
  &.node-gold { border-color: #d4a017; }
  &.node-red { border-color: #f56c6c; }
  &.node-violet { border-color: #7c3aed; }
  &.node-pink { border-color: #ec4899; }
  &.node-teal { border-color: #14b8a6; }

  &.node-selected {
    box-shadow: 0 0 0 3px var(--el-color-primary-light-7);
  }

  .node-label {
    font-size: 13px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .node-type {
    font-size: 11px;
    color: #909399;
    margin-top: 2px;
  }

  .port {
    position: absolute;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #fff;
    border: 2px solid #909399;
    top: 50%;
    transform: translateY(-50%);
    cursor: crosshair;

    &.port-in { left: -7px; }
    &.port-out { right: -7px; border-color: var(--el-color-primary); }

    &:hover { transform: translateY(-50%) scale(1.3); }
  }
}

.canvas-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: #c0c4cc;
  font-size: 14px;
}

.inspector {
  width: 280px;
  flex-shrink: 0;
  background: var(--el-bg-color);
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px;
  overflow-y: auto;

  .inspector-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 12px;
  }
  .inspector-hint {
    color: #c0c4cc;
    font-size: 13px;
  }
  .edge-info {
    display: flex;
    align-items: center;
    font-size: 13px;
    color: #606266;
    margin-bottom: 12px;
  }
}
</style>
