import request from './request'

export interface NotificationChannel {
  type: 'wechat' | 'dingtalk' | 'feishu' | 'email' | 'webhook'
  config: Record<string, any>
}

export interface AlertRule {
  id: number
  name: string
  description?: string | null
  rule_type: 'threshold' | 'change_rate' | 'duration'
  metric_type: string
  condition: '>' | '>=' | '<' | '<=' | '==' | '!='
  threshold: number
  duration: number
  severity: 'P0' | 'P1' | 'P2' | 'P3' | 'critical' | 'warning' | 'info'
  suppress_duration?: number
  enabled: boolean
  device_group_id?: number | null
  device_ids?: number[]
  extra_config?: Record<string, any>
  applicable_vendors?: string[]
  notification_channels: NotificationChannel[]
  created_at?: string
  updated_at?: string | null
}

export interface AlertHistory {
  id: number
  alarm_id?: string | null
  rule_id: number
  rule_name?: string | null
  device_id?: number | null
  device_name?: string | null
  device_ip?: string | null
  alert_value?: number | null
  threshold?: number | null
  message?: string | null
  alert_target_type?: string | null
  alert_target_key?: string | null
  alert_target_name?: string | null
  status: 'firing' | 'resolved' | 'acknowledged' | 'ignored' | 'snoozed'
  severity?: 'P0' | 'P1' | 'P2' | 'P3' | 'critical' | 'warning' | 'info' | null
  acknowledged_by?: string | null
  acknowledged_at?: string | null
  ignored_by?: string | null
  ignored_at?: string | null
  resolved_by?: string | null
  current_handler?: string | null
  started_at?: string | null
  resolved_at?: string | null
  resolution_note?: string | null
}

export interface AlertStats {
  total_firing: number
  total_resolved: number
  by_severity: Record<string, number>
  by_device: Record<string, number>
}

export interface AlertHistorySummary {
  total: number
  protected_total?: number
  deletable_total?: number
  datacenters: Array<{ name: string; count: number }>
  days: Array<{ day: string; count: number }>
  devices: Array<{ device_id: number; device_name: string; device_ip?: string | null; count: number }>
  statuses?: Record<string, number>
  severities?: Record<string, number>
}

export interface AlertSilence {
  id: number
  name: string
  rule_id?: number | null
  device_id?: number | null
  device_name?: string | null
  target_pattern?: string | null
  include_device_ip?: string | null
  include_interface?: string | null
  include_message?: string | null
  exclude_device_ip?: string | null
  exclude_interface?: string | null
  exclude_message?: string | null
  starts_at?: string | null
  conditions?: Array<{ field: string; operator: string; value: string }> | null
  reason?: string | null
  created_by?: string | null
  enabled: boolean
  expires_at?: string | null
  matched_active_alerts?: number | null
  matched_total_alerts?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AlertSilencePayload {
  name: string
  rule_id?: number | null
  device_id?: number | null
  target_pattern?: string | null
  include_device_ip?: string | null
  include_interface?: string | null
  include_message?: string | null
  exclude_device_ip?: string | null
  exclude_interface?: string | null
  exclude_message?: string | null
  starts_at?: string | null
  conditions?: Array<{ field: string; operator: string; value: string }>
  reason?: string | null
  enabled: boolean
  expires_at?: string | null
  actor_username?: string | null
}

export interface AlertRulePayload {
  name: string
  description?: string
  rule_type: 'threshold' | 'change_rate' | 'duration'
  metric_type: string
  condition: '>' | '>=' | '<' | '<=' | '==' | '!='
  threshold: number
  duration: number
  severity: 'P0' | 'P1' | 'P2' | 'P3'
  enabled: boolean
  device_group_id?: number | null
  device_ids: number[]
  extra_config?: Record<string, any>
  notification_channels: NotificationChannel[]
  suppress_duration?: number
}

export interface AlertRuleStatusItem {
  rule_id: number
  device_id: number
  device_name?: string | null
  device_ip?: string | null
  monitor_source?: string | null
  target_type?: string | null
  target_key?: string | null
  target_name?: string | null
  value?: number | null
  condition: string
  status: 'normal' | 'alert' | 'no_data'
  state_text?: string | null
  message?: string | null
}

export interface AlertRuleStatusResponse {
  rule: AlertRule
  summary: {
    total: number
    normal: number
    alert: number
    no_data: number
  }
  items: AlertRuleStatusItem[]
  limit: number
  truncated: boolean
  partial?: boolean
  evaluated_at: string
  cached?: boolean
  cache_ttl_seconds?: number
}

export interface SyslogEvent {
  id: number
  device_id?: number | null
  source_ip?: string | null
  source_host?: string | null
  facility?: number | null
  severity?: number | null
  app_name?: string | null
  message: string
  raw_message: string
  created_at: string
}

export const getAlertRules = async (params?: {
  skip?: number
  limit?: number
  enabled?: boolean
  severity?: string
  search?: string
  vendor?: string
}): Promise<{ total: number; items: AlertRule[] }> => {
  return await request.get('/alerts/rules', { params }) as { total: number; items: AlertRule[] }
}

export const createAlertRule = async (data: AlertRulePayload): Promise<AlertRule> => {
  return await request.post('/alerts/rules', data) as AlertRule
}

export const updateAlertRule = async (id: number, data: Partial<AlertRulePayload>): Promise<AlertRule> => {
  return await request.put(`/alerts/rules/${id}`, data) as AlertRule
}

export const deleteAlertRule = async (id: number): Promise<void> => {
  await request.delete(`/alerts/rules/${id}`)
}

export const getAlertRuleStatus = async (
  id: number,
  params?: {
    search?: string
    status?: 'normal' | 'alert' | 'no_data'
    limit?: number
    refresh?: boolean
    max_runtime_seconds?: number
  }
): Promise<AlertRuleStatusResponse> => {
  return await request.get(`/alerts/rules/${id}/status`, { params }) as AlertRuleStatusResponse
}

export const getAlertHistory = async (params?: {
  skip?: number
  limit?: number
  view?: 'active' | 'audit'
  status?: string
  device_id?: number
  rule_id?: number
  alert_id?: number
  alarm_id?: string
  severity?: string
  datacenter?: string
  search?: string
}): Promise<{ total: number; items: AlertHistory[] }> => {
  return await request.get('/alerts/history', { params }) as { total: number; items: AlertHistory[] }
}

export const getAlertHistorySummary = async (params?: {
  view?: 'active' | 'audit'
  status?: string
  device_id?: number
  rule_id?: number
  alert_id?: number
  alarm_id?: string
  severity?: string
  datacenter?: string
  search?: string
  older_than_days?: number
  limit?: number
}): Promise<AlertHistorySummary> => {
  return await request.get('/alerts/history/summary', { params }) as AlertHistorySummary
}

export const clearAlertHistory = async (data: {
  view?: 'active' | 'audit'
  status?: string
  device_id?: number
  rule_id?: number
  alert_id?: number
  alarm_id?: string
  severity?: string
  datacenter?: string
  search?: string
  older_than_days?: number
  include_active?: boolean
  confirm_text: string
  actor_username?: string
}): Promise<{ deleted_count: number; protected_skipped: number }> => {
  return await request.post('/alerts/history/clear', data) as { deleted_count: number; protected_skipped: number }
}

export const acknowledgeAlert = async (id: number, note?: string, actor_username?: string): Promise<AlertHistory> => {
  return await request.post(`/alerts/history/${id}/acknowledge`, { note: note || '', actor_username: actor_username || '' }) as AlertHistory
}

export const ignoreAlert = async (id: number, note?: string, actor_username?: string): Promise<AlertHistory> => {
  return await request.post(`/alerts/history/${id}/ignore`, { note: note || '', actor_username: actor_username || '' }) as AlertHistory
}

export const resolveAlert = async (id: number, note?: string, actor_username?: string): Promise<AlertHistory> => {
  return await request.post(`/alerts/history/${id}/resolve`, { note: note || '', actor_username: actor_username || '' }) as AlertHistory
}

export const quickSilenceAlert = async (
  id: number,
  durationHours: number,
  actorUsername?: string,
): Promise<{ alert: AlertHistory; silence: AlertSilence; expires_at: string }> => {
  return await request.post(`/alerts/history/${id}/quick-silence`, {
    duration_hours: durationHours,
    actor_username: actorUsername || '',
  }) as { alert: AlertHistory; silence: AlertSilence; expires_at: string }
}

export const getAlertStats = async (): Promise<AlertStats> => {
  return await request.get('/alerts/stats') as AlertStats
}

export const getSyslogEvents = async (params?: {
  skip?: number
  limit?: number
  device_id?: number
  search?: string
}): Promise<{ total: number; items: SyslogEvent[] }> => {
  return await request.get('/alerts/syslog', { params }) as { total: number; items: SyslogEvent[] }
}

export const testAlertNotification = async (url: string, mentionUsers?: string[]): Promise<{ success: boolean; channel_type: string; message: string }> => {
  return await request.post('/alerts/test-notification', { url, mention_users: mentionUsers || [] }) as { success: boolean; channel_type: string; message: string }
}

export const getAlertSilences = async (params?: {
  skip?: number
  limit?: number
  enabled?: boolean
  include_match_counts?: boolean
  include_total_match_counts?: boolean
}): Promise<{ total: number; items: AlertSilence[] }> => {
  return await request.get('/alerts/silences', { params }) as { total: number; items: AlertSilence[] }
}

export const getAlertSilenceMatches = async (
  id: number,
  params?: { skip?: number; limit?: number; active_only?: boolean; rule_id?: number }
): Promise<{ total: number; total_exact?: boolean; items: AlertHistory[]; rule_filters?: Array<{ text: string; value: string }> }> => {
  return await request.get(`/alerts/silences/${id}/matches`, { params }) as {
    total: number
    total_exact?: boolean
    items: AlertHistory[]
    rule_filters?: Array<{ text: string; value: string }>
  }
}

export const getAlertSilenceMatchCounts = async (
  id: number,
  params?: { include_total?: boolean }
): Promise<{
  silence_id: number
  active: { count: number | null; cached: boolean; pending: boolean }
  total: { count: number | null; cached: boolean; pending: boolean }
  pending: boolean
}> => {
  return await request.get(`/alerts/silences/${id}/match-counts`, { params }) as {
    silence_id: number
    active: { count: number | null; cached: boolean; pending: boolean }
    total: { count: number | null; cached: boolean; pending: boolean }
    pending: boolean
  }
}

export const createAlertSilence = async (data: AlertSilencePayload): Promise<AlertSilence> => {
  return await request.post('/alerts/silences', data) as AlertSilence
}

export const updateAlertSilence = async (id: number, data: Partial<AlertSilencePayload>): Promise<AlertSilence> => {
  return await request.put(`/alerts/silences/${id}`, data) as AlertSilence
}

export const deleteAlertSilence = async (id: number): Promise<void> => {
  await request.delete(`/alerts/silences/${id}`)
}
