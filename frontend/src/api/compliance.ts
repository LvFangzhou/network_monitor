import request from './request'

export type ComplianceStatus = 'compliant' | 'non_compliant' | 'pending' | 'not_monitored'
export type CheckStatus = 'passed' | 'failed' | 'pending' | 'skipped'

export interface DeviceModelProfile {
  id: number
  name: string
  vendor: string
  model_pattern: string
  network_type: string
  device_type?: string
  default_role?: string
  capabilities: Record<string, boolean>
  required_checks: string[]
  description?: string
  priority: number
  is_active: boolean
}

export interface VersionBaseline {
  id: number
  name: string
  model_profile_id?: number
  vendor?: string
  model_pattern?: string
  device_role?: string
  platform_version?: string
  allowed_releases: string[]
  allowed_versions: string[]
  minimum_version?: string
  required_patches: string[]
  forbidden_versions: string[]
  recommendation?: string
  priority: number
  is_active: boolean
}

export interface ComplianceCheck {
  key: string
  label: string
  status: CheckStatus
  message: string
  evidence?: any
  required: boolean
}

export interface DeviceCompliance {
  device_id: number
  model_profile_id?: number
  version_baseline_id?: number
  overall_status: ComplianceStatus
  score: number
  observed_vendor?: string
  observed_model?: string
  observed_version?: string
  observed_patches: string[]
  checks: ComplianceCheck[]
  blockers: Array<{ key: string; label: string; status: string; message: string }>
  evaluated_at: string
  profile?: DeviceModelProfile
  baseline?: VersionBaseline
  device: {
    id: number
    name: string
    ip_address: string
    vendor?: string
    model?: string
    device_role?: string
    is_monitored: boolean
    datacenter?: { id: number; name: string; code?: string }
  }
}

export const getModelProfiles = async () =>
  await request.get('/compliance/model-profiles') as { total: number; items: DeviceModelProfile[] }

export const createModelProfile = async (data: Partial<DeviceModelProfile>) =>
  await request.post('/compliance/model-profiles', data) as DeviceModelProfile

export const discoverModelProfiles = async () =>
  await request.post('/compliance/model-profiles/discover') as { created: number; skipped: number; items: DeviceModelProfile[] }

export const updateModelProfile = async (id: number, data: Partial<DeviceModelProfile>) =>
  await request.put(`/compliance/model-profiles/${id}`, data) as DeviceModelProfile

export const deleteModelProfile = async (id: number) => {
  await request.delete(`/compliance/model-profiles/${id}`)
}

export const getVersionBaselines = async () =>
  await request.get('/compliance/version-baselines') as { total: number; items: VersionBaseline[] }

export const createVersionBaseline = async (data: Partial<VersionBaseline>) =>
  await request.post('/compliance/version-baselines', data) as VersionBaseline

export const updateVersionBaseline = async (id: number, data: Partial<VersionBaseline>) =>
  await request.put(`/compliance/version-baselines/${id}`, data) as VersionBaseline

export const deleteVersionBaseline = async (id: number) => {
  await request.delete(`/compliance/version-baselines/${id}`)
}

export const getComplianceDevices = async (params?: {
  skip?: number
  limit?: number
  overall_status?: string
  vendor?: string
  datacenter_id?: number
  search?: string
  refresh?: boolean
}) => await request.get('/compliance/devices', { params }) as { total: number; items: DeviceCompliance[] }

export const getComplianceSummary = async () =>
  await request.get('/compliance/summary') as {
    total: number
    evaluated: number
    unevaluated: number
    counts: Record<string, number>
    compliance_rate: number
  }

export const getDeviceCompliance = async (deviceId: number, refresh = false) =>
  await request.get(`/compliance/devices/${deviceId}`, { params: { refresh } }) as DeviceCompliance

export const evaluateCompliance = async (deviceId?: number) =>
  await request.post('/compliance/evaluate', undefined, { params: deviceId ? { device_id: deviceId } : undefined }) as {
    total: number
    counts: Record<string, number>
  }
