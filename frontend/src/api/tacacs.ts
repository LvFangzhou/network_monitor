import request from './request'

export interface TacacsCommandLog {
  time: string
  device_ip: string
  username: string
  tty: string
  client_ip: string
  command: string
  raw: string
}

export interface TacacsConfigResponse {
  path: string
  exists: boolean
  content: string
  settings: TacacsSettings
}

export interface TacacsServiceStatus {
  status: 'running' | 'stopped' | 'not_found'
  running: boolean
  label: string
  message?: string
}

export interface TacacsCommandRule {
  name: string
  permit: string[]
  deny: string[]
}

export interface TacacsRole {
  name: string
  priv_lvl: number
  default_permit: boolean
  commands: TacacsCommandRule[]
}

export interface TacacsUser {
  username: string
  password: string
  role: string
}

export interface TacacsNotificationChannel {
  type: 'wechat' | 'dingtalk' | 'feishu' | 'webhook'
  webhook: string
}

export interface TacacsSettings {
  key: string
  accounting_file: string
  roles: TacacsRole[]
  users: TacacsUser[]
  notification_channels: TacacsNotificationChannel[]
}

export const getTacacsConfig = async (): Promise<TacacsConfigResponse> => {
  return await request.get('/tacacs/config') as TacacsConfigResponse
}

export const saveTacacsConfig = async (settings: TacacsSettings): Promise<TacacsConfigResponse> => {
  return await request.post('/tacacs/config', settings) as TacacsConfigResponse
}

export const saveTacacsNotifications = async (
  notification_channels: TacacsNotificationChannel[],
): Promise<{ message: string; settings: TacacsSettings }> => {
  return await request.post('/tacacs/notifications', { notification_channels }) as { message: string; settings: TacacsSettings }
}

export const getTacacsLogs = async (params?: {
  skip?: number
  limit?: number
  search?: string
  start_time?: string
  end_time?: string
  device_ip?: string
  username?: string
  command?: string
}): Promise<{ total: number; items: TacacsCommandLog[]; path: string }> => {
  return await request.get('/tacacs/logs', { params }) as { total: number; items: TacacsCommandLog[]; path: string }
}

export const getTacacsStatus = async (): Promise<TacacsServiceStatus> => {
  return await request.get('/tacacs/status') as TacacsServiceStatus
}

export const startTacacs = async (): Promise<TacacsServiceStatus> => {
  return await request.post('/tacacs/start') as TacacsServiceStatus
}

export const restartTacacs = async (): Promise<TacacsServiceStatus> => {
  return await request.post('/tacacs/restart') as TacacsServiceStatus
}

export const stopTacacs = async (): Promise<TacacsServiceStatus> => {
  return await request.post('/tacacs/stop') as TacacsServiceStatus
}

export const testTacacsNotification = async (url: string, channelIndex?: number): Promise<{ success: boolean; channel_type: string; message: string }> => {
  return await request.post('/tacacs/test-notification', { url, channel_index: channelIndex }) as { success: boolean; channel_type: string; message: string }
}
