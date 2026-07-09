import request from './request'

export interface DashboardAlert {
  id: number
  device_name: string
  device_ip: string
  message: string
  severity: string
  started_at?: string | null
}

export interface DashboardStats {
  total_devices: number
  online_devices: number
  offline_devices: number
  total_alerts_firing: number
  public_circuits: number
  private_circuits: number
  device_status_distribution?: Array<{ name: string; value: number }>
  asset_by_datacenter?: {
    devices: Array<{ name: string; value: number }>
    public_circuits: Array<{ name: string; value: number }>
    private_circuits: Array<{ name: string; value: number }>
  }
  snmp_metrics_count: number
  gnmi_metrics_count: number
  recent_alerts: DashboardAlert[]
}

export const getDashboardStats = async (): Promise<DashboardStats> => {
  return await request.get('/metrics/dashboard/stats') as DashboardStats
}

export interface ServerResourceStats {
  hostname: string
  platform: string
  timestamp: string
  uptime_seconds: number
  cpu: {
    percent: number
    cores: number
    physical_cores: number
    load_avg?: number[] | null
  }
  memory: {
    total: number
    used: number
    available: number
    percent: number
  }
  disk: {
    path: string
    total: number
    used: number
    free: number
    percent: number
  }
}

export const getServerResources = async (): Promise<ServerResourceStats> => {
  return await request.get('/metrics/server/resources') as ServerResourceStats
}

export interface MonitorDevice {
  id: number
  name: string
  ip_address: string
  hostname?: string
  device_type?: string
  device_role?: string
  vendor?: string
  model?: string
  status: string
  is_monitored: boolean
  monitor_source?: 'snmp' | 'asternos_exporter'
  prometheus_url?: string
  prometheus_job?: string
  prometheus_instance?: string
  datacenter?: {
    id: number
    name: string
    code?: string
  } | null
  snmp: {
    enabled: boolean
    version?: string
    port?: number
    community_configured?: boolean
    username?: string
    security_level?: string
  }
}

export interface MonitorDeviceSearchOption {
  id: number
  name: string
  ip_address: string
  device_role?: string
  vendor?: string
  model?: string
  status?: string
}

export const searchMonitorDevices = async (
  keyword: string
): Promise<MonitorDeviceSearchOption[]> => {
  const response = await request.get('/devices', {
    params: {
      search: keyword,
      limit: 8,
      status: 'active',
      is_monitored: true,
    },
  }) as {
    items: MonitorDeviceSearchOption[]
  }

  return response.items || []
}

export interface MonitorInterface {
  index: number
  name: string
  description?: string
  alias?: string
  admin_status?: string
  oper_status?: string
  speed_bps?: number | null
  in_octets?: number | null
  out_octets?: number | null
  in_discards?: number | null
  out_discards?: number | null
  in_errors?: number | null
  out_errors?: number | null
  queue_length?: number | null
  in_bps?: number | null
  out_bps?: number | null
  in_utilization_percent?: number | null
  out_utilization_percent?: number | null
  buffer_usage?: number | null
  buffer_usage_unit?: string
  sample_seconds?: number
  in_discards_delta?: number | null
  out_discards_delta?: number | null
  in_errors_delta?: number | null
  out_errors_delta?: number | null
  queue_egress_dropped_pkts_delta?: number | null
  queue_ingress_dropped_pkts_delta?: number | null
  pfc_rx_pkts_delta?: number | null
  pfc_tx_pkts_delta?: number | null
  ecn_marked_pkts_delta?: number | null
  asternos_counters?: Array<{
    field: string
    metric_base: string
    label: string
    target: string
    current: number
    previous?: number | null
    delta?: number | null
    labels?: Record<string, string>
  }>
}

export interface MonitorHistoryPoint {
  _time?: string
  in_bps?: number | null
  out_bps?: number | null
  in_utilization_percent?: number | null
  out_utilization_percent?: number | null
  in_discards?: number | null
  out_discards?: number | null
  in_errors?: number | null
  out_errors?: number | null
  in_discards_delta?: number | null
  out_discards_delta?: number | null
  in_errors_delta?: number | null
  out_errors_delta?: number | null
  queue_egress_dropped_pkts_delta?: number | null
  queue_ingress_dropped_pkts_delta?: number | null
  pfc_rx_pkts_delta?: number | null
  pfc_tx_pkts_delta?: number | null
  ecn_marked_pkts_delta?: number | null
  buffer_usage?: number | null
  [key: string]: any
}

export interface MonitorDynamicSeries {
  key: string
  label: string
  color: string
  field?: string
  target?: string
  queue?: string | null
  prio?: string | null
}

export const getMonitorDeviceByIp = async (ipAddress: string): Promise<MonitorDevice> => {
  return await request.get('/metrics/monitoring/devices/by-ip', {
    params: { ip_address: ipAddress },
  }) as MonitorDevice
}

export const getMonitorDeviceInterfaces = async (
  deviceId: number
): Promise<{ device: MonitorDevice; interfaces: MonitorInterface[]; total: number }> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/interfaces`) as {
    device: MonitorDevice
    interfaces: MonitorInterface[]
    total: number
  }
}

export const getMonitorInterfaceStats = async (
  deviceId: number,
  interfaceIndex: number,
  params?: { fresh?: boolean }
): Promise<{ device: MonitorDevice; interface: MonitorInterface; collected_at: string }> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/interfaces/${interfaceIndex}`, { params }) as {
    device: MonitorDevice
    interface: MonitorInterface
    collected_at: string
  }
}

export const getMonitorInterfaceHistory = async (
  deviceId: number,
  interfaceIndex: number,
  params: { range: string; interval: string; rate_window?: string; group?: string; start?: string; end?: string; start_ts?: number; end_ts?: number }
): Promise<{
  device: MonitorDevice
  interface_index: number
  range: string
  interval: string
  rate_window?: string
  data: MonitorHistoryPoint[]
  total: number
}> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/interfaces/${interfaceIndex}/history`, {
    params,
  }) as {
    device: MonitorDevice
    interface_index: number
    range: string
    interval: string
    rate_window?: string
    data: MonitorHistoryPoint[]
    total: number
  }
}

export const getMonitorInterfaceQueueHistory = async (
  deviceId: number,
  interfaceIndex: number,
  params: { group: string; range: string; interval: string }
): Promise<{
  device: MonitorDevice
  interface_index: number
  range: string
  interval: string
  series: MonitorDynamicSeries[]
  data: MonitorHistoryPoint[]
  total: number
  message?: string
}> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/interfaces/${interfaceIndex}/queue-history`, {
    params,
  }) as {
    device: MonitorDevice
    interface_index: number
    range: string
    interval: string
    series: MonitorDynamicSeries[]
    data: MonitorHistoryPoint[]
    total: number
    message?: string
  }
}

export const getAsterNOSDeviceSummary = async (
  deviceId: number
): Promise<{ device: MonitorDevice; summary: Record<string, any>; collected_at: string }> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/asternos/summary`) as {
    device: MonitorDevice
    summary: Record<string, any>
    collected_at: string
  }
}

export interface DeviceProtocolSummary {
  total: number
  up: number
  down: number
}

export interface DeviceOverviewItem {
  device: MonitorDevice
  monitor_source: 'snmp' | 'asternos_exporter'
  connectivity: {
    type: 'snmp' | 'exporter' | 'asternos_exporter' | string
    status: 'reachable' | 'unreachable' | 'unknown' | 'not_configured' | 'not_monitored' | string
    message?: string
  }
  resources: {
    cpu_percent?: number | null
    memory_percent?: number | null
    temperature?: number | null
    temperature_details?: Array<{
      sensor?: string | null
      temperature?: number | null
    }>
    storage_percent?: number | null
  }
  sessions?: {
    current?: number | null
    total?: number | null
    usage_percent?: number | null
  }
  hardware?: {
    fan_total?: number
    fan_down?: number
    fan_status_known?: boolean
    power_total?: number
    power_down?: number
    power_status_known?: boolean
  }
  protocols: {
    bgp: DeviceProtocolSummary
    ospf: DeviceProtocolSummary
  }
  data_sources?: {
    resources?: Record<string, string>
    protocols?: Record<string, string>
    system_info?: Record<string, string>
  }
  system_info?: {
    sys_name?: string | null
    sys_descr?: string | null
    software_version?: string | null
    snmp_model?: string | null
    serial_number?: string | null
    platform_name?: string | null
    uptime_seconds?: number | null
  }
  collected_at: string
}

export const getDeviceOverview = async (params?: {
  search?: string
  vendor?: string
  model?: string
  connectivity?: string
  monitored_only?: boolean
  include_storage?: boolean
  include_hardware?: boolean
  include_sessions?: boolean
  limit?: number
}): Promise<{ items: DeviceOverviewItem[]; total: number }> => {
  return await request.get('/metrics/monitoring/devices/overview', { params }) as {
    items: DeviceOverviewItem[]
    total: number
  }
}

export const refreshDeviceOverview = async (): Promise<{
  message: string
  tasks: { snmp?: string; asternos_exporter?: string }
}> => {
  return await request.post('/metrics/monitoring/devices/refresh') as {
    message: string
    tasks: { snmp?: string; asternos_exporter?: string }
  }
}

export const refreshMonitorDevice = async (
  deviceId: number
): Promise<{
  message: string
  device_id: number
  monitor_source: string
  task_id: string
}> => {
  return await request.post(`/metrics/monitoring/devices/${deviceId}/refresh`) as {
    message: string
    device_id: number
    monitor_source: string
    task_id: string
  }
}

export interface IpFlowPoint {
  _time?: string
  time?: string
  in_bps?: number | null
  out_bps?: number | null
  [key: string]: any
}

export interface IpFlowTraffic {
  ip: string
  cidr: string
  customers: Array<{ id: number; name: string }>
  source?: 'customer' | 'sflow_interface' | string
  range: string
  interval: string
  interval_seconds: number
  data: IpFlowPoint[]
  total: number
}

export const getIpFlowTraffic = async (params: {
  ip: string
  range: string
  interval: string
}): Promise<IpFlowTraffic> => {
  return await request.get('/metrics/flow/ip-traffic', { params }) as IpFlowTraffic
}

export interface InterfaceTopIpItem {
  ip: string
  in_bps?: number | null
  out_bps?: number | null
  total_bps?: number | null
  [key: string]: any
}

export const getInterfaceTopIps = async (params: {
  agent_ip: string
  interface_index: number
  range: string
  interval: string
  limit?: number
}): Promise<{
  agent_ip: string
  interface_index: number
  range: string
  interval: string
  interval_seconds: number
  items: InterfaceTopIpItem[]
  total: number
}> => {
  return await request.get('/metrics/flow/interface-top-ips', { params }) as {
    agent_ip: string
    interface_index: number
    range: string
    interval: string
    interval_seconds: number
    items: InterfaceTopIpItem[]
    total: number
  }
}

export interface SflowInterfaceOption {
  interface_index: number | string
  label: string
  interface_name?: string | null
  alias?: string | null
  admin_status?: string | null
  oper_status?: string | null
  speed_bps?: number | null
  device?: {
    id: number
    name: string
    ip_address: string
    vendor?: string | null
    model?: string | null
    datacenter?: { id: number; name: string; code?: string | null } | null
  } | null
  circuit?: {
    id: number
    name: string
    operator_name?: string | null
    line_type?: string | null
    bandwidth_mbps?: number | null
    status?: string | null
  } | null
  total_bps?: number | null
  last_seen?: string | null
}

export interface SflowAgentOption {
  agent_ip: string
  interface_count: number
  total_bps?: number | null
  last_seen?: string | null
  device?: {
    id: number
    name: string
    ip_address: string
    vendor?: string | null
    model?: string | null
    datacenter?: { id: number; name: string; code?: string | null } | null
  } | null
  circuits?: Array<{
    id: number
    name: string
    operator_name?: string | null
    line_type?: string | null
    bandwidth_mbps?: number | null
    status?: string | null
  } | null>
}

export const getSflowAgents = async (params?: {
  range?: string
}): Promise<{
  range: string
  items: SflowAgentOption[]
  total: number
}> => {
  return await request.get('/metrics/flow/sflow-agents', { params }) as {
    range: string
    items: SflowAgentOption[]
    total: number
  }
}

export const getSflowInterfaces = async (params: {
  agent_ip: string
  range?: string
}): Promise<{
  agent_ip: string
  range: string
  items: SflowInterfaceOption[]
  total: number
}> => {
  return await request.get('/metrics/flow/sflow-interfaces', { params }) as {
    agent_ip: string
    range: string
    items: SflowInterfaceOption[]
    total: number
  }
}

export interface InterfaceIpSeriesPoint {
  _time?: string
  time?: string
  ip: string
  _value?: number | null
  value?: number | null
  [key: string]: any
}

export const getInterfaceIpSeries = async (params: {
  agent_ip: string
  interface_index: number
  range: string
  interval: string
  limit?: number
  ip?: string
}): Promise<{
  agent_ip: string
  interface_index: number
  range: string
  interval: string
  interval_seconds: number
  top_ips: InterfaceTopIpItem[]
  selected_ip?: string | null
  selected_rank?: number | null
  series: InterfaceIpSeriesPoint[]
  total: number
}> => {
  return await request.get('/metrics/flow/interface-ip-series', { params }) as {
    agent_ip: string
    interface_index: number
    range: string
    interval: string
    interval_seconds: number
    top_ips: InterfaceTopIpItem[]
    selected_ip?: string | null
    selected_rank?: number | null
    series: InterfaceIpSeriesPoint[]
    total: number
  }
}

export interface ProtocolNeighbor {
  protocol: 'bgp' | 'ospf' | string
  peer: string
  neighbor?: string | null
  remote_as?: string | number | null
  local_addr?: string | null
  local_address?: string | null
  interface?: string | null
  local_port?: string | null
  local_port_id?: string | null
  local_port_num?: string | null
  remote_system?: string | null
  remote_display_name?: string | null
  remote_port?: string | null
  remote_port_id?: string | null
  remote_chassis_id?: string | null
  remote_mgmt_addr?: string | null
  remote_mgmt_addr_source?: string | null
  remote_kind?: string | null
  remote_sys_desc?: string | null
  state: string
  status: 'up' | 'down' | string
  duration_seconds?: number | null
  duration_text?: string | null
  source?: string
}

export const getDeviceProtocolNeighbors = async (
  deviceId: number
): Promise<{
  device: MonitorDevice
  neighbors: {
    bgp: ProtocolNeighbor[]
    ospf: ProtocolNeighbor[]
    lldp: ProtocolNeighbor[]
  }
  collected_at: string
}> => {
  return await request.get(`/metrics/monitoring/devices/${deviceId}/protocol-neighbors`) as {
    device: MonitorDevice
    neighbors: {
      bgp: ProtocolNeighbor[]
      ospf: ProtocolNeighbor[]
      lldp: ProtocolNeighbor[]
    }
    collected_at: string
  }
}

export interface MetricPoint {
  time?: string
  measurement?: string
  field?: string
  value?: string | number | boolean | null
  device_id?: string
  [key: string]: any
}

export const getMeasurements = async (): Promise<{ measurements: string[] }> => {
  return await request.get('/metrics/measurements') as { measurements: string[] }
}

export const queryMetrics = async (params: {
  measurement: string
  device_id?: number
  field?: string
  start?: string
  stop?: string
  aggregation?: string
  interval?: string
  limit?: number
}): Promise<{ data: MetricPoint[]; total: number }> => {
  return await request.get('/metrics/query', { params }) as { data: MetricPoint[]; total: number }
}

export interface QualityProbeTarget {
  id: number
  name: string
  target: string
  datacenter_id?: number | null
  datacenter_name?: string | null
  operator_name?: string | null
  interval_seconds: number
  packet_count: number
  timeout_ms: number
  latency_threshold_ms: number
  loss_threshold_percent: number
  jitter_threshold_ms: number
  description?: string | null
  is_active: boolean
  last_probe_at?: string | null
  last_success?: boolean | null
  last_avg_latency_ms?: number | null
  last_packet_loss_percent?: number | null
  last_jitter_ms?: number | null
  last_error?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface QualityProbeResult {
  success: boolean
  avg_latency_ms?: number | null
  min_latency_ms?: number | null
  max_latency_ms?: number | null
  jitter_ms?: number | null
  packet_loss_percent?: number | null
  availability_percent?: number | null
  received: number
  sent: number
  error?: string | null
}

export interface QualityProbeHistoryPoint {
  _time?: string
  avg_latency_ms?: number | null
  min_latency_ms?: number | null
  max_latency_ms?: number | null
  jitter_ms?: number | null
  packet_loss_percent?: number | null
  availability_percent?: number | null
}

export const getQualityProbeTargets = async (params?: {
  search?: string
  active?: boolean
}): Promise<{ total: number; items: QualityProbeTarget[] }> => {
  return await request.get('/metrics/quality/probe-targets', { params }) as { total: number; items: QualityProbeTarget[] }
}

export const createQualityProbeTarget = async (
  data: Partial<QualityProbeTarget>
): Promise<QualityProbeTarget> => {
  return await request.post('/metrics/quality/probe-targets', data) as QualityProbeTarget
}

export const updateQualityProbeTarget = async (
  id: number,
  data: Partial<QualityProbeTarget>
): Promise<QualityProbeTarget> => {
  return await request.put(`/metrics/quality/probe-targets/${id}`, data) as QualityProbeTarget
}

export const deleteQualityProbeTarget = async (id: number): Promise<void> => {
  await request.delete(`/metrics/quality/probe-targets/${id}`)
}

export const testQualityProbeTarget = async (
  id: number
): Promise<{ target: QualityProbeTarget; result: QualityProbeResult }> => {
  return await request.post(`/metrics/quality/probe-targets/${id}/test`) as {
    target: QualityProbeTarget
    result: QualityProbeResult
  }
}

export const runQualityProbeMtr = async (
  id: number
): Promise<{ target: QualityProbeTarget; generated_at: string; command: string; output: string; tool: string }> => {
  return await request.post(`/metrics/quality/probe-targets/${id}/mtr`) as {
    target: QualityProbeTarget
    generated_at: string
    command: string
    output: string
    tool: string
  }
}

export const getQualityProbeHistory = async (
  id: number,
  params: { range: string; interval: string }
): Promise<{
  target: QualityProbeTarget
  range: string
  interval: string
  data: QualityProbeHistoryPoint[]
  total: number
}> => {
  return await request.get(`/metrics/quality/probe-targets/${id}/history`, { params }) as {
    target: QualityProbeTarget
    range: string
    interval: string
    data: QualityProbeHistoryPoint[]
    total: number
  }
}
