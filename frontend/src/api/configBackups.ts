import request from './request'

export interface ConfigBackupJob {
  id: number
  status: 'pending' | 'running' | 'success' | 'partial_failed' | 'failed' | 'cancelled'
  trigger_type: 'manual' | 'scheduled'
  total_devices: number
  success_count: number
  failed_count: number
  summary?: string | null
  error_message?: string | null
  started_by?: string | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
}

export interface ConfigBackupResult {
  id: number
  job_id: number
  device_id: number
  device_name: string
  device_ip: string
  datacenter_name?: string | null
  device_type?: string | null
  vendor?: string | null
  model?: string | null
  status: 'pending' | 'running' | 'success' | 'failed'
  command?: string | null
  config_hash?: string | null
  line_count?: number | null
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
  config_content?: string | null
}

export interface ConfigSearchMatch {
  result_id: number
  job_id: number
  device_id: number
  device_name: string
  device_ip: string
  datacenter_name?: string | null
  device_type?: string | null
  vendor?: string | null
  model?: string | null
  line_number: number
  line: string
  context: Array<{ line_number: number; text: string }>
}

export interface ConfigBackupNotificationChannel {
  type?: string
  webhook?: string
  url?: string
}

export const triggerConfigBackup = async (): Promise<{ message: string; task_id?: string; job: ConfigBackupJob }> => {
  return await request.post('/config-backups/run') as { message: string; task_id?: string; job: ConfigBackupJob }
}

export const cancelConfigBackupJob = async (id: number): Promise<{ message: string; job: ConfigBackupJob | null }> => {
  return await request.post(`/config-backups/jobs/${id}/cancel`) as { message: string; job: ConfigBackupJob | null }
}

export const cancelRunningConfigBackupJob = async (): Promise<{ message: string; job: ConfigBackupJob | null }> => {
  return await request.post('/config-backups/jobs/cancel-running') as { message: string; job: ConfigBackupJob | null }
}

export const getConfigBackupJobs = async (params?: { skip?: number; limit?: number }): Promise<{ total: number; items: ConfigBackupJob[] }> => {
  return await request.get('/config-backups/jobs', { params }) as { total: number; items: ConfigBackupJob[] }
}

export const getLatestConfigBackupJob = async (): Promise<{ job: ConfigBackupJob | null }> => {
  return await request.get('/config-backups/jobs/latest') as { job: ConfigBackupJob | null }
}

export const getConfigBackupJob = async (id: number): Promise<ConfigBackupJob & { results: ConfigBackupResult[] }> => {
  return await request.get(`/config-backups/jobs/${id}`) as ConfigBackupJob & { results: ConfigBackupResult[] }
}

export const getConfigBackupResult = async (id: number): Promise<ConfigBackupResult> => {
  return await request.get(`/config-backups/results/${id}`) as ConfigBackupResult
}

export const searchConfigBackups = async (params: {
  keyword: string
  datacenter?: string
  device_ip?: string
  limit?: number
  context_lines?: number
}): Promise<{ total: number; items: ConfigSearchMatch[]; job: ConfigBackupJob | null }> => {
  return await request.get('/config-backups/search', { params }) as { total: number; items: ConfigSearchMatch[]; job: ConfigBackupJob | null }
}

export const getConfigBackupFilters = async (): Promise<{ datacenters: Array<{ name: string }> }> => {
  return await request.get('/config-backups/filters') as { datacenters: Array<{ name: string }> }
}

export const getConfigBackupSettings = async (): Promise<{ settings: { notification_channels: ConfigBackupNotificationChannel[] } }> => {
  return await request.get('/config-backups/settings') as { settings: { notification_channels: ConfigBackupNotificationChannel[] } }
}

export const saveConfigBackupSettings = async (payload: { notification_channels: ConfigBackupNotificationChannel[] }): Promise<{ message: string; settings: { notification_channels: ConfigBackupNotificationChannel[] } }> => {
  return await request.post('/config-backups/settings', payload) as { message: string; settings: { notification_channels: ConfigBackupNotificationChannel[] } }
}

export const testConfigBackupNotification = async (url: string): Promise<{ success: boolean; channel_type: string; message: string }> => {
  return await request.post('/config-backups/test-notification', { url }) as { success: boolean; channel_type: string; message: string }
}
