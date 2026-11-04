/**
 * 渠道推送 / 邮件通知 API（Pro）
 *
 * 后端：app/pro/routers/channel_configs.py（/api/channel-configs）
 *       app/pro/routers/email_settings.py（/api/email-settings）
 *
 * Webhook 地址与 SMTP 密码只写不读：接口只回传脱敏地址 / password_set。
 */

import { ApiClient } from './request'

// ==================== 渠道 ====================

export type ChannelType = 'feishu' | 'dingtalk' | 'wechatwork' | 'email'

export interface Channel {
  channel_id: string
  type: ChannelType
  name: string
  enabled: boolean
  events: string[]
  has_webhook: boolean
  webhook_url_masked: string | null
  email_address: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ChannelMeta {
  items: Channel[]
  types: Array<{ value: ChannelType; label: string }>
  events: Array<{ value: string; label: string }>
  max_channels: number
}

export interface ChannelPayload {
  type?: ChannelType
  name?: string
  enabled?: boolean
  webhook_url?: string
  email_address?: string
  events?: string[]
}

export async function listChannels(): Promise<ChannelMeta> {
  const res: any = await ApiClient.get('/api/channel-configs', undefined, { showLoading: false })
  return res.data
}

export async function createChannel(payload: ChannelPayload & { type: ChannelType; name: string }): Promise<Channel> {
  const res: any = await ApiClient.post('/api/channel-configs', payload)
  return res.data
}

export async function updateChannel(id: string, payload: ChannelPayload): Promise<Channel> {
  const res: any = await ApiClient.put(`/api/channel-configs/${id}`, payload)
  return res.data
}

export const deleteChannel = (id: string) => ApiClient.delete(`/api/channel-configs/${id}`)

export async function testChannel(id: string): Promise<{ success: boolean; message: string }> {
  const res: any = await ApiClient.post(`/api/channel-configs/${id}/test`, {}, { showLoading: false, skipErrorHandler: true })
  return { success: !!res.success, message: res.message || '' }
}

// ==================== 邮件 ====================

export interface EmailSettings {
  enabled: boolean
  email_address: string | null
  single_analysis: boolean
  batch_analysis: boolean
  scheduled_analysis: boolean
  important_signals: boolean
  system_notifications: boolean
  quiet_hours_enabled: boolean
  quiet_hours_start: string
  quiet_hours_end: string
  format: 'html' | 'text'
  language: 'zh' | 'en'
}

export interface MyEmailSettings {
  settings: EmailSettings
  account_email: string | null
  smtp_ready: boolean
}

export async function getEmailSettings(): Promise<MyEmailSettings> {
  const res: any = await ApiClient.get('/api/email-settings', undefined, { showLoading: false })
  return res.data
}

export async function saveEmailSettings(s: EmailSettings): Promise<EmailSettings> {
  const res: any = await ApiClient.put('/api/email-settings', s)
  return res.data
}

export async function sendTestEmail(): Promise<{ success: boolean; message: string; to?: string }> {
  const res: any = await ApiClient.post('/api/email-settings/test', {}, { showLoading: false, skipErrorHandler: true })
  return { success: !!res.success, message: res.message || '', to: res.to }
}

export interface EmailRecord {
  id: string
  to_email: string
  subject: string
  email_type: string
  status: 'pending' | 'sending' | 'sent' | 'failed' | 'cancelled'
  created_at: string
  sent_at?: string | null
  error_message?: string | null
}

export async function getEmailHistory(page: number, pageSize: number): Promise<{ items: EmailRecord[]; total: number }> {
  const res: any = await ApiClient.get('/api/email-settings/history', { page, page_size: pageSize }, { showLoading: false })
  return res.data
}

// ---- SMTP（管理员）----

export interface SmtpConfig {
  source: 'database' | 'env' | 'none'
  enabled: boolean
  host: string
  port: number
  username: string
  from_email: string
  from_name: string
  use_tls: boolean
  use_ssl: boolean
  password_set: boolean
}

export type SmtpForm = Omit<SmtpConfig, 'source' | 'password_set'> & { password: string }

export async function getSmtp(): Promise<SmtpConfig> {
  const res: any = await ApiClient.get('/api/email-settings/smtp', undefined, { showLoading: false })
  return res.data
}

export const saveSmtp = (form: SmtpForm) => ApiClient.put('/api/email-settings/smtp', form)

export async function testSmtp(form: SmtpForm): Promise<{ success: boolean; message: string }> {
  const res: any = await ApiClient.post('/api/email-settings/smtp/test', form, { showLoading: false, skipErrorHandler: true })
  return { success: !!res.success, message: res.message || '' }
}
