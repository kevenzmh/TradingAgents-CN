<template>
  <div class="channels-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">
          <el-icon><Bell /></el-icon>
          渠道推送
        </h1>
        <p class="page-description">
          把分析完成、预警等通知推送到飞书、钉钉、企业微信机器人或邮箱。每个渠道可单独选择订阅的事件。
        </p>
      </div>
      <el-button type="primary" :icon="Plus" :disabled="items.length >= maxChannels" @click="openCreate">
        添加渠道
      </el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="items" v-loading="loading" stripe empty-text="还没有推送渠道，点击右上角“添加渠道”">
        <el-table-column label="名称" min-width="160">
          <template #default="{ row }">
            <strong>{{ row.name }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.type)" effect="plain">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="推送目标" min-width="260">
          <template #default="{ row }">
            <span class="mono">{{ row.type === 'email' ? row.email_address : row.webhook_url_masked }}</span>
          </template>
        </el-table-column>
        <el-table-column label="订阅事件" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="e in row.events" :key="e" size="small" type="info" class="ev-tag">{{ eventLabel(e) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" :loading="busyId === row.channel_id" @change="(v: any) => toggle(row, !!v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :loading="testingId === row.channel_id" @click="runTest(row)">测试推送</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm :title="`确认删除渠道「${row.name}」？`" @confirm="remove(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div class="foot-tip">已配置 {{ items.length }} / {{ maxChannels }} 个渠道</div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑渠道' : '添加渠道'" width="600px" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" @submit.prevent>
        <el-form-item label="渠道类型" prop="type">
          <el-radio-group v-model="form.type" :disabled="!!editing">
            <el-radio-button v-for="t in types" :key="t.value" :value="t.value">{{ t.label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="渠道名称" prop="name">
          <el-input v-model="form.name" maxlength="50" show-word-limit placeholder="如：团队飞书群" />
        </el-form-item>

        <el-form-item v-if="form.type !== 'email'" label="Webhook" prop="webhook_url">
          <el-input
            v-model="form.webhook_url"
            type="textarea"
            :rows="2"
            class="mono"
            :placeholder="editing ? `已设置（${editing.webhook_url_masked}），留空表示不修改` : webhookPlaceholder"
          />
          <div class="help">{{ webhookHelp }}</div>
        </el-form-item>
        <el-form-item v-else label="邮箱地址" prop="email_address">
          <el-input v-model="form.email_address" placeholder="接收通知的邮箱" />
          <div class="help">邮件渠道复用系统 SMTP，请确认管理员已在「邮件通知」页配置发信服务器。</div>
        </el-form-item>

        <el-form-item label="订阅事件" prop="events">
          <el-checkbox-group v-model="form.events">
            <el-checkbox v-for="e in eventOptions" :key="e.value" :value="e.value">{{ e.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing ? '保存' : '添加' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Bell, Plus } from '@element-plus/icons-vue'

import {
  createChannel,
  deleteChannel,
  listChannels,
  testChannel,
  updateChannel,
  type Channel,
  type ChannelType
} from '@/api/notificationChannels'

const items = ref<Channel[]>([])
const types = ref<Array<{ value: ChannelType; label: string }>>([])
const eventOptions = ref<Array<{ value: string; label: string }>>([])
const maxChannels = ref(20)
const loading = ref(false)
const busyId = ref('')
const testingId = ref('')

const typeLabel = (t: string) => types.value.find(x => x.value === t)?.label || t
const eventLabel = (e: string) => eventOptions.value.find(x => x.value === e)?.label || e
const typeTag = (t: string) => (({ feishu: 'primary', dingtalk: 'success', wechatwork: 'warning', email: 'info' } as Record<string, any>)[t] || 'info')

async function load() {
  loading.value = true
  try {
    const meta = await listChannels()
    items.value = meta.items
    types.value = meta.types
    eventOptions.value = meta.events
    maxChannels.value = meta.max_channels
  } finally {
    loading.value = false
  }
}

// ---- 表单 ----
const dialogVisible = ref(false)
const editing = ref<Channel | null>(null)
const saving = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  type: 'feishu' as ChannelType,
  name: '',
  webhook_url: '',
  email_address: '',
  events: ['analysis_complete', 'alert'] as string[],
  enabled: true
})

const WEBHOOK_HINTS: Record<string, { placeholder: string; help: string }> = {
  feishu: {
    placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx',
    help: '飞书群 → 设置 → 群机器人 → 添加自定义机器人，复制 Webhook 地址。若启用了“自定义关键词”，请把「TradingAgents」设为关键词。'
  },
  dingtalk: {
    placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx',
    help: '钉钉群 → 群设置 → 智能群助手 → 自定义机器人。安全设置请选择“自定义关键词”并填写「TradingAgents」（暂不支持加签）。'
  },
  wechatwork: {
    placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx',
    help: '企业微信群 → 添加群机器人，复制 Webhook 地址。'
  }
}
const webhookPlaceholder = computed(() => WEBHOOK_HINTS[form.type]?.placeholder || '')
const webhookHelp = computed(() => WEBHOOK_HINTS[form.type]?.help || '')

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: '请填写渠道名称', trigger: 'blur' }],
  webhook_url: [
    {
      validator: (_r, v: string, cb) => {
        if (editing.value && !v) return cb()
        if (!v?.trim()) return cb(new Error('请填写 Webhook 地址'))
        if (!/^https:\/\//i.test(v.trim())) return cb(new Error('Webhook 地址必须以 https:// 开头'))
        cb()
      },
      trigger: 'blur'
    }
  ],
  email_address: [
    { required: true, message: '请填写邮箱地址', trigger: 'blur' },
    { type: 'email', message: '邮箱地址格式不正确', trigger: 'blur' }
  ],
  events: [{ type: 'array', required: true, min: 1, message: '请至少选择一个事件', trigger: 'change' }]
}))

function openCreate() {
  editing.value = null
  Object.assign(form, { type: 'feishu', name: '', webhook_url: '', email_address: '', events: ['analysis_complete', 'alert'], enabled: true })
  dialogVisible.value = true
  formRef.value?.clearValidate()
}

function openEdit(row: Channel) {
  editing.value = row
  Object.assign(form, {
    type: row.type, name: row.name, webhook_url: '', email_address: row.email_address || '',
    events: [...row.events], enabled: row.enabled
  })
  dialogVisible.value = true
  formRef.value?.clearValidate()
}

async function save() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    if (editing.value) {
      const payload: Record<string, unknown> = { name: form.name.trim(), enabled: form.enabled, events: form.events }
      if (form.type === 'email') payload.email_address = form.email_address.trim()
      else if (form.webhook_url.trim()) payload.webhook_url = form.webhook_url.trim()
      await updateChannel(editing.value.channel_id, payload)
      ElMessage.success('已保存')
    } else {
      await createChannel({
        type: form.type,
        name: form.name.trim(),
        enabled: form.enabled,
        events: form.events,
        ...(form.type === 'email' ? { email_address: form.email_address.trim() } : { webhook_url: form.webhook_url.trim() })
      })
      ElMessage.success('已添加，建议点击“测试推送”确认可用')
    }
    dialogVisible.value = false
    await load()
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function toggle(row: Channel, enabled: boolean) {
  busyId.value = row.channel_id
  try {
    await updateChannel(row.channel_id, { enabled })
    row.enabled = enabled
  } catch {
    /* 已提示，开关保持原状 */
  } finally {
    busyId.value = ''
  }
}

async function runTest(row: Channel) {
  testingId.value = row.channel_id
  try {
    const res = await testChannel(row.channel_id)
    if (res.success) ElMessage.success(`「${row.name}」测试推送成功`)
    else ElMessage.error({ message: `「${row.name}」测试推送失败：${res.message}`, duration: 6000 })
  } catch {
    /* 已提示 */
  } finally {
    testingId.value = ''
  }
}

async function remove(row: Channel) {
  try {
    await deleteChannel(row.channel_id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 已提示 */
  }
}

onMounted(load)
</script>

<style scoped lang="scss">
.channels-page {
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

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  word-break: break-all;
}

.ev-tag {
  margin: 0 4px 4px 0;
}

.help {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.foot-tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
