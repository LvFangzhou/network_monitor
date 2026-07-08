import request from './request'

export interface Datacenter {
  id: number
  code?: string
  name: string
  location?: string
  address?: string
  contact_person?: string
  contact_phone?: string
  contact_email?: string
  network_owner?: string
  network_owner_email?: string
  robot_mention?: string
  build_date?: string
  description?: string
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface DeviceType {
  id: number
  name: string
  display_name?: string
  icon?: string
  description?: string
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface DeviceRole {
  id: number
  name: string
  display_name?: string
  description?: string
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface DeviceVendor {
  id: number
  name: string
  display_name?: string
  description?: string
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface Device {
  id: number
  name: string
  ip_address: string
  hostname?: string
  device_type: string
  device_type_id?: number
  device_role?: string
  vendor?: string
  model?: string
  serial_number?: string
  location?: string
  latitude?: string
  longitude?: string
  rack?: string
  description?: string
  status: string
  is_monitored: boolean
  last_seen?: string
  created_at: string
  updated_at: string
  tags?: string[]
  snmp?: SNMPConfig
  gnmi?: GNMIConfig
  ssh?: SSHConfig
  datacenter_id?: number
  datacenter?: Datacenter
  network_owner?: string
  ops_owner?: string
  contact_phone?: string
  contact_email?: string
  business_type?: string
  monitor_source?: 'snmp' | 'asternos_exporter'
  prometheus_url?: string
  prometheus_job?: string
  prometheus_instance?: string
  custom_fields?: Record<string, any>
}

export interface SNMPConfig {
  version: string
  port: number
  community?: string
  username?: string
  auth_protocol?: string
  auth_password?: string
  priv_protocol?: string
  priv_password?: string
  security_level?: string
}

export interface GNMIConfig {
  enabled: boolean
  port: number
  username?: string
  password?: string
  tls_enabled: boolean
  tls_cert?: string
  skip_verify: boolean
  subscriptions?: Record<string, any>[]
}

export interface SSHConfig {
  port: number
  username?: string
  password?: string
  key?: string
}

export interface DeviceCreate {
  name: string
  status?: string
  ip_address: string
  hostname?: string
  device_type: string
  device_type_id?: number
  device_role?: string
  vendor?: string
  model?: string
  serial_number?: string
  location?: string
  datacenter_id?: number
  network_owner?: string
  ops_owner?: string
  contact_phone?: string
  contact_email?: string
  business_type?: string
  is_monitored?: boolean
  monitor_source?: 'snmp' | 'asternos_exporter'
  prometheus_url?: string
  prometheus_job?: string
  prometheus_instance?: string
  description?: string
  group_id?: number
  tags?: string[]
  snmp?: SNMPConfig
  gnmi?: GNMIConfig
  ssh?: SSHConfig
  custom_fields?: Record<string, any>
}

export interface DeviceListResponse {
  total: number
  items: Device[]
}

export interface ConnectionTestResult {
  success: boolean
  message: string
  latency?: number
  details?: any
}

export const getDevices = async (params?: {
  skip?: number
  limit?: number
  group_id?: number
  status?: string
  device_type?: string
  device_type_id?: number
  device_role?: string
  vendor?: string
  datacenter_id?: number
  rack?: string
  network_owner?: string
  ops_owner?: string
  business_type?: string
  is_monitored?: boolean
  search?: string
  search_mode?: 'fuzzy' | 'regex'
  name_text?: string
  ip_address_text?: string
  status_text?: string
  monitored_text?: string
  datacenter_text?: string
  model_text?: string
  device_type_text?: string
  serial_number_text?: string
  sort_by?: 'name' | 'ip_address' | 'status' | 'is_monitored' | 'datacenter' | 'model' | 'device_type' | 'serial_number'
  sort_order?: 'asc' | 'desc'
}): Promise<DeviceListResponse> => {
  return await request.get('/devices', { params }) as DeviceListResponse
}

export const getDeviceFilterOptions = async (): Promise<{
  datacenters: Array<{ id: number; name: string; code?: string; location?: string; contact_person?: string }>
  device_types: Array<{ id: number; name: string; display_name?: string }>
  device_roles: string[]
  vendors: string[]
  network_owners: string[]
  ops_owners: string[]
  business_types: string[]
  statuses: string[]
}> => {
  return await request.get('/devices/filters/options') as any
}

export const getDatacenters = async (): Promise<Datacenter[]> => {
  return await request.get('/devices/datacenters') as Datacenter[]
}

export const createDatacenter = async (data: Partial<Datacenter>): Promise<Datacenter> => {
  return await request.post('/devices/datacenters', data) as Datacenter
}

export const updateDatacenter = async (id: number, data: Partial<Datacenter>): Promise<Datacenter> => {
  return await request.put(`/devices/datacenters/${id}`, data) as Datacenter
}

export const deleteDatacenter = async (id: number): Promise<void> => {
  await request.delete(`/devices/datacenters/${id}`)
}

export const getDeviceTypesList = async (): Promise<DeviceType[]> => {
  return await request.get('/devices/device-types') as DeviceType[]
}

export const createDeviceType = async (data: Partial<DeviceType>): Promise<DeviceType> => {
  return await request.post('/devices/device-types', data) as DeviceType
}

export const updateDeviceType = async (id: number, data: Partial<DeviceType>): Promise<DeviceType> => {
  return await request.put(`/devices/device-types/${id}`, data) as DeviceType
}

export const deleteDeviceType = async (id: number): Promise<void> => {
  await request.delete(`/devices/device-types/${id}`)
}

export const getDeviceRolesList = async (): Promise<DeviceRole[]> => {
  return await request.get('/devices/device-roles') as DeviceRole[]
}

export const createDeviceRole = async (data: Partial<DeviceRole>): Promise<DeviceRole> => {
  return await request.post('/devices/device-roles', data) as DeviceRole
}

export const updateDeviceRole = async (id: number, data: Partial<DeviceRole>): Promise<DeviceRole> => {
  return await request.put(`/devices/device-roles/${id}`, data) as DeviceRole
}

export const deleteDeviceRole = async (id: number): Promise<void> => {
  await request.delete(`/devices/device-roles/${id}`)
}

export const getDeviceVendorsList = async (): Promise<DeviceVendor[]> => {
  return await request.get('/devices/device-vendors') as DeviceVendor[]
}

export const createDeviceVendor = async (data: Partial<DeviceVendor>): Promise<DeviceVendor> => {
  return await request.post('/devices/device-vendors', data) as DeviceVendor
}

export const updateDeviceVendor = async (id: number, data: Partial<DeviceVendor>): Promise<DeviceVendor> => {
  return await request.put(`/devices/device-vendors/${id}`, data) as DeviceVendor
}

export const deleteDeviceVendor = async (id: number): Promise<void> => {
  await request.delete(`/devices/device-vendors/${id}`)
}

export const getDevice = async (id: number): Promise<Device> => {
  return await request.get(`/devices/${id}`) as Device
}

export const createDevice = async (data: DeviceCreate): Promise<Device> => {
  return await request.post('/devices', data) as Device
}

export const updateDevice = async (id: number, data: Partial<DeviceCreate>): Promise<Device> => {
  return await request.put(`/devices/${id}`, data) as Device
}

export const deleteDevice = async (id: number): Promise<void> => {
  await request.delete(`/devices/${id}`)
}

export const batchDeleteDevices = async (
  deviceIds: number[]
): Promise<{ deleted: number; missing_ids: number[] }> => {
  return await request.post('/devices/bulk/delete', {
    device_ids: deviceIds,
  }) as { deleted: number; missing_ids: number[] }
}

export const batchUpdateDevices = async (payload: {
  device_ids: number[]
  field: string
  value?: string
  value_id?: number
}): Promise<{ updated: number; missing_ids: number[] }> => {
  return await request.post('/devices/bulk/update', payload) as { updated: number; missing_ids: number[] }
}

export const testDeviceConnection = async (
  id: number,
  type: 'ping' | 'snmp' | 'gnmi' | 'ssh'
): Promise<ConnectionTestResult> => {
  return await request.post(`/devices/${id}/test-connection`, { type }) as ConnectionTestResult
}

export const importDevices = async (file: File): Promise<{ imported: number; failed: number; errors: string[] }> => {
  const formData = new FormData()
  formData.append('file', file)
  return await request.post('/cmdb/devices/import', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  }) as { imported: number; failed: number; errors: string[] }
}

export const exportDevices = async (fields?: string[], filters?: {
  group_id?: number
  status?: string
  device_type?: string
  device_type_id?: number
  device_role?: string
  vendor?: string
  datacenter_id?: number
  is_monitored?: boolean
  search?: string
  search_mode?: 'fuzzy' | 'regex'
  name_text?: string
  ip_address_text?: string
  status_text?: string
  monitored_text?: string
  datacenter_text?: string
  model_text?: string
  device_type_text?: string
  serial_number_text?: string
}): Promise<Blob> => {
  return await request.get('/cmdb/devices/export', {
    params: {
      ...(filters || {}),
      ...(fields && fields.length > 0 ? { fields } : {}),
    },
    paramsSerializer: {
      indexes: null,
    },
    responseType: 'blob',
  }) as Blob
}

export const exportDeviceTemplate = async (): Promise<Blob> => {
  return await request.get('/cmdb/devices/template', {
    responseType: 'blob',
  }) as Blob
}

export const getDeviceTypes = async (): Promise<string[]> => {
  return await request.get('/cmdb/device-types') as string[]
}

export const getVendors = async (): Promise<string[]> => {
  return await request.get('/cmdb/vendors') as string[]
}
