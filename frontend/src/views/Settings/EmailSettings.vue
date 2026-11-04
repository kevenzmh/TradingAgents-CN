<template>
  <div class="email-page">
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Message /></el-icon>
        邮件通知
      </h1>
      <p class="page-description">
        分析完成、预警等通知可发送到你的邮箱。发信使用系统统一的 SMTP 服务器，由管理员配置。
      </p>
    </div>

    <!-- 我的通知设置 -->
    <el-card shadow="never" class="block" v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>我的邮件通知</span>
          <el-tag v-if="smtpReady" type="success" size="small">发信服务器已就绪</el-tag>
          <el-tag v-else type="warning" size="small">发信服务器未配置</el-tag>
        </div>
      </template>

      <el-alert
        v-if="!smtpReady"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
        :title="authStore.isAdmin ? '请先在下方配置 SMTP 发信服务器，否则邮件无法发出。' : '管理员尚未配置 SMTP 发信服务器，邮件暂时无法发出。'"
      />

      <el-form :model="form" label-width="120px" @submit.prevent>
        <el-form-item label="开启邮件通知">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="接收邮箱">
          <el-input v-model="form.email_address" :placeholder="accountEmail ? `留空则使用账号邮箱（${accountEmail}）` : '请输入接收邮箱'" style="max-width: 380px" />
        </el-form-item>
        <el-form-item label="通知类型">
          <el-checkbox v-model="form.single_analysis">单股分析结果</el-checkbox>
          <el-checkbox v-model="form.batch_analysis">批量分析汇总</el-checkbox>
          <el-checkbox v-model="form.scheduled_analysis">定时分析报告</el-checkbox>
          <el-checkbox v-model="form.important_signals">重要信号预警</el-checkbox>
          <el-checkbox v-model="form.system_notifications">系统通知</el-checkbox>
        </el-form-item>
        <el-form-item label="免打扰时段">
          <el-switch v-model="form.quiet_hours_enabled" />
          <template v-if="form.quiet_hours_enabled">
            <el-time-select v-model="form.quiet_hours_start" start="00:00" end="23:30" step="00:30" style="width: 120px; margin-left: 16px" placeholder="开始" />
            <span class="sep">至</span>
            <el-time-select v-model="form.quiet_hours_end" start="00:00" end="23:30" step="00:30" style="width: 120px" placeholder="结束" />
            <span class="help">时段内不发送邮件（支持跨天，如 22:00 至 08:00）</span>
          </template>
        </el-form-item>
        <el-form-item label="邮件格式">
          <el-radio-group v-model="form.format">
            <el-radio value="html">HTML</el-radio>
            <el-radio value="text">纯文本</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
          <el-button :loading="testing" :disabled="!smtpReady" @click="runTest">发送测试邮件</el-button>
          <span class="help">测试邮件发往已保存的接收邮箱，不受免打扰与频率限制影响</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- SMTP（管理员） -->
    <el-card v-if="authStore.isAdmin" shadow="never" class="block" v-loading="smtpLoading">
      <template #header>
        <div class="card-header">
          <span>SMTP 发信服务器（管理员）</span>
          <el-tag v-if="smtp?.source === 'env'" type="info" size="small">当前使用 .env 配置，保存后改用数据库配置</el-tag>
        </div>
      </template>
      <el-form :model="smtpForm" label-width="120px" @submit.prevent>
        <el-form-item label="启用">
          <el-switch v-model="smtpForm.enabled" />
        </el-form-item>
        <el-form-item label="服务器地址">
          <el-input v-model="smtpForm.host" placeholder="如 smtp.qq.com" style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="端口 / 加密">
          <el-input-number v-model="smtpForm.port" :min="1" :max="65535" controls-position="right" style="width: 130px" />
          <el-radio-group v-model="encryption" style="margin-left: 16px" @change="onEncryptionChange">
            <el-radio-button value="starttls">STARTTLS（587）</el-radio-button>
            <el-radio-button value="ssl">SSL（465）</el-radio-button>
            <el-radio-button value="none">不加密</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="smtpForm.username" autocomplete="off" style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="密码 / 授权码">
          <el-input
            v-model="smtpForm.password"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="smtp?.password_set ? '已设置，留空表示不修改' : '邮箱密码或授权码'"
            style="max-width: 320px"
          />
          <span class="help">QQ / 163 等邮箱需使用“授权码”而非登录密码</span>
        </el-form-item>
        <el-form-item label="发件人">
          <el-input v-model="smtpForm.from_email" placeholder="发件邮箱（通常与用户名一致）" style="max-width: 280px" />
          <el-input v-model="smtpForm.from_name" placeholder="显示名称" style="max-width: 180px; margin-left: 8px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="smtpSaving" @click="saveSmtpConfig">保存</el-button>
          <el-button :loading="smtpTesting" @click="runSmtpTest">测试连接</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 发送历史 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>发送历史</span>
          <el-button size="small" :icon="Refresh" :loading="historyLoading" @click="loadHistory">刷新</el-button>
        </div>
      </template>
      <el-table :data="history" size="small" stripe v-loading="historyLoading" empty-text="暂无发送记录">
        <el-table-column label="时间" width="165">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="subject" label="主题" min-width="240" show-overflow-tooltip />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ typeLabel(row.email_type) }}</template>
        </el-table-column>
        <el-table-column prop="to_email" label="收件人" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.error_message" :content="row.error_message || ''">
              <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        class="pager"
        layout="total, prev, pager, next"
        :page-size="pageSize"
        :total="historyTotal"
        @current-change="loadHistory"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Message, Refresh } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'
import {
  getEmailHistory,
  getEmailSettings,
  getSmtp,
  saveEmailSettings,
  saveSmtp,
  sendTestEmail,
  testSmtp,
  type EmailRecord,
  type EmailSettings,
  type SmtpConfig,
  type SmtpForm
} from '@/api/notificationChannels'

const authStore = useAuthStore()

// ---- 我的设置 ----
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const smtpReady = ref(false)
const accountEmail = ref<string | null>(null)
const form = reactive<EmailSettings>({
  enabled: false, email_address: '', single_analysis: true, batch_analysis: true, scheduled_analysis: true,
  important_signals: true, system_notifications: false, quiet_hours_enabled: false,
  quiet_hours_start: '22:00', quiet_hours_end: '08:00', format: 'html', language: 'zh'
})

async function loadMine() {
  loading.value = true
  try {
    const res = await getEmailSettings()
    Object.assign(form, res.settings, { email_address: res.settings.email_address || '' })
    accountEmail.value = res.account_email
    smtpReady.value = res.smtp_ready
  } finally {
    loading.value = false
  }
}

async function save() {
  if (form.email_address && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email_address)) {
    ElMessage.warning('接收邮箱格式不正确')
    return
  }
  if (form.enabled && !form.email_address && !accountEmail.value) {
    ElMessage.warning('请填写接收邮箱（账号未绑定邮箱）')
    return
  }
  saving.value = true
  try {
    await saveEmailSettings({ ...form, email_address: form.email_address?.trim() || null })
    ElMessage.success('已保存')
  } catch {
    /* 已提示 */
  } finally {
    saving.value = false
  }
}

async function runTest() {
  testing.value = true
  try {
    const res = await sendTestEmail()
    if (res.success) ElMessage.success(`测试邮件已发送至 ${res.to}，请查收（可能进垃圾箱）`)
    else ElMessage.error({ message: `发送失败：${res.message}`, duration: 6000 })
    loadHistory()
  } catch {
    /* 已提示 */
  } finally {
    testing.value = false
  }
}

// ---- SMTP ----
const smtp = ref<SmtpConfig | null>(null)
const smtpLoading = ref(false)
const smtpSaving = ref(false)
const smtpTesting = ref(false)
const smtpForm = reactive<SmtpForm>({
  enabled: true, host: '', port: 587, username: '', password: '', from_email: '', from_name: 'TradingAgents-CN',
  use_tls: true, use_ssl: false
})
const encryption = ref<'starttls' | 'ssl' | 'none'>('starttls')

function onEncryptionChange(v: string | number | boolean | undefined) {
  smtpForm.use_tls = v === 'starttls'
  smtpForm.use_ssl = v === 'ssl'
  // 仅当端口还是另一种加密的默认值时才联动，避免覆盖用户自定义端口
  if (v === 'ssl' && smtpForm.port === 587) smtpForm.port = 465
  if (v === 'starttls' && smtpForm.port === 465) smtpForm.port = 587
}

async function loadSmtp() {
  if (!authStore.isAdmin) return
  smtpLoading.value = true
  try {
    const cfg = await getSmtp()
    smtp.value = cfg
    Object.assign(smtpForm, {
      enabled: cfg.source === 'none' ? true : cfg.enabled, host: cfg.host, port: cfg.port, username: cfg.username,
      password: '', from_email: cfg.from_email, from_name: cfg.from_name, use_tls: cfg.use_tls, use_ssl: cfg.use_ssl
    })
    encryption.value = cfg.use_ssl ? 'ssl' : cfg.use_tls ? 'starttls' : 'none'
  } finally {
    smtpLoading.value = false
  }
}

async function saveSmtpConfig() {
  smtpSaving.value = true
  try {
    await saveSmtp({ ...smtpForm })
    ElMessage.success('SMTP 配置已保存')
    await Promise.all([loadSmtp(), loadMine()])
  } catch {
    /* 已提示 */
  } finally {
    smtpSaving.value = false
  }
}

async function runSmtpTest() {
  smtpTesting.value = true
  try {
    const res = await testSmtp({ ...smtpForm })
    if (res.success) ElMessage.success(res.message || 'SMTP 连接成功')
    else ElMessage.error({ message: res.message || 'SMTP 连接失败', duration: 6000 })
  } catch {
    /* 已提示 */
  } finally {
    smtpTesting.value = false
  }
}

// ---- 历史 ----
const history = ref<EmailRecord[]>([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const page = ref(1)
const pageSize = 10

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await getEmailHistory(page.value, pageSize)
    history.value = res.items
    historyTotal.value = res.total
  } finally {
    historyLoading.value = false
  }
}

const TYPE_LABELS: Record<string, string> = {
  single_analysis: '单股分析', batch_analysis: '批量分析', scheduled_analysis: '定时分析',
  important_signal: '重要信号', system_notification: '系统通知', test_email: '测试邮件'
}
const typeLabel = (t: string) => TYPE_LABELS[t] || t
const statusLabel = (s: string) => (({ sent: '已发送', failed: '失败', pending: '待发送', sending: '发送中', cancelled: '已取消' } as Record<string, string>)[s] || s)
const statusTag = (s: string) => (({ sent: 'success', failed: 'danger', pending: 'info', sending: 'warning', cancelled: 'info' } as Record<string, any>)[s] || 'info')

function formatTime(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

onMounted(() => {
  loadMine()
  loadSmtp()
  loadHistory()
})
</script>

<style scoped lang="scss">
.email-page {
  padding: 20px;
}

.page-header {
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

.block {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.help {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.sep {
  margin: 0 8px;
  color: var(--el-text-color-secondary);
}

.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
