import request from './request'

export interface ControllerSettings {
  id: string
  name: string
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

export interface ControllerSettingsPayload {
  controllers: ControllerSettings[]
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

export interface ControllerOption {
  id: string
  name: string
  base_url: string
  enabled: boolean
}

export const getControllerSettings = () => request.get<ControllerSettingsPayload, ControllerSettingsPayload>('/controller/settings')

export const getControllerOptions = () => request.get<any, { items: ControllerOption[] }>('/controller/options')

export const updateControllerSettings = (payload: ControllerSettingsPayload) =>
  request.put<ControllerSettingsPayload, ControllerSettingsPayload>('/controller/settings', payload)

export const testController = (payload?: Partial<ControllerSettings>) =>
  request.post<ControllerTestResult, ControllerTestResult>('/controller/test', payload || {})

export const getControllerAssets = (params?: { page?: number; page_size?: number; search?: string; controller_id?: string }) =>
  request.get<any, { total: number; items: any[] }>('/controller/assets', { params })

export const getControllerOpticals = (params?: {
  page?: number
  page_size?: number
  search?: string
  device_ip?: string
  interface_name?: string
  vendor_name?: string
  level?: number
  hours?: number
  controller_id?: string
}) =>
  request.get<any, { total: number; items: any[] }>('/controller/opticals', { params })

export const getLosslessOverrunDevices = (params?: { controller_id?: string; hours?: number; tag?: string }) =>
  request.get<any, { total: number; items: any[]; raw?: any }>('/controller/lossless/overrun-devices', { params })

export const getLosslessBufferDetails = (params: {
  controller_id?: string
  asset_id: string
  page?: number
  page_size?: number
  hours?: number
  if_index?: string
  sort_column?: string
  order_type?: string
}) =>
  request.get<any, { total: number; items: any[]; page: number; pageSize: number }>('/controller/lossless/buffer-details', { params })
