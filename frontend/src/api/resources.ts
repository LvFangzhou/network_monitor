import request from './request'

export interface Vendor {
  id: number
  name: string
  vendor_type: string
  contact_person?: string
  contact_phone?: string
  contact_email?: string
  service_scope?: string
  description?: string
  is_active: boolean
  created_at?: string
  updated_at?: string
}

export interface Customer {
  id: number
  name: string
  legal_name?: string
  customer_sites?: Array<{
    datacenter_id?: number
    datacenter_name?: string
    datacenter_code?: string
    private_network_entries?: Array<{
      prefix: string
      mask: string
      cidr: string
    }>
    public_address_entries?: Array<{
      prefix: string
      mask: string
      cidr: string
      provider_name?: string
      matched_circuit_name?: string
    }>
    bandwidth_description?: string
    description?: string
  }>
  private_networks?: string
  private_network_entries?: Array<{
    prefix: string
    mask: string
    cidr: string
  }>
  public_addresses?: string
  service_manager_name?: string
  service_manager_contact?: string
  sales_name?: string
  sales_contact?: string
  public_address_entries?: Array<{
    prefix: string
    mask: string
    cidr: string
    provider_name?: string
    matched_circuit_name?: string
  }>
  bandwidth_description?: string
  dedicated_lines?: string
  contact_info?: string
  contact_group?: string
  description?: string
  is_active: boolean
  customer_resources?: Array<{
    id: number
    name: string
    line_type: string
    status: string
    datacenter_name?: string
  }>
  created_at?: string
  updated_at?: string
}

export interface CustomerAudit {
  id: number
  customer_id: number
  customer_name?: string
  action: string
  actor_user_id?: number | null
  actor_username: string
  change_summary: Array<{
    field: string
    before: unknown
    after: unknown
  }>
  before_data?: Record<string, unknown> | null
  after_data?: Record<string, unknown> | null
  created_at?: string
}

export interface CustomerFlowPoint {
  _time?: string
  in_bps?: number | null
  out_bps?: number | null
  [key: string]: any
}

export interface CustomerFlowTraffic {
  customer_id: number
  customer_name: string
  cidr?: string | null
  available_cidrs: string[]
  range: string
  interval: string
  interval_seconds: number
  data: CustomerFlowPoint[]
  total: number
}

export interface Circuit {
  id: number
  name: string
  operator_name?: string
  line_type: string
  access_mode?: string
  ip_address?: string
  bandwidth_mbps: number
  physical_port_rate_gbps?: number
  primary_port_rate?: string
  secondary_port_rate?: string
  dual_link_mode?: string
  is_redundant: boolean
  redundancy_note?: string
  status: string
  datacenter_id?: number
  datacenter_name?: string
  vendor_id?: number
  vendor_name?: string
  customer_id?: number
  customer_name?: string
  primary_device_id?: number
  primary_device_name?: string
  primary_device_ip?: string
  primary_port_name?: string
  aggregation_monitor_device_id?: number
  aggregation_monitor_device_name?: string
  aggregation_monitor_device_ip?: string
  aggregation_interface_name?: string
  primary_local_interconnect_ip?: string
  primary_remote_interconnect_ip?: string
  primary_interconnect_type?: string
  primary_routing_mode?: string
  primary_bfd_mode?: string
  primary_interconnect_ip?: string
  primary_vlan_id?: number
  secondary_device_id?: number
  secondary_device_name?: string
  secondary_device_ip?: string
  secondary_port_name?: string
  secondary_local_interconnect_ip?: string
  secondary_remote_interconnect_ip?: string
  secondary_interconnect_type?: string
  secondary_routing_mode?: string
  secondary_bfd_mode?: string
  secondary_interconnect_ip?: string
  secondary_vlan_id?: number
  interconnect_address?: string
  local_interconnect_address?: string
  remote_interconnect_address?: string
  interconnect_type?: string
  routing_mode?: string
  bfd_mode?: string
  bfd_enabled?: boolean
  routed_cidrs?: string
  routed_networks?: Array<{
    prefix: string
    mask: string
  }>
  local_routed_cidrs?: string
  local_routed_networks?: Array<{
    prefix: string
    mask: string
  }>
  remote_routed_cidrs?: string
  remote_routed_networks?: Array<{
    prefix: string
    mask: string
  }>
  address_segments?: Array<{
    cidr: string
    segment_type: string
    usage?: string
    gateway_ip?: string
    is_public?: boolean
    description?: string
  }>
  segment_count?: number
  public_segment_count?: number
  description?: string
  created_at?: string
  updated_at?: string
}

export interface CircuitAudit {
  id: number
  circuit_id: number
  circuit_name?: string
  action: string
  actor_user_id?: number | null
  actor_username: string
  change_summary: Array<{
    field: string
    before: unknown
    after: unknown
  }>
  before_data?: Record<string, unknown> | null
  after_data?: Record<string, unknown> | null
  created_at?: string
}

export interface IPRecord {
  id: number
  ip_address: string
  prefix_length: number
  status: string
  usage_type: string
  datacenter_id?: number
  datacenter_name?: string
  circuit_id?: number
  circuit_name?: string
  description?: string
  created_at?: string
  updated_at?: string
}

export const getVendors = async (): Promise<{ total: number; items: Vendor[] }> => {
  return await request.get('/resources/vendors') as { total: number; items: Vendor[] }
}

export const getCustomers = async (search?: string): Promise<{ total: number; items: Customer[] }> => {
  const params = search ? { search } : undefined
  return await request.get('/resources/customers', { params }) as { total: number; items: Customer[] }
}

export const createCustomer = async (data: Partial<Customer>): Promise<Customer> => {
  return await request.post('/resources/customers', data) as Customer
}

export const updateCustomer = async (id: number, data: Partial<Customer>): Promise<Customer> => {
  return await request.put(`/resources/customers/${id}`, data) as Customer
}

export const deleteCustomer = async (id: number): Promise<void> => {
  await request.delete(`/resources/customers/${id}`)
}

export const getCustomerAudits = async (id: number): Promise<{ total: number; items: CustomerAudit[] }> => {
  return await request.get(`/resources/customers/${id}/audits`) as { total: number; items: CustomerAudit[] }
}

export const getCustomerFlowTraffic = async (
  id: number,
  params?: { range?: string; interval?: string; cidr?: string }
): Promise<CustomerFlowTraffic> => {
  return await request.get(`/resources/customers/${id}/flow-traffic`, { params }) as CustomerFlowTraffic
}

export const createVendor = async (data: Partial<Vendor>): Promise<Vendor> => {
  return await request.post('/resources/vendors', data) as Vendor
}

export const updateVendor = async (id: number, data: Partial<Vendor>): Promise<Vendor> => {
  return await request.put(`/resources/vendors/${id}`, data) as Vendor
}

export const deleteVendor = async (id: number): Promise<void> => {
  await request.delete(`/resources/vendors/${id}`)
}

export const getCircuits = async (params?: {
  skip?: number
  limit?: number
  datacenter_id?: number
  line_type?: string
  access_mode?: string
  customer_id?: number
  search?: string
}): Promise<{ total: number; items: Circuit[] }> => {
  return await request.get('/resources/circuits', { params }) as { total: number; items: Circuit[] }
}

export const createCircuit = async (data: Partial<Circuit>): Promise<Circuit> => {
  return await request.post('/resources/circuits', data) as Circuit
}

export const updateCircuit = async (id: number, data: Partial<Circuit>): Promise<Circuit> => {
  return await request.put(`/resources/circuits/${id}`, data) as Circuit
}

export const deleteCircuit = async (id: number): Promise<void> => {
  await request.delete(`/resources/circuits/${id}`)
}

export const getCircuitAudits = async (id: number): Promise<{ total: number; items: CircuitAudit[] }> => {
  return await request.get(`/resources/circuits/${id}/audits`) as { total: number; items: CircuitAudit[] }
}

export const getIPRecords = async (search?: string): Promise<{ total: number; items: IPRecord[] }> => {
  const params = search ? { search } : undefined
  return await request.get('/resources/ipdb', { params }) as { total: number; items: IPRecord[] }
}

export const createIPRecord = async (data: Partial<IPRecord>): Promise<IPRecord> => {
  return await request.post('/resources/ipdb', data) as IPRecord
}

export const updateIPRecord = async (id: number, data: Partial<IPRecord>): Promise<IPRecord> => {
  return await request.put(`/resources/ipdb/${id}`, data) as IPRecord
}

export const deleteIPRecord = async (id: number): Promise<void> => {
  await request.delete(`/resources/ipdb/${id}`)
}
