import request from './request'

export interface ControllerSettings {
  enabled: boolean
  base_url: string
  username: string
  password?: string
  user_id: string
  region_id?: string
  effective_time: number
  timeout: number
  area_type: number
  insecure: boolean
}

export interface ControllerCheck {
  name: string
  ok: boolean
  detail: string
  elapsed_ms: number
  preview?: string
}

export interface ControllerTestResult {
  ok: boolean
  base_url: string
  checks: ControllerCheck[]
}

export const getControllerSettings = () => request.get<ControllerSettings, ControllerSettings>('/controller/settings')

export const updateControllerSettings = (payload: Partial<ControllerSettings>) =>
  request.put<ControllerSettings, ControllerSettings>('/controller/settings', payload)

export const testController = (payload?: Partial<ControllerSettings>) =>
  request.post<ControllerTestResult, ControllerTestResult>('/controller/test', payload || {})

export const getControllerAssets = (params?: { page?: number; page_size?: number; search?: string }) =>
  request.get<any, { total: number; items: any[] }>('/controller/assets', { params })

export const getControllerOpticals = (params?: { page?: number; page_size?: number; search?: string; device_ip?: string; hours?: number }) =>
  request.get<any, { total: number; items: any[] }>('/controller/opticals', { params })
