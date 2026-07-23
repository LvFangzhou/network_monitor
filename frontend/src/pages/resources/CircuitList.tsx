import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
  theme,
} from 'antd'
import React from 'react'
import { DeleteOutlined, EditOutlined, MinusCircleOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { getDatacenters, getDevices, type Datacenter, type Device } from '../../api/devices'
import { getMonitorDeviceInterfaces, type MonitorInterface } from '../../api/metrics'
import {
  createCircuit,
  getCustomers,
  deleteCircuit,
  getCircuitAudits,
  getCircuits,
  getVendors,
  updateCircuit,
  type Circuit,
  type CircuitAudit,
  type Customer,
  type Vendor,
} from '../../api/resources'
import { useAuthStore } from '../../store/auth'

const operatorOptions = ['联通', '移动', '电信', '广电', 'BGP', '其他']
const physicalPortRateOptions = [
  { value: '100M', label: '100M' },
  { value: '1000M', label: '1000M' },
  { value: '10G', label: '10G' },
  { value: '25G', label: '25G' },
  { value: '100G', label: '100G' },
]

const parsePortRateMbps = (rate?: string) => {
  const match = String(rate || '').trim().match(/^([\d.]+)\s*([GMK]?)(?:BPS|B)?$/i)
  if (!match) return null
  const value = Number(match[1])
  if (!Number.isFinite(value)) return null
  const unit = match[2].toUpperCase()
  if (unit === 'G') return value * 1000
  if (unit === 'K') return value / 1000
  return value
}

const getAggregatedPortRate = (primaryRate?: string, secondaryRate?: string) => {
  const primaryMbps = parsePortRateMbps(primaryRate)
  const secondaryMbps = parsePortRateMbps(secondaryRate)
  if (primaryMbps === null || secondaryMbps === null) {
    return primaryRate || secondaryRate || '-'
  }
  const totalMbps = primaryMbps + secondaryMbps
  if (totalMbps >= 1000) {
    const totalGbps = totalMbps / 1000
    return `${Number.isInteger(totalGbps) ? totalGbps.toFixed(0) : totalGbps.toFixed(1)}G`
  }
  return `${Number.isInteger(totalMbps) ? totalMbps.toFixed(0) : totalMbps.toFixed(1)}M`
}
const dualLinkModeOptions = [
  { value: 'lacp', label: 'LACP（逻辑单线）' },
  { value: 'cold_standby', label: '冷备' },
  { value: 'hot_standby', label: '热备' },
]
const lineTypeOptions = [
  { value: 'internet', label: '互联网线路' },
  { value: 'private_line', label: '专线线路' },
]
const accessModeOptions = [
  { value: 'single', label: '单线接入' },
  { value: 'dual', label: '双线接入' },
]
const segmentTypeOptions = [
  { value: 'interconnect', label: '互联地址' },
  { value: 'business', label: '业务地址' },
  { value: 'public', label: '公网地址' },
  { value: 'management', label: '管理地址' },
  { value: 'other', label: '其他' },
]
const segmentTypeLabelMap = Object.fromEntries(segmentTypeOptions.map((item) => [item.value, item.label]))
const segmentTypeColorMap: Record<string, string> = {
  interconnect: 'blue',
  business: 'green',
  public: 'volcano',
  management: 'purple',
  other: 'default',
}
const auditFieldLabelMap: Record<string, string> = {
  name: '线路名称',
  operator_name: '运营商',
  line_type: '线路类型',
  access_mode: '接入方式',
  ip_address: '线路主IP',
  bandwidth_mbps: '带宽',
  physical_port_rate_gbps: '物理端口速率',
  primary_port_rate: '端口速率',
  secondary_port_rate: '端口速率',
  dual_link_mode: '双线接入策略',
  is_redundant: '是否冗余',
  redundancy_note: '冗余说明',
  status: '状态',
  datacenter_id: '所属机房',
  vendor_id: '供应商',
  customer_id: '客户',
  primary_device_id: '主接入交换机',
  primary_port_name: '主接入端口',
  aggregation_monitor_device_id: '逻辑聚合接口设备',
  aggregation_interface_name: '逻辑聚合接口',
  primary_local_interconnect_ip: '主本端地址',
  primary_remote_interconnect_ip: '主对端地址',
  primary_interconnect_type: '主互联方式',
  primary_routing_mode: '主路由协议',
  primary_bfd_mode: '主探测方式',
  primary_interconnect_ip: '主接入互联IP',
  primary_vlan_id: '主接入VLAN ID',
  secondary_device_id: '备接入交换机',
  secondary_port_name: '备接入端口',
  secondary_local_interconnect_ip: '备本端地址',
  secondary_remote_interconnect_ip: '备对端地址',
  secondary_interconnect_type: '备互联方式',
  secondary_routing_mode: '备路由协议',
  secondary_bfd_mode: '备探测方式',
  secondary_interconnect_ip: '备接入互联IP',
  secondary_vlan_id: '备接入VLAN ID',
  interconnect_address: '互联地址',
  local_interconnect_address: '本端互联地址',
  remote_interconnect_address: '对端互联地址',
  interconnect_type: '互联方式',
  routing_mode: '路由对接方式',
  bfd_mode: '探测方式',
  bfd_enabled: 'BFD',
  routed_cidrs: '路由CIDR',
  routed_networks: '路由网段',
  local_routed_cidrs: '本地IDC内网段',
  local_routed_networks: '本地IDC内网段',
  remote_routed_cidrs: '对端IDC内网段',
  remote_routed_networks: '对端IDC内网段',
  address_segments: '地址段',
  description: '备注',
}
const lineTypeLabelMap = Object.fromEntries(lineTypeOptions.map((item) => [item.value, item.label]))
const accessModeLabelMap = Object.fromEntries(accessModeOptions.map((item) => [item.value, item.label]))
const dualLinkModeLabelMap = Object.fromEntries(dualLinkModeOptions.map((item) => [item.value, item.label]))
const statusLabelMap: Record<string, string> = {
  active: '启用',
  inactive: '停用',
}
const CIRCUIT_VISIBLE_COLUMNS_KEY = 'resource-circuit-list-visible-columns-v3'
const DEFAULT_VISIBLE_COLUMNS = [
  'name',
  'datacenter_name',
  'operator_name',
  'line_type',
  'access_mode',
  'termination',
  'physical_port_rate_gbps',
  'bandwidth_mbps',
  'segments',
  'status',
  'action',
]
const COLUMN_ORDER = [
  'name',
  'datacenter_name',
  'operator_name',
  'line_type',
  'vendor_name',
  'customer_name',
  'access_mode',
  'termination',
  'physical_port_rate_gbps',
  'bandwidth_mbps',
  'local_interconnect_address',
  'remote_interconnect_address',
  'interconnect_address',
  'routing_mode',
  'bfd_enabled',
  'routed_cidrs',
  'segments',
  'status',
  'action',
]
const TABLE_TEXT_STYLE: React.CSSProperties = {
  display: 'block',
  width: '100%',
  fontSize: 12,
  lineHeight: 1.45,
  fontWeight: 600,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const routingModeOptions = [
  { value: 'static', label: 'static' },
  { value: 'bgp', label: 'BGP' },
  { value: 'ospf', label: 'OSPF' },
  { value: 'isis', label: 'ISIS' },
  { value: 'other', label: '其他' },
]
const interconnectTypeOptions = [
  { value: 'l2', label: '二层互联（Vlan-if）' },
  { value: 'l3', label: '三层互联（物理接口）' },
]
const bfdModeOptions = [
  { value: 'none', label: '无' },
  { value: 'bfd', label: 'BFD' },
  { value: 'track', label: 'Track' },
]
const bfdModeLabelMap = Object.fromEntries(bfdModeOptions.map((item) => [item.value, item.label]))

type CircuitListProps = {
  title?: string
  fixedLineType?: 'internet' | 'private_line'
}

type AggregationOption = {
  value: string
  label: string
  interfaceName: string
  monitorDeviceId: number | null
  presentOnPrimary: boolean
  presentOnSecondary: boolean
}

const CircuitList = ({ title = '公网管理', fixedLineType }: CircuitListProps) => {
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const isPrivateLine = fixedLineType === 'private_line'
  const {
    token: { colorBgContainer, colorBgElevated, colorFillAlter, colorBorder, colorText },
  } = theme.useToken()
  const defaultVisibleColumns = isPrivateLine
    ? ['name', 'datacenter_name', 'vendor_name', 'customer_name', 'access_mode', 'termination', 'physical_port_rate_gbps', 'bandwidth_mbps', 'local_interconnect_address', 'remote_interconnect_address', 'routing_mode', 'bfd_enabled', 'local_routed_cidrs', 'remote_routed_cidrs', 'status', 'action']
    : DEFAULT_VISIBLE_COLUMNS
  const visibleColumnsStorageKey = `${CIRCUIT_VISIBLE_COLUMNS_KEY}-${fixedLineType || 'all'}`
  const persistedVisibleColumns = (() => {
    try {
      const raw = localStorage.getItem(visibleColumnsStorageKey)
      return raw ? (JSON.parse(raw) as string[]) : defaultVisibleColumns
    } catch {
      return defaultVisibleColumns
    }
  })()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [circuits, setCircuits] = useState<Circuit[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [deviceInterfacesMap, setDeviceInterfacesMap] = useState<Record<number, MonitorInterface[]>>({})
  const [editingCircuit, setEditingCircuit] = useState<Circuit | null>(null)
  const [auditMap, setAuditMap] = useState<Record<number, CircuitAudit[]>>({})
  const [auditModalCircuit, setAuditModalCircuit] = useState<Circuit | null>(null)
  const [expandedRowKeys, setExpandedRowKeys] = useState<number[]>([])
  const [visibleColumns, setVisibleColumns] = useState<string[]>(persistedVisibleColumns)
  const [datacenterFilter, setDatacenterFilter] = useState<string | undefined>()
  const [operatorFilter, setOperatorFilter] = useState<string | undefined>()
  const [customerFilter, setCustomerFilter] = useState<string | undefined>()
  const [accessModeFilter, setAccessModeFilter] = useState<string | undefined>()
  const [search, setSearch] = useState('')
  const accessMode = Form.useWatch('access_mode', form)
  const dualLinkMode = Form.useWatch('dual_link_mode', form)
  const primaryInterconnectType = Form.useWatch('primary_interconnect_type', form)
  const secondaryInterconnectType = Form.useWatch('secondary_interconnect_type', form)
  const primaryDeviceId = Form.useWatch('primary_device_id', form)
  const secondaryDeviceId = Form.useWatch('secondary_device_id', form)
  const isDualLacpMode = isPrivateLine && accessMode === 'dual' && dualLinkMode === 'lacp'
  const navigate = useNavigate()
  const location = useLocation()

  const fetchDeviceOptions = async () => {
    const allDevices: Device[] = []
    let skip = 0
    const limit = 100

    while (true) {
      const result = await getDevices({ skip, limit })
      allDevices.push(...result.items)
      if (result.items.length < limit) {
        break
      }
      skip += limit
      if (skip >= 1000) {
        break
      }
    }

    setDevices(allDevices)
  }

  const fetchOptions = async () => {
    const [datacenterResult, vendorResult, customerResult] = await Promise.all([getDatacenters(), getVendors(), getCustomers()])
    setDatacenters(datacenterResult)
    setVendors(vendorResult.items)
    setCustomers(customerResult.items)
    await fetchDeviceOptions()
  }

  const fetchCircuits = async (keyword = search) => {
    setLoading(true)
    try {
      const result = await getCircuits({
        limit: 100,
        ...(fixedLineType ? { line_type: fixedLineType } : {}),
        ...(keyword ? { search: keyword } : {}),
      })
      setCircuits(result.items)
      const focusCircuitId = (location.state as { focusCircuitId?: number } | null)?.focusCircuitId
      if (focusCircuitId && result.items.some((item) => item.id === focusCircuitId)) {
        setExpandedRowKeys([focusCircuitId])
      }
    } catch {
      message.error('获取线路列表失败')
    } finally {
      setLoading(false)
    }
  }

  const buildAggregationOptions = (): AggregationOption[] => {
    const primaryInterfaces = primaryDeviceId ? deviceInterfacesMap[primaryDeviceId] || [] : []
    const secondaryInterfaces = secondaryDeviceId ? deviceInterfacesMap[secondaryDeviceId] || [] : []
    const aggregationMap = new Map<string, AggregationOption>()

    const appendInterfaces = (
      interfaces: MonitorInterface[],
      deviceId: number | undefined,
      side: 'primary' | 'secondary'
    ) => {
      interfaces
        .filter((item) => /^(Bridge-Aggregation|Route-Aggregation)/i.test(item.name))
        .forEach((item) => {
          const existing = aggregationMap.get(item.name)
          if (existing) {
            if (side === 'primary') {
              existing.presentOnPrimary = true
              existing.monitorDeviceId = deviceId ?? existing.monitorDeviceId
            } else {
              existing.presentOnSecondary = true
              if (!existing.monitorDeviceId) {
                existing.monitorDeviceId = deviceId ?? null
              }
            }
            return
          }
          aggregationMap.set(item.name, {
            value: item.name,
            label: item.name,
            interfaceName: item.name,
            monitorDeviceId: deviceId ?? null,
            presentOnPrimary: side === 'primary',
            presentOnSecondary: side === 'secondary',
          })
        })
    }

    appendInterfaces(primaryInterfaces, primaryDeviceId, 'primary')
    appendInterfaces(secondaryInterfaces, secondaryDeviceId, 'secondary')

    return Array.from(aggregationMap.values())
      .map((item) => {
        let placementLabel = '单端识别'
        if (item.presentOnPrimary && item.presentOnSecondary) {
          placementLabel = '双端同步（M-LAG）'
        } else if (item.presentOnPrimary) {
          placementLabel = '仅主端识别'
        } else if (item.presentOnSecondary) {
          placementLabel = '仅备端识别'
        }
        return {
          ...item,
          label: `${item.interfaceName}（${placementLabel}）`,
        }
      })
      .sort((left, right) => left.interfaceName.localeCompare(right.interfaceName, 'zh-CN', { numeric: true }))
  }

  useEffect(() => {
    fetchOptions()
    fetchCircuits('')
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      fetchCircuits(search)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search, datacenterFilter, operatorFilter, accessModeFilter])

  useEffect(() => {
    localStorage.setItem(visibleColumnsStorageKey, JSON.stringify(visibleColumns))
  }, [visibleColumns, visibleColumnsStorageKey])

  useEffect(() => {
    if (primaryDeviceId) {
      void loadDeviceInterfaces(primaryDeviceId)
    }
  }, [primaryDeviceId])

  useEffect(() => {
    if (secondaryDeviceId) {
      void loadDeviceInterfaces(secondaryDeviceId)
    }
  }, [secondaryDeviceId])

  const handleCreate = () => {
    setEditingCircuit(null)
    form.resetFields()
    form.setFieldsValue({
      line_type: 'internet',
      ...(fixedLineType ? { line_type: fixedLineType } : {}),
      access_mode: 'single',
      status: 'active',
      is_redundant: false,
      bandwidth_mbps: 100,
      primary_port_rate: '10G',
      secondary_port_rate: undefined,
      dual_link_mode: undefined,
      aggregation_selection: undefined,
      bfd_mode: 'none',
      bfd_enabled: false,
      routing_mode: undefined,
      customer_id: undefined,
      interconnect_address: undefined,
      primary_local_interconnect_ip: undefined,
      primary_remote_interconnect_ip: undefined,
      primary_interconnect_type: 'l3',
      primary_routing_mode: undefined,
      primary_bfd_mode: 'none',
      primary_interconnect_ip: undefined,
      secondary_local_interconnect_ip: undefined,
      secondary_remote_interconnect_ip: undefined,
      secondary_interconnect_type: 'l3',
      secondary_routing_mode: undefined,
      secondary_bfd_mode: 'none',
      secondary_interconnect_ip: undefined,
      primary_vlan_id: undefined,
      secondary_vlan_id: undefined,
      local_interconnect_address: undefined,
      remote_interconnect_address: undefined,
      interconnect_type: 'l3',
      routed_cidrs: undefined,
      routed_networks: [{ prefix: '', mask: '' }],
      local_routed_cidrs: undefined,
      local_routed_networks: [{ prefix: '', mask: '' }],
      remote_routed_cidrs: undefined,
      remote_routed_networks: [{ prefix: '', mask: '' }],
      address_segments: [{ segment_type: 'interconnect', is_public: false }],
    })
    setOpen(true)
  }

  const handleEdit = (record: Circuit) => {
    setEditingCircuit(record)
    form.setFieldsValue({
      ...record,
      aggregation_selection: record.aggregation_interface_name || undefined,
      bfd_mode: record.bfd_mode || (record.bfd_enabled ? 'bfd' : 'none'),
      primary_local_interconnect_ip: record.primary_local_interconnect_ip || record.primary_interconnect_ip || record.local_interconnect_address,
      primary_remote_interconnect_ip: record.primary_remote_interconnect_ip || record.remote_interconnect_address,
      primary_interconnect_type: record.primary_interconnect_type || record.interconnect_type || 'l3',
      primary_routing_mode: record.primary_routing_mode || record.routing_mode,
      primary_bfd_mode: record.primary_bfd_mode || record.bfd_mode || (record.bfd_enabled ? 'bfd' : 'none'),
      secondary_local_interconnect_ip: record.secondary_local_interconnect_ip || record.secondary_interconnect_ip,
      secondary_remote_interconnect_ip: record.secondary_remote_interconnect_ip,
      secondary_interconnect_type: record.secondary_interconnect_type || record.interconnect_type || 'l3',
      secondary_routing_mode: record.secondary_routing_mode || record.routing_mode,
      secondary_bfd_mode: record.secondary_bfd_mode || record.bfd_mode || (record.bfd_enabled ? 'bfd' : 'none'),
      routed_networks: record.routed_networks?.length ? record.routed_networks : [{ prefix: '', mask: '' }],
      local_routed_networks: record.local_routed_networks?.length
        ? record.local_routed_networks
        : (record.routed_networks?.length ? record.routed_networks : [{ prefix: '', mask: '' }]),
      remote_routed_networks: record.remote_routed_networks?.length ? record.remote_routed_networks : [{ prefix: '', mask: '' }],
      address_segments: record.address_segments?.length
        ? record.address_segments
        : [{ segment_type: 'interconnect', is_public: false }],
    })
    setOpen(true)
  }

  const loadDeviceInterfaces = async (deviceId?: number) => {
    if (!deviceId || deviceInterfacesMap[deviceId]) {
      return
    }
    try {
      const response = await getMonitorDeviceInterfaces(deviceId)
      setDeviceInterfacesMap((prev) => ({ ...prev, [deviceId]: response.interfaces }))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取交换机端口失败')
    }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const aggregationSelection = decodeAggregationSelection(values.aggregation_selection)
      const normalizedValues = {
        ...values,
        line_type: fixedLineType || values.line_type,
        operator_name: isPrivateLine ? '专线' : values.operator_name,
        secondary_device_id: values.access_mode === 'dual' ? values.secondary_device_id : null,
        secondary_port_name: values.access_mode === 'dual' ? values.secondary_port_name : null,
        secondary_port_rate: values.access_mode === 'dual' ? values.secondary_port_rate : null,
        dual_link_mode: values.access_mode === 'dual' ? values.dual_link_mode : null,
        aggregation_monitor_device_id: isDualLacpMode ? aggregationSelection.deviceId : null,
        aggregation_interface_name: isDualLacpMode ? aggregationSelection.interfaceName : null,
        physical_port_rate_gbps: 0,
        interconnect_type: isPrivateLine ? values.primary_interconnect_type : values.interconnect_type,
        routing_mode: isPrivateLine ? values.primary_routing_mode : values.routing_mode,
        bfd_mode: isPrivateLine ? (values.primary_bfd_mode || 'none') : 'none',
        bfd_enabled: isPrivateLine && values.primary_bfd_mode === 'bfd',
        primary_interconnect_ip: isPrivateLine ? values.primary_local_interconnect_ip : values.primary_interconnect_ip,
        primary_interconnect_type: isPrivateLine ? values.primary_interconnect_type : null,
        secondary_interconnect_ip: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_local_interconnect_ip : null,
        secondary_local_interconnect_ip: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_local_interconnect_ip : null,
        secondary_remote_interconnect_ip: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_remote_interconnect_ip : null,
        secondary_interconnect_type: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_interconnect_type : null,
        secondary_routing_mode: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_routing_mode : null,
        secondary_bfd_mode: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode ? values.secondary_bfd_mode : 'none',
        primary_vlan_id: isPrivateLine && values.primary_interconnect_type === 'l2' ? values.primary_vlan_id : null,
        secondary_vlan_id: isPrivateLine && values.access_mode === 'dual' && !isDualLacpMode && values.secondary_interconnect_type === 'l2' ? values.secondary_vlan_id : null,
        local_interconnect_address: isPrivateLine ? values.primary_local_interconnect_ip : values.local_interconnect_address,
        remote_interconnect_address: isPrivateLine ? values.primary_remote_interconnect_ip : values.remote_interconnect_address,
        interconnect_address: isPrivateLine
          ? [
              [values.primary_local_interconnect_ip, values.primary_remote_interconnect_ip].filter(Boolean).join(' - '),
              values.access_mode === 'dual' && !isDualLacpMode
                ? [values.secondary_local_interconnect_ip, values.secondary_remote_interconnect_ip].filter(Boolean).join(' - ')
                : undefined,
            ].filter(Boolean).join(' / ')
          : values.interconnect_address,
        routed_cidrs: isPrivateLine
          ? [
              ...((values.local_routed_networks || []).filter((item: any) => item?.prefix && item?.mask).map((item: any) => `${item.prefix}/${item.mask}`)),
              ...((values.remote_routed_networks || []).filter((item: any) => item?.prefix && item?.mask).map((item: any) => `${item.prefix}/${item.mask}`)),
            ].join(', ')
          : values.routed_cidrs,
        routed_networks: isPrivateLine ? [] : [],
        local_routed_cidrs: isPrivateLine
          ? (values.local_routed_networks || [])
              .filter((item: any) => item?.prefix && item?.mask)
              .map((item: any) => `${item.prefix}/${item.mask}`)
              .join(', ')
          : undefined,
        local_routed_networks: isPrivateLine
          ? (values.local_routed_networks || []).filter((item: any) => item?.prefix && item?.mask)
          : [],
        remote_routed_cidrs: isPrivateLine
          ? (values.remote_routed_networks || [])
              .filter((item: any) => item?.prefix && item?.mask)
              .map((item: any) => `${item.prefix}/${item.mask}`)
              .join(', ')
          : undefined,
        remote_routed_networks: isPrivateLine
          ? (values.remote_routed_networks || []).filter((item: any) => item?.prefix && item?.mask)
          : [],
        address_segments: isPrivateLine ? [] : (values.address_segments || []).filter((item: any) => item?.cidr),
      }
      setSaving(true)
      if (editingCircuit) {
        await updateCircuit(editingCircuit.id, normalizedValues)
        message.success('更新成功')
      } else {
        await createCircuit(normalizedValues)
        message.success('创建成功')
      }
      setDatacenterFilter(undefined)
      setCustomerFilter(undefined)
      setAccessModeFilter(undefined)
      setOpen(false)
      await fetchCircuits()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteCircuit(id)
      message.success('删除成功')
      fetchCircuits()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleViewTraffic = (record: Circuit) => {
    const targets = [
      record.primary_device_id && record.primary_device_ip && record.primary_port_name
        ? {
            deviceId: record.primary_device_id,
            deviceIp: record.primary_device_ip,
            deviceName: record.primary_device_name,
            portName: record.primary_port_name,
            side: 'primary',
          }
        : null,
      record.access_mode === 'dual' && record.secondary_device_id && record.secondary_device_ip && record.secondary_port_name
        ? {
            deviceId: record.secondary_device_id,
            deviceIp: record.secondary_device_ip,
            deviceName: record.secondary_device_name,
            portName: record.secondary_port_name,
            side: 'secondary',
          }
        : null,
    ].filter(Boolean)

    if (!targets.length) {
      message.warning('该线路未绑定可监控的交换机端口')
      return
    }

    navigate('/grafana', {
      state: {
        circuitMonitorTargets: targets,
        sourceCircuitName: record.name,
        sourceCircuitType: record.line_type === 'private_line' ? '专线' : '公网',
      },
    })
  }

  const loadCircuitAudits = async (circuitId: number) => {
    try {
      const result = await getCircuitAudits(circuitId)
      setAuditMap((prev) => ({ ...prev, [circuitId]: result.items }))
    } catch {
      message.error('获取线路审计失败')
    }
  }

  const openAuditModal = async (record: Circuit) => {
    if (!auditMap[record.id]) {
      await loadCircuitAudits(record.id)
    }
    setAuditModalCircuit(record)
  }

  const formatDateTime = (value?: string) => {
    if (!value) {
      return '-'
    }
    const date = new Date(value)
    const yyyy = date.getFullYear()
    const mm = String(date.getMonth() + 1).padStart(2, '0')
    const dd = String(date.getDate()).padStart(2, '0')
    const hh = String(date.getHours()).padStart(2, '0')
    const mi = String(date.getMinutes()).padStart(2, '0')
    const ss = String(date.getSeconds()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`
  }

  const formatAuditValue = (field: string, value: unknown) => {
    if (value === null || value === undefined || value === '') {
      return '空'
    }
    if (field === 'bandwidth_mbps' && typeof value === 'number') {
      return `${value}M`
    }
    if ((field === 'physical_port_rate_gbps' && typeof value === 'number') || field === 'primary_port_rate' || field === 'secondary_port_rate') {
      return String(value)
    }
    if (field === 'dual_link_mode' && typeof value === 'string') {
      return dualLinkModeLabelMap[value] || value
    }
    if (field === 'line_type' && typeof value === 'string') {
      return lineTypeLabelMap[value] || value
    }
    if (field === 'routing_mode' && typeof value === 'string') {
      return getRoutingModeLabel(value)
    }
    if (['interconnect_type', 'primary_interconnect_type', 'secondary_interconnect_type'].includes(field) && typeof value === 'string') {
      return getInterconnectTypeLabel(value)
    }
    if (field === 'access_mode' && typeof value === 'string') {
      return accessModeLabelMap[value] || value
    }
    if (field === 'status' && typeof value === 'string') {
      return statusLabelMap[value] || value
    }
    if (field === 'is_redundant' && typeof value === 'boolean') {
      return value ? '是' : '否'
    }
    if (field === 'bfd_enabled' && typeof value === 'boolean') {
      return value ? '已开启' : '未开启'
    }
    if (field === 'address_segments' && Array.isArray(value)) {
      return `${value.length}段地址`
    }
    if ((field === 'routed_networks' || field === 'local_routed_networks' || field === 'remote_routed_networks') && Array.isArray(value)) {
      return value
        .map((item: any) => `${item?.prefix || ''}/${item?.mask || ''}`)
        .filter((item: string) => item !== '/')
        .join(', ') || '空'
    }
    if (typeof value === 'object') {
      return JSON.stringify(value)
    }
    return String(value)
  }

  const buildAddressSegmentAuditLines = (audit: CircuitAudit, when: string, beforeValue: unknown, afterValue: unknown) => {
    const beforeSegments = Array.isArray(beforeValue) ? beforeValue : []
    const afterSegments = Array.isArray(afterValue) ? afterValue : []
    const beforeMap = new Map(beforeSegments.map((item: any) => [String(item?.cidr || ''), item]))
    const afterMap = new Map(afterSegments.map((item: any) => [String(item?.cidr || ''), item]))

    const removed = beforeSegments.filter((item: any) => item?.cidr && !afterMap.has(String(item.cidr)))
    const added = afterSegments.filter((item: any) => item?.cidr && !beforeMap.has(String(item.cidr)))

    if (!removed.length && !added.length) {
      return null
    }

    const lines: React.ReactNode[] = []

    removed.forEach((item: any, index: number) => {
      lines.push(
        <React.Fragment key={`${audit.id}-addr-del-${item.cidr}-${index}`}>
          <span>
            账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
            {' '}在 {when} 将【地址段】{' '}
            <span style={{ color: '#cf1322', fontWeight: 600 }}>{item.cidr}</span>
            {' '}删除。
          </span>
        </React.Fragment>,
      )
    })

    added.forEach((item: any, index: number) => {
      lines.push(
        <React.Fragment key={`${audit.id}-addr-add-${item.cidr}-${index}`}>
          <span>
            账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
            {' '}在 {when} 将【地址段】{' '}
            <span style={{ color: '#389e0d', fontWeight: 600 }}>{item.cidr}</span>
            {' '}添加。
          </span>
        </React.Fragment>,
      )
    })

    return lines.flatMap((line, index) => (
      index < lines.length - 1 ? [line, <br key={`${audit.id}-addr-br-${index}`} />] : [line]
    ))
  }

  const renderAuditDescription = (audit: CircuitAudit) => {
    const when = formatDateTime(audit.created_at)
    if (!audit.change_summary?.length) {
      return (
        <span>
          账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
          {' '}在 {when} 执行了{audit.action === 'create' ? '新增' : audit.action === 'delete' ? '删除' : '修改'}操作。
        </span>
      )
    }
    return audit.change_summary.map((item, index) => {
      const label = auditFieldLabelMap[item.field] || item.field
      if (item.field === 'address_segments') {
        const addressLines = buildAddressSegmentAuditLines(audit, when, item.before, item.after)
        if (addressLines) {
          return (
            <React.Fragment key={`${audit.id}-${item.field}-${index}`}>
              {addressLines}
              {index < audit.change_summary.length - 1 ? <br /> : null}
            </React.Fragment>
          )
        }
      }
      const beforeValue = formatAuditValue(item.field, item.before)
      const afterValue = formatAuditValue(item.field, item.after)
      if (audit.action === 'create') {
        return (
          <React.Fragment key={`${audit.id}-${item.field}-${index}`}>
            <span>
              账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
              {' '}在 {when} 新增了【{label}】，内容为{' '}
              <span style={{ color: '#389e0d', fontWeight: 600 }}>{afterValue}</span>。
            </span>
            {index < audit.change_summary.length - 1 ? <br /> : null}
          </React.Fragment>
        )
      }
      if (audit.action === 'delete') {
        return (
          <React.Fragment key={`${audit.id}-${item.field}-${index}`}>
            <span>
              账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
              {' '}在 {when} 删除了【{label}】，删除前内容为{' '}
              <span style={{ color: '#cf1322', fontWeight: 600 }}>{beforeValue}</span>。
            </span>
            {index < audit.change_summary.length - 1 ? <br /> : null}
          </React.Fragment>
        )
      }
      return (
        <React.Fragment key={`${audit.id}-${item.field}-${index}`}>
          <span>
            账号 <span style={{ color: '#1677ff', fontWeight: 600 }}>【{audit.actor_username}】</span>
            {' '}在 {when} 将【{label}】{' '}
            <span style={{ color: '#cf1322', fontWeight: 600 }}>{beforeValue}</span>
            {' '}修改为{' '}
            <span style={{ color: '#389e0d', fontWeight: 600 }}>{afterValue}</span>。
          </span>
          {index < audit.change_summary.length - 1 ? <br /> : null}
        </React.Fragment>
      )
    })
  }

  const renderTerminationLine = (
    label: string,
    deviceLabel?: string,
    portName?: string,
    portRate?: string,
    singleLine = false
  ) => (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', lineHeight: 1.4, minWidth: 0 }}>
      <Tag style={{ marginInlineEnd: 0, fontSize: singleLine ? 12 : 13 }}>{label}</Tag>
      <span
        title={deviceLabel ? `${deviceLabel} / ${portName || '-'} / ${portRate || '-'}` : '未绑定'}
        style={{
          minWidth: 0,
          flex: 1,
          fontSize: singleLine ? 12 : 14,
          whiteSpace: singleLine ? 'nowrap' : 'normal',
          overflow: singleLine ? 'hidden' : 'visible',
          textOverflow: singleLine ? 'ellipsis' : 'clip',
          wordBreak: singleLine ? 'normal' : 'break-all',
        }}
      >
        {deviceLabel ? `${deviceLabel} / ${portName || '-'} / ${portRate || '-'}` : '未绑定'}
      </span>
    </div>
  )

  const renderTerminationDetailLine = (
    label: string,
    deviceName?: string,
    deviceIp?: string,
    portName?: string,
    portRate?: string
  ) => (
    <div style={{ color: '#666', fontWeight: 600, lineHeight: 1.7 }}>
      <span style={{ color: '#333', fontWeight: 700 }}>{label}</span>
      设备名称：
      <span style={{ color: '#333', fontWeight: 700 }}>{deviceName || '未绑定'}</span>
      <span style={{ color: '#1677ff', fontWeight: 800 }}>【{deviceIp || '未填写'}】</span>
      {' '} / 端口：
      <span style={{ color: '#262626', fontWeight: 800 }}>【{portName || '未填写'}】</span>
      {' '} / 物理速率
      <span style={{ color: '#262626', fontWeight: 800 }}>【{portRate || '未填写'}】</span>
    </div>
  )

  const renderAggregationDetailLine = (
    interfaceName?: string,
    placementLabel?: string,
    aggregatedRate?: string
  ) => (
    <div style={{ color: '#666', fontWeight: 600, lineHeight: 1.7 }}>
      <span style={{ color: '#333', fontWeight: 700 }}>逻辑聚合接口：</span>
      <span style={{ color: '#262626', fontWeight: 800 }}>【{interfaceName || '未填写'}】</span>
      {placementLabel ? (
        <>
          {' '} / 部署方式：
          <span style={{ color: '#1677ff', fontWeight: 800 }}>【{placementLabel}】</span>
        </>
      ) : null}
      {aggregatedRate ? (
        <>
          {' '} / 聚合速率：
          <span style={{ color: '#262626', fontWeight: 800 }}>【{aggregatedRate}】</span>
        </>
      ) : null}
    </div>
  )

  const renderPrivateLineInterconnectLine = (
    label: string,
    localAddress?: string,
    remoteAddress?: string,
    vlanId?: number,
    currentInterconnectType?: string,
  ) => (
    <div style={{ color: '#666', fontWeight: 600, lineHeight: 1.7 }}>
      <span style={{ color: '#333', fontWeight: 700 }}>{label ? `${label}互联地址：` : '互联地址：'}</span>
      本端地址
      <span style={{ color: '#262626', fontWeight: 800 }}>【{localAddress || '未填写'}】</span>
      {' '} / 对端地址
      <span style={{ color: '#262626', fontWeight: 800 }}>【{remoteAddress || '未填写'}】</span>
      {currentInterconnectType === 'l2' ? (
        <>
          {' '} / VLAN ID
          <span style={{ color: '#262626', fontWeight: 800 }}>【{vlanId || '未填写'}】</span>
        </>
      ) : null}
    </div>
  )

  const getPrimaryLocalAddress = (record: Circuit) =>
    record.primary_local_interconnect_ip || record.primary_interconnect_ip || record.local_interconnect_address

  const getPrimaryRemoteAddress = (record: Circuit) =>
    record.primary_remote_interconnect_ip || record.remote_interconnect_address

  const getSecondaryLocalAddress = (record: Circuit) =>
    record.secondary_local_interconnect_ip || record.secondary_interconnect_ip

  const getSecondaryRemoteAddress = (record: Circuit) =>
    record.secondary_remote_interconnect_ip

  const getBfdMode = (record: Circuit) => record.bfd_mode || (record.bfd_enabled ? 'bfd' : 'none')

  const getPrimaryRoutingMode = (record: Circuit) => record.primary_routing_mode || record.routing_mode
  const getSecondaryRoutingMode = (record: Circuit) => record.secondary_routing_mode || record.routing_mode
  const getPrimaryBfdMode = (record: Circuit) => record.primary_bfd_mode || getBfdMode(record)
  const getSecondaryBfdMode = (record: Circuit) => record.secondary_bfd_mode || getBfdMode(record)
  const getPrimaryInterconnectType = (record: Circuit) => record.primary_interconnect_type || record.interconnect_type
  const getSecondaryInterconnectType = (record: Circuit) => record.secondary_interconnect_type || record.interconnect_type
  const getInterconnectTypeLabel = (value?: string) => interconnectTypeOptions.find((item) => item.value === value)?.label || value || '-'
  const getRoutingModeLabel = (value?: string) => routingModeOptions.find((item) => item.value === value)?.label || value || '-'

  const renderStackedText = (lines: Array<[string, string | undefined]>) => {
    const visibleLines = lines.map(([label, value]) => [label, value || '-'] as [string, string])
    const title = visibleLines.map(([label, value]) => (label ? `${label} ${value}` : value)).join('\n')
    return (
      <div title={title} style={{ ...TABLE_TEXT_STYLE, display: 'grid', gap: 2, whiteSpace: 'normal', lineHeight: 1.35 }}>
        {visibleLines.map(([label, value]) => (
          <span key={`${label}-${value}`} style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {label ? `${label} ${value}` : value}
          </span>
        ))}
      </div>
    )
  }

  const renderCidrsHighlight = (text?: string) => {
    const items = (text || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    if (!items.length) {
      items.push('未填写')
    }

    return (
      <Space size={[6, 6]} wrap>
        {items.map((item, index) => (
          <span
            key={`${item}-${index}`}
            style={{
              display: 'inline-block',
              padding: '1px 8px',
              borderRadius: 999,
              background: '#e6f4ff',
              border: '1px solid #91caff',
              color: '#0958d9',
              fontWeight: 800,
            }}
          >
            {item}
          </span>
        ))}
      </Space>
    )
  }

  const renderSegmentCard = (segment: NonNullable<Circuit['address_segments']>[number], index: number, recordId: number) => (
    <div
      key={`${recordId}-${index}`}
      style={{
        border: `1px solid ${colorBorder}`,
        borderRadius: 8,
        padding: '8px 10px',
        minWidth: 240,
        background: colorFillAlter,
      }}
    >
      <Space size={[6, 6]} wrap>
        <Typography.Text strong>{segment.cidr}</Typography.Text>
        <Tag color={segmentTypeColorMap[segment.segment_type] || 'default'}>
          {segmentTypeLabelMap[segment.segment_type] || segment.segment_type}
        </Tag>
        {segment.is_public ? <Tag color="volcano">公网</Tag> : <Tag color="green">业务/内网</Tag>}
      </Space>
      <div style={{ marginTop: 6, color: '#666' }}>
        {segment.usage || '未填写用途'}
        {segment.gateway_ip ? ` · 网关 ${segment.gateway_ip}` : ''}
      </div>
      {segment.description ? (
        <div style={{ marginTop: 4, color: '#999' }}>备注：{segment.description}</div>
      ) : null}
    </div>
  )

  const deviceOptions = devices.map((item) => ({
    value: item.id,
    label: `${item.name} (${item.ip_address})`,
  }))
  const getPortOptions = (deviceId?: number) =>
    (deviceId ? deviceInterfacesMap[deviceId] || [] : []).map((item) => ({
      value: item.name,
      label: item.name,
    }))
  const aggregationOptions = useMemo<AggregationOption[]>(() => buildAggregationOptions(), [deviceInterfacesMap, primaryDeviceId, secondaryDeviceId])

  const getAggregationOptionByInterfaceName = (interfaceName?: string) =>
    aggregationOptions.find((item) => item.interfaceName === interfaceName)

  const getAggregationPlacementLabel = (interfaceName?: string) => {
    const matched = getAggregationOptionByInterfaceName(interfaceName)
    if (matched?.presentOnPrimary && matched?.presentOnSecondary) {
      return '双端同步（M-LAG）'
    }
    if (matched?.presentOnPrimary) {
      return '仅主端识别'
    }
    if (matched?.presentOnSecondary) {
      return '仅备端识别'
    }
    return undefined
  }

  const decodeAggregationSelection = (value?: string) => {
    const matched = getAggregationOptionByInterfaceName(value)
    if (!matched) {
      return { deviceId: null, interfaceName: null }
    }
    return {
      deviceId: matched.monitorDeviceId,
      interfaceName: matched.interfaceName,
    }
  }

  useEffect(() => {
    if (!isDualLacpMode) {
      form.setFieldValue('aggregation_selection', undefined)
      return
    }
    const currentValue = form.getFieldValue('aggregation_selection') as string | undefined
    if (!currentValue) {
      return
    }
    const validOptions = new Set(aggregationOptions.map((item) => item.value))
    if (!validOptions.has(currentValue)) {
      form.setFieldValue('aggregation_selection', undefined)
    }
  }, [aggregationOptions, form, isDualLacpMode])

  const isDualLacpCircuit = (record: Circuit) =>
    isPrivateLine && record.access_mode === 'dual' && record.dual_link_mode === 'lacp'

  const renderFilterChips = <T extends string>(params: {
    label: string
    value: T | undefined
    onChange: (value: T | undefined) => void
    options: Array<{ value: T; label: string }>
  }) => {
    const { label, value, onChange, options } = params

    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ minWidth: 84, fontWeight: 700, color: '#333', lineHeight: '30px', fontSize: 12 }}>
          {label}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0, flex: 1 }}>
          <Button
            size="small"
            type={value === undefined ? 'primary' : 'default'}
            onClick={() => onChange(undefined)}
            style={{ borderRadius: 0, minWidth: 40, marginRight: -1, marginBottom: -1, fontSize: 12 }}
          >
            全部
          </Button>
          {options.map((option) => (
            <Button
              key={String(option.value)}
              size="small"
              type={value === option.value ? 'primary' : 'default'}
              onClick={() => onChange(value === option.value ? undefined : option.value)}
              style={{ borderRadius: 0, marginRight: -1, marginBottom: -1, fontSize: 12 }}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>
    )
  }

  const datacenterFilterOptions = useMemo(
    () =>
      Array.from(
        new Map(
          circuits
            .filter((item) => item.datacenter_name)
            .map((item) => [item.datacenter_name as string, { value: item.datacenter_name as string, label: item.datacenter_name as string }])
        ).values()
      ),
    [circuits]
  )

  const operatorFilterOptions = useMemo(
    () =>
      Array.from(
        new Map(
          circuits
            .filter((item) => item.operator_name)
            .map((item) => [item.operator_name as string, { value: item.operator_name as string, label: item.operator_name as string }])
        ).values()
      ),
    [circuits]
  )
  const customerFilterOptions = useMemo(
    () =>
      Array.from(
        new Map(
          circuits
            .filter((item) => item.customer_name)
            .map((item) => [item.customer_name as string, { value: item.customer_name as string, label: item.customer_name as string }])
        ).values()
      ),
    [circuits]
  )

  const filteredCircuits = useMemo(() => {
    return circuits.filter((item) => {
      if (fixedLineType && item.line_type !== fixedLineType) {
        return false
      }
      if (datacenterFilter && item.datacenter_name !== datacenterFilter) {
        return false
      }
      if (operatorFilter && item.operator_name !== operatorFilter) {
        return false
      }
      if (customerFilter && item.customer_name !== customerFilter) {
        return false
      }
      if (accessModeFilter && item.access_mode !== accessModeFilter) {
        return false
      }
      return true
    })
  }, [circuits, datacenterFilter, operatorFilter, customerFilter, accessModeFilter, fixedLineType])

  const columnToggleOptions = [
    { key: 'name', label: '线路名称' },
    { key: 'datacenter_name', label: 'IDC机房' },
    { key: 'operator_name', label: '运营商' },
    { key: 'line_type', label: '类型' },
    { key: 'vendor_name', label: '供应商' },
    { key: 'customer_name', label: '客户' },
    { key: 'access_mode', label: '接入方式' },
    { key: 'termination', label: '落交换机端口' },
    { key: 'physical_port_rate_gbps', label: '物理端口速率' },
    { key: 'bandwidth_mbps', label: '带宽' },
    { key: 'local_interconnect_address', label: '本端地址' },
    { key: 'remote_interconnect_address', label: '对端地址' },
    { key: 'routing_mode', label: '路由对接方式' },
    { key: 'bfd_enabled', label: 'BFD' },
    { key: 'routed_cidrs', label: '路由CIDR' },
    { key: 'segments', label: '地址段' },
    { key: 'status', label: '状态' },
    { key: 'action', label: '操作' },
  ]
  const filteredColumnToggleOptions = columnToggleOptions.filter((option) => {
    if (isPrivateLine) {
      return !['operator_name', 'line_type', 'interconnect_type', 'interconnect_address', 'routed_cidrs', 'segments'].includes(option.key)
    }
    return !['customer_name', 'local_interconnect_address', 'remote_interconnect_address', 'routing_mode', 'bfd_enabled', 'routed_cidrs'].includes(option.key)
  })

  const columns = [
    {
      title: '线路名称',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: 'IDC机房',
      dataIndex: 'datacenter_name',
      key: 'datacenter_name',
      width: 92,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '运营商',
      dataIndex: 'operator_name',
      key: 'operator_name',
      width: 78,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '类型',
      dataIndex: 'line_type',
      key: 'line_type',
      width: 100,
      ellipsis: true,
      render: (v: string) => {
        const text = lineTypeOptions.find((item) => item.value === v)?.label || v
        return <span title={text || '-'} style={TABLE_TEXT_STYLE}>{text || '-'}</span>
      },
    },
    {
      title: '供应商',
      dataIndex: 'vendor_name',
      key: 'vendor_name',
      width: 100,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      key: 'customer_name',
      width: 120,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '接入方式',
      dataIndex: 'access_mode',
      key: 'access_mode',
      width: 86,
      render: (v: string) => <Tag style={{ fontWeight: 700 }} color={v === 'dual' ? 'processing' : 'default'}>{v === 'dual' ? '双线' : '单线'}</Tag>,
    },
    {
      title: '落交换机端口',
      key: 'termination',
      width: 360,
      render: (_: unknown, record: Circuit) => {
        const dualLacpCircuit = isDualLacpCircuit(record)
        return (
          <div style={{ display: 'grid', gap: 4 }}>
            {renderTerminationLine('主接入', record.primary_device_ip || record.primary_device_name, record.primary_port_name, record.primary_port_rate, true)}
            {record.access_mode === 'dual'
              ? renderTerminationLine('备接入', record.secondary_device_ip || record.secondary_device_name, record.secondary_port_name, record.secondary_port_rate, true)
              : null}
            {dualLacpCircuit ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', lineHeight: 1.4, minWidth: 0 }}>
                <Tag style={{ marginInlineEnd: 0, fontSize: 12 }}>逻辑聚合</Tag>
                <span
                  title={`${record.aggregation_interface_name || '-'} / ${getAggregationPlacementLabel(record.aggregation_interface_name) || '逻辑单线'} / ${getAggregatedPortRate(record.primary_port_rate, record.secondary_port_rate)}`}
                  style={{
                    minWidth: 0,
                    flex: 1,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {`${record.aggregation_interface_name || '-'} / ${getAggregationPlacementLabel(record.aggregation_interface_name) || '逻辑单线'} / ${getAggregatedPortRate(record.primary_port_rate, record.secondary_port_rate)}`}
                </span>
              </div>
            ) : null}
          </div>
        )
      },
    },
    {
      title: '端口速率',
      dataIndex: 'physical_port_rate_gbps',
      key: 'physical_port_rate_gbps',
      width: 132,
      render: (_: unknown, record: Circuit) => (
        <span
          title={
            record.access_mode === 'dual' && !isDualLacpCircuit(record)
              ? `${record.primary_port_rate || '-'} / ${record.secondary_port_rate || '-'}`
              : (record.primary_port_rate || record.secondary_port_rate || '-')
          }
          style={TABLE_TEXT_STYLE}
        >
          {record.access_mode === 'dual' && !isDualLacpCircuit(record)
            ? `${record.primary_port_rate || '-'} / ${record.secondary_port_rate || '-'}`
            : (record.primary_port_rate || record.secondary_port_rate || '-')}
        </span>
      ),
    },
    {
      title: '带宽(Mbps)',
      dataIndex: 'bandwidth_mbps',
      key: 'bandwidth_mbps',
      width: 74,
      ellipsis: true,
      render: (v: number) => <span title={String(v ?? '-')} style={TABLE_TEXT_STYLE}>{v ?? '-'}</span>,
    },
    {
      title: '互联地址',
      dataIndex: 'interconnect_address',
      key: 'interconnect_address',
      width: 160,
      ellipsis: true,
      render: (_: string, record: Circuit) => {
        const text = record.access_mode === 'dual' && !isDualLacpCircuit(record)
          ? `主 ${getPrimaryLocalAddress(record) || '-'} -> ${getPrimaryRemoteAddress(record) || '-'} / 备 ${getSecondaryLocalAddress(record) || '-'} -> ${getSecondaryRemoteAddress(record) || '-'}`
          : `${getPrimaryLocalAddress(record) || '-'} -> ${getPrimaryRemoteAddress(record) || '-'}`
        return <span title={text} style={TABLE_TEXT_STYLE}>{text}</span>
      },
    },
    {
      title: '本端地址',
      dataIndex: 'local_interconnect_address',
      key: 'local_interconnect_address',
      width: 160,
      render: (_: string, record: Circuit) => {
        if (record.access_mode === 'dual' && !isDualLacpCircuit(record)) {
          return renderStackedText([
            ['主', getPrimaryLocalAddress(record)],
            ['备', getSecondaryLocalAddress(record)],
          ])
        }
        const text = getPrimaryLocalAddress(record) || record.interconnect_address || '-'
        return <span title={text} style={TABLE_TEXT_STYLE}>{text}</span>
      },
    },
    {
      title: '对端地址',
      dataIndex: 'remote_interconnect_address',
      key: 'remote_interconnect_address',
      width: 160,
      render: (_: string, record: Circuit) => {
        if (record.access_mode === 'dual' && !isDualLacpCircuit(record)) {
          return renderStackedText([
            ['主', getPrimaryRemoteAddress(record)],
            ['备', getSecondaryRemoteAddress(record)],
          ])
        }
        const text = getPrimaryRemoteAddress(record) || '-'
        return <span title={text} style={TABLE_TEXT_STYLE}>{text}</span>
      },
    },
    {
      title: '本地IDC内网段',
      dataIndex: 'local_routed_cidrs',
      key: 'local_routed_cidrs',
      width: 150,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '对端IDC内网段',
      dataIndex: 'remote_routed_cidrs',
      key: 'remote_routed_cidrs',
      width: 150,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '互联方式',
      dataIndex: 'interconnect_type',
      key: 'interconnect_type',
      width: 130,
      render: (v: string, record: Circuit) => {
        if (record.access_mode === 'dual' && !isDualLacpCircuit(record)) {
          return renderStackedText([
            ['主', getInterconnectTypeLabel(getPrimaryInterconnectType(record))],
            ['备', getInterconnectTypeLabel(getSecondaryInterconnectType(record))],
          ])
        }
        const text = getInterconnectTypeLabel(getPrimaryInterconnectType(record) || v)
        return <span title={text} style={TABLE_TEXT_STYLE}>{text}</span>
      },
    },
    {
      title: '路由协议',
      dataIndex: 'routing_mode',
      key: 'routing_mode',
      width: 150,
      render: (_: string, record: Circuit) => {
        const primary = getPrimaryRoutingMode(record)
        const secondary = record.access_mode === 'dual' && !isDualLacpCircuit(record) ? getSecondaryRoutingMode(record) : undefined
        if (record.access_mode === 'dual' && !isDualLacpCircuit(record)) {
          return renderStackedText([
            ['主', getRoutingModeLabel(primary)],
            ['备', getRoutingModeLabel(secondary)],
          ])
        }
        const text = getRoutingModeLabel(primary)
        return <span title={text} style={TABLE_TEXT_STYLE}>{text}</span>
      },
    },
    {
      title: '探测方式',
      dataIndex: 'bfd_mode',
      key: 'bfd_enabled',
      width: 132,
      render: (_: string, record: Circuit) => {
        const modes = record.access_mode === 'dual' && !isDualLacpCircuit(record)
          ? [
              ['主', getPrimaryBfdMode(record)],
              ['备', getSecondaryBfdMode(record)],
            ]
          : [['', getPrimaryBfdMode(record)]]
        return (
          <Space size={4} direction={record.access_mode === 'dual' && !isDualLacpCircuit(record) ? 'vertical' : 'horizontal'}>
            {modes.map(([label, mode]) => (
              <Tag key={`${label}-${mode}`} style={{ fontWeight: 700, marginInlineEnd: 0 }} color={mode === 'bfd' ? 'success' : mode === 'track' ? 'processing' : 'default'}>
                {label ? `${label}${bfdModeLabelMap[mode] || mode}` : bfdModeLabelMap[mode] || mode}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '路由CIDR',
      dataIndex: 'routed_cidrs',
      key: 'routed_cidrs',
      width: 180,
      ellipsis: true,
      render: (v: string) => <span title={v || '-'} style={TABLE_TEXT_STYLE}>{v || '-'}</span>,
    },
    {
      title: '地址段',
      key: 'segments',
      width: 112,
      render: (_: unknown, record: Circuit) => (
        <span
          title={`${record.segment_count || 0} 段 / 公网 ${record.public_segment_count || 0} 段`}
          style={{
            ...TABLE_TEXT_STYLE,
            maxWidth: 96,
          }}
        >
          {`${record.segment_count || 0} 段 / 公网 ${record.public_segment_count || 0} 段`}
        </span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 78,
      render: (v: string) => <Tag style={{ fontWeight: 700 }} color={v === 'active' ? 'success' : 'default'}>{v === 'active' ? '启用' : '停用'}</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: unknown, record: Circuit) => (
        <Space size={6}>
          <Button type="link" style={{ paddingInline: 2, fontSize: 12, fontWeight: 600 }} onClick={() => handleViewTraffic(record)}>查看流量</Button>
          <Button type="link" style={{ paddingInline: 2, fontSize: 12, fontWeight: 600 }} onClick={() => openAuditModal(record)}>查看更改记录</Button>
          {canModify ? (
            <>
              <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(record)} />
              <Popconfirm title="确认删除该线路吗？" onConfirm={() => handleDelete(record.id)}>
                <Button type="text" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          ) : null}
        </Space>
      ),
    },
  ]
    .filter((column) => {
      if (isPrivateLine) {
        return !['operator_name', 'line_type', 'segments', 'interconnect_address', 'interconnect_type', 'routed_cidrs'].includes(column.key)
      }
      return !['customer_name', 'local_interconnect_address', 'remote_interconnect_address', 'local_routed_cidrs', 'remote_routed_cidrs', 'interconnect_address', 'interconnect_type', 'routing_mode', 'bfd_enabled', 'routed_cidrs'].includes(column.key)
    })
    .filter((column) => visibleColumns.includes(column.key))
    .sort((left, right) => COLUMN_ORDER.indexOf(left.key) - COLUMN_ORDER.indexOf(right.key))

  return (
    <Card
      title={title}
      extra={
        <Space>
          <Input
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={isPrivateLine ? '搜索线路、设备、IP、互联地址、网段、客户' : '搜索线路、设备、IP、地址段、运营商'}
            style={{ width: 320 }}
          />
          <Dropdown
            trigger={['click']}
            dropdownRender={() => (
              <div
                style={{
                  background: colorBgElevated,
                  border: `1px solid ${colorBorder}`,
                  borderRadius: 8,
                  boxShadow: '0 6px 16px rgba(0,0,0,0.12)',
                  padding: 12,
                  width: 220,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 8 }}>显示/隐藏列</div>
                <Checkbox.Group
                  value={visibleColumns}
                  onChange={(values) => {
                    const next = (values as string[]).filter((key) => COLUMN_ORDER.includes(key))
                    setVisibleColumns(next.includes('action') ? next : [...next, 'action'])
                  }}
                  style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
                >
                  {filteredColumnToggleOptions.map((option) => (
                    <Checkbox key={option.key} value={option.key}>
                      {option.label}
                    </Checkbox>
                  ))}
                </Checkbox.Group>
              </div>
            )}
          >
            <Button icon={<SettingOutlined />}>显示/隐藏列</Button>
          </Dropdown>
          {canModify ? <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增线路</Button> : null}
        </Space>
      }
    >
      <style>{`
        .circuit-table .ant-table {
          font-size: 12px;
        }
        .circuit-table .ant-table-thead > tr > th {
          background: #f3f3f3;
          color: #444;
          font-weight: 700;
          font-size: 12px;
          padding: 10px 8px;
          white-space: nowrap;
        }
        .circuit-table .ant-table-tbody > tr > td {
          padding: 9px 8px;
          font-size: 12px;
          font-weight: 600;
          vertical-align: middle;
          white-space: nowrap;
        }
        .circuit-table .ant-table-expanded-row > td {
          white-space: normal;
        }
        .circuit-table .ant-table-tbody > tr:hover > td {
          background: #fafcff;
        }
        .private-route-card .ant-card-head {
          min-height: 34px;
          padding: 0 12px;
        }
        .private-route-card .ant-card-head-title {
          padding: 8px 0;
          font-size: 13px;
          font-weight: 700;
        }
        .private-route-card .ant-card-body {
          padding: 10px;
        }
        .private-route-card .ant-form-item {
          margin-bottom: 0;
        }
        .private-route-card .ant-form-item-label > label {
          font-size: 12px;
          font-weight: 700;
        }
        .private-route-card .ant-input,
        .private-route-card .ant-input-number,
        .private-route-card .ant-input-number-input,
        .private-route-card .ant-select-selector {
          min-height: 32px !important;
        }
        .private-route-card .ant-card.ant-card-small {
          border-radius: 8px;
        }
        .private-interconnect-grid {
          border: 1px solid ${colorBorder};
          border-radius: 8px;
          padding: 10px 12px 2px;
          background: ${colorBgContainer};
          margin-bottom: 16px;
        }
        .private-interconnect-grid-header,
        .private-interconnect-grid-row {
          display: grid;
          grid-template-columns: 64px 150px minmax(0, 1fr) minmax(0, 1fr) 100px 130px 116px;
          gap: 12px;
          align-items: center;
        }
        .private-interconnect-grid-header {
          margin-bottom: 8px;
          color: #262626;
          font-size: 12px;
          font-weight: 800;
        }
        .private-interconnect-grid-row {
          margin-bottom: 8px;
        }
        .private-interconnect-grid-row .ant-form-item {
          margin-bottom: 0;
        }
        .private-interconnect-row-label {
          font-weight: 800;
          color: #262626;
        }
        .private-route-inline-header {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 88px 64px;
          gap: 10px;
          align-items: center;
          margin-bottom: 6px;
          padding: 0 2px;
        }
        .private-route-inline-header span {
          font-size: 12px;
          font-weight: 700;
          color: #262626;
        }
        .private-route-inline-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 88px 64px;
          gap: 10px;
          align-items: center;
          margin-bottom: 8px;
        }
        .private-route-inline-row:last-child {
          margin-bottom: 0;
        }
        .private-route-inline-actions {
          display: flex;
          justify-content: flex-end;
          gap: 6px;
        }
        .public-segment-inline-header {
          display: grid;
          grid-template-columns: 190px 104px 104px 100px 64px 64px;
          gap: 10px;
          align-items: center;
          margin-bottom: 6px;
          padding: 0 2px;
        }
        .public-segment-inline-header span {
          font-size: 12px;
          font-weight: 700;
          color: #262626;
        }
        .public-segment-inline-row {
          display: grid;
          grid-template-columns: 190px 104px 104px 100px 64px 64px;
          gap: 10px;
          align-items: center;
          margin-bottom: 8px;
        }
        .public-segment-inline-row:last-child {
          margin-bottom: 0;
        }
        .public-segment-inline-actions {
          display: flex;
          justify-content: flex-end;
          gap: 6px;
        }
      `}</style>
      <div
        style={{
          marginBottom: 16,
          padding: 12,
          background: colorBgContainer,
          border: `1px solid ${colorBorder}`,
          borderRadius: 4,
        }}
      >
        <div style={{ fontWeight: 'bold', marginBottom: 14, fontSize: 14, color: colorText }}>▼ 过滤器</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {renderFilterChips({
            label: '机房',
            value: datacenterFilter,
            onChange: setDatacenterFilter,
            options: datacenterFilterOptions,
          })}
          {isPrivateLine
            ? renderFilterChips({
                label: '客户',
                value: customerFilter,
                onChange: setCustomerFilter,
                options: customerFilterOptions,
              })
            : renderFilterChips({
                label: '运营商',
                value: operatorFilter,
                onChange: setOperatorFilter,
                options: operatorFilterOptions,
              })}
          {renderFilterChips({
            label: '接入方式',
            value: accessModeFilter,
            onChange: setAccessModeFilter,
            options: [
              { value: 'single', label: '单线' },
              { value: 'dual', label: '双线' },
            ],
          })}
        </div>
      </div>
      <div className="circuit-table">
      <Table
        rowKey="id"
        loading={loading}
        dataSource={filteredCircuits}
        size="small"
        bordered
        scroll={{ x: 1560 }}
        expandable={{
          expandedRowKeys,
          onExpand: (expanded, record) => {
            setExpandedRowKeys((prev) => (
              expanded ? [...prev, record.id] : prev.filter((item) => item !== record.id)
            ))
          },
          expandedRowRender: (record) => (
            <div style={{ display: 'grid', gap: 12 }}>
              {(() => {
                const dualLacpCircuit = isDualLacpCircuit(record)
                return (
                  <>
              <div>
                <strong>接入信息：</strong>
                <div style={{ marginTop: 8, display: 'grid', gap: 6 }}>
                  {renderTerminationDetailLine('主接入', record.primary_device_name, record.primary_device_ip, record.primary_port_name, record.primary_port_rate)}
                  {record.access_mode === 'dual'
                    ? (
                      <>
                        {renderTerminationDetailLine('备接入', record.secondary_device_name, record.secondary_device_ip, record.secondary_port_name, record.secondary_port_rate)}
                        {dualLacpCircuit
                          ? renderAggregationDetailLine(
                              record.aggregation_interface_name,
                              getAggregationPlacementLabel(record.aggregation_interface_name),
                              getAggregatedPortRate(record.primary_port_rate, record.secondary_port_rate)
                            )
                          : null}
                        <div style={{ color: '#666', fontWeight: 600, marginTop: 2 }}>
                          双线接入策略：{record.dual_link_mode ? (dualLinkModeLabelMap[record.dual_link_mode] || record.dual_link_mode) : '未填写'}
                        </div>
                      </>
                    )
                    : null}
                </div>
              </div>
              {isPrivateLine ? (
                <div style={{ display: 'grid', gap: 8, color: '#666', fontWeight: 600 }}>
                  <div><strong style={{ color: '#333' }}>客户：</strong>{record.customer_name || '未绑定客户'}</div>
                  {renderPrivateLineInterconnectLine(
                    dualLacpCircuit ? '逻辑' : (record.access_mode === 'dual' ? '主' : ''),
                    getPrimaryLocalAddress(record),
                    getPrimaryRemoteAddress(record),
                    record.primary_vlan_id,
                    getPrimaryInterconnectType(record),
                  )}
                  <div><strong style={{ color: '#333' }}>{dualLacpCircuit ? '逻辑互联方式：' : '主互联方式：'}</strong>{getInterconnectTypeLabel(getPrimaryInterconnectType(record))}</div>
                  <div><strong style={{ color: '#333' }}>{dualLacpCircuit ? '逻辑链路路由/探测：' : '主链路路由/探测：'}</strong>{getPrimaryRoutingMode(record) ? (routingModeOptions.find((item) => item.value === getPrimaryRoutingMode(record))?.label || getPrimaryRoutingMode(record)) : '未填写'} / {bfdModeLabelMap[getPrimaryBfdMode(record)] || getPrimaryBfdMode(record)}</div>
                  {record.access_mode === 'dual' && !dualLacpCircuit
                    ? (
                      <>
                        {renderPrivateLineInterconnectLine(
                          '备',
                          getSecondaryLocalAddress(record),
                          getSecondaryRemoteAddress(record),
                          record.secondary_vlan_id,
                          getSecondaryInterconnectType(record),
                        )}
                        <div><strong style={{ color: '#333' }}>备互联方式：</strong>{getInterconnectTypeLabel(getSecondaryInterconnectType(record))}</div>
                        <div><strong style={{ color: '#333' }}>备链路路由/探测：</strong>{getSecondaryRoutingMode(record) ? (routingModeOptions.find((item) => item.value === getSecondaryRoutingMode(record))?.label || getSecondaryRoutingMode(record)) : '未填写'} / {bfdModeLabelMap[getSecondaryBfdMode(record)] || getSecondaryBfdMode(record)}</div>
                      </>
                    )
                    : null}
                  <div><strong style={{ color: '#333' }}>本地IDC内网段：</strong>{renderCidrsHighlight(record.local_routed_cidrs)}</div>
                  <div><strong style={{ color: '#333' }}>对端IDC内网段：</strong>{renderCidrsHighlight(record.remote_routed_cidrs)}</div>
                </div>
              ) : (
                <div>
                  <strong>地址段：</strong>
                  {record.address_segments?.length ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 8 }}>
                      {record.address_segments.map((segment, index) => renderSegmentCard(segment, index, record.id))}
                    </div>
                  ) : (
                    '未录入地址段'
                  )}
                </div>
              )}
              <div>
                <strong>线路备注：</strong>
                <div style={{ marginTop: 8, color: '#666' }}>{record.description || '未填写备注'}</div>
              </div>
                  </>
                )
              })()}
            </div>
          ),
        }}
        columns={columns}
        pagination={{
          defaultPageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
        }}
      />
      </div>

      <Modal
        title={auditModalCircuit ? `${auditModalCircuit.name} - 完整更改记录` : '完整更改记录'}
        open={Boolean(auditModalCircuit)}
        onCancel={() => setAuditModalCircuit(null)}
        footer={null}
        width={920}
      >
        <div style={{ display: 'grid', gap: 8, maxHeight: '70vh', overflowY: 'auto' }}>
          {auditModalCircuit && auditMap[auditModalCircuit.id]?.length ? auditMap[auditModalCircuit.id].map((audit) => (
            <div key={audit.id} style={{ border: `1px solid ${colorBorder}`, borderRadius: 8, padding: '10px 12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
                <Space wrap>
                  <Tag color={audit.action === 'create' ? 'success' : audit.action === 'delete' ? 'error' : 'processing'}>
                    {audit.action === 'create' ? '新增' : audit.action === 'delete' ? '删除' : '修改'}
                  </Tag>
                  <Typography.Text>{audit.actor_username}</Typography.Text>
                </Space>
                <Typography.Text type="secondary">{formatDateTime(audit.created_at)}</Typography.Text>
              </div>
              <Typography.Paragraph style={{ marginTop: 6, marginBottom: 0, color: '#666' }}>
                {renderAuditDescription(audit)}
              </Typography.Paragraph>
            </div>
          )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无更改记录" />}
        </div>
      </Modal>

      <Modal
        title={editingCircuit ? '编辑线路' : '新增线路'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSubmit}
        confirmLoading={saving}
        destroyOnClose
        width={1120}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="线路名称" rules={[{ required: true, message: '请输入线路名称' }]}>
                <Input placeholder={isPrivateLine ? '例如：北京-客户A-专线1' : '例如：北京IDC-电信互联网出口A'} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="datacenter_id" label="所属IDC">
                <Select allowClear options={datacenters.map((item) => ({ value: item.id, label: item.name }))} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="vendor_id" label="供应商">
                <Select allowClear options={vendors.map((item) => ({ value: item.id, label: item.name }))} />
              </Form.Item>
            </Col>
            {isPrivateLine ? (
              <Col span={8}>
                <Form.Item name="customer_id" label="客户">
                  <Select allowClear showSearch optionFilterProp="label" options={customers.map((item) => ({ value: item.id, label: item.name }))} />
                </Form.Item>
              </Col>
            ) : (
              <Col span={8}>
                <Form.Item name="operator_name" label="运营商" rules={[{ required: true, message: '请选择运营商' }]}>
                  <Select options={operatorOptions.map((item) => ({ value: item, label: item }))} />
                </Form.Item>
              </Col>
            )}
            {fixedLineType ? null : (
              <Col span={8}>
                <Form.Item name="line_type" label="线路类型">
                  <Select options={lineTypeOptions} />
                </Form.Item>
              </Col>
            )}
            <Col span={8}>
              <Form.Item name="access_mode" label="接入方式">
                <Select options={accessModeOptions} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="bandwidth_mbps" label="带宽(Mbps)">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="status" label="状态">
                <Select options={[{ value: 'active', label: '启用' }, { value: 'inactive', label: '停用' }]} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="is_redundant" label="是否冗余" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name="redundancy_note" label="冗余说明">
                <Input placeholder="例如：与另一条联通线路互为备份" />
              </Form.Item>
            </Col>
                <Col span={8}>
                  <Form.Item
                    name="primary_device_id"
                    label="主接入交换机"
                rules={[{ required: true, message: '请选择主接入交换机' }]}
                  >
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      options={deviceOptions}
                      onChange={() => {
                        form.setFieldValue('primary_port_name', undefined)
                        form.setFieldValue('aggregation_selection', undefined)
                      }}
                    />
                  </Form.Item>
                </Col>
            <Col span={8}>
              <Form.Item
                name="primary_port_name"
                label="接入端口"
                rules={[{ required: true, message: '请输入主接入端口' }]}
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="请选择主接入端口"
                  options={getPortOptions(primaryDeviceId)}
                  disabled={!primaryDeviceId}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                name="primary_port_rate"
                label="端口速率"
                rules={[{ required: true, message: '请选择端口速率' }]}
              >
                <Select options={physicalPortRateOptions} placeholder="请选择端口速率" />
              </Form.Item>
            </Col>
            {accessMode === 'dual' ? (
              <>
                <Col span={8}>
                  <Form.Item name="secondary_device_id" label="备接入交换机" rules={[{ required: true, message: '请选择备接入交换机' }]}>
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      options={deviceOptions}
                      onChange={() => {
                        form.setFieldValue('secondary_port_name', undefined)
                        form.setFieldValue('aggregation_selection', undefined)
                      }}
                    />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="secondary_port_name" label="接入端口" rules={[{ required: true, message: '请输入接入端口' }]}>
                    <Select
                      showSearch
                      optionFilterProp="label"
                      placeholder="请选择备接入端口"
                      options={getPortOptions(secondaryDeviceId)}
                      disabled={!secondaryDeviceId}
                    />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="secondary_port_rate" label="端口速率" rules={[{ required: true, message: '请选择端口速率' }]}>
                    <Select options={physicalPortRateOptions} placeholder="请选择端口速率" />
                  </Form.Item>
                </Col>
                <Col span={24}>
                  <Form.Item name="dual_link_mode" label="双线接入策略" rules={[{ required: true, message: '请选择双线接入策略' }]}>
                    <Select options={dualLinkModeOptions} placeholder="请选择 LACP / 冷备 / 热备" />
                  </Form.Item>
                </Col>
                {isDualLacpMode ? (
                  <Col span={24}>
                    <Form.Item
                      name="aggregation_selection"
                      label="逻辑聚合接口（Bridge-Aggregation / Route-Aggregation）"
                      rules={[{ required: true, message: '请选择逻辑聚合接口' }]}
                    >
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder="请选择 Bridge-Aggregation / Route-Aggregation 接口"
                        options={aggregationOptions}
                      />
                    </Form.Item>
                  </Col>
                ) : null}
              </>
            ) : null}
            <Col span={24}>
              <Form.Item name="ip_address" label={isPrivateLine ? '线路标识 / 对端描述' : '线路主IP / 对端标识'}>
                <Input placeholder={isPrivateLine ? '例如：专线对端PE或线路标识' : '例如：对端互联网网关IP或线路标识IP'} />
              </Form.Item>
            </Col>
            {isPrivateLine ? (
              <>
                <Col span={24}>
                  <div className="private-interconnect-grid">
                    <div className="private-interconnect-grid-header">
                      <span />
                      <span>互联方式</span>
                      <span>本端地址</span>
                      <span>对端地址</span>
                      <span>VLAN ID</span>
                      <span>路由协议</span>
                      <span>探测方式</span>
                    </div>
                    <div className="private-interconnect-grid-row">
                      <div className="private-interconnect-row-label">{isDualLacpMode ? '逻辑链路' : '主链路'}</div>
                      <Form.Item name="primary_interconnect_type" rules={[{ required: true, message: '请选择主互联方式' }]}>
                        <Select options={interconnectTypeOptions} placeholder="互联方式" />
                      </Form.Item>
                      <Form.Item name="primary_local_interconnect_ip" rules={[{ required: true, message: '请输入主本端地址' }]}>
                        <Input placeholder="例如：172.31.252.122/30" />
                      </Form.Item>
                      <Form.Item name="primary_remote_interconnect_ip" rules={[{ required: true, message: '请输入主对端地址' }]}>
                        <Input placeholder="例如：172.31.252.121/30" />
                      </Form.Item>
                      <Form.Item name="primary_vlan_id" rules={primaryInterconnectType === 'l2' ? [{ required: true, message: '请输入主VLAN ID' }] : []}>
                        <InputNumber min={1} max={4094} style={{ width: '100%' }} placeholder="例如：210" disabled={primaryInterconnectType !== 'l2'} />
                      </Form.Item>
                      <Form.Item name="primary_routing_mode">
                        <Select allowClear options={routingModeOptions} placeholder="路由协议" />
                      </Form.Item>
                      <Form.Item name="primary_bfd_mode">
                        <Select options={bfdModeOptions} placeholder="探测方式" />
                      </Form.Item>
                    </div>
                    {accessMode === 'dual' && !isDualLacpMode ? (
                      <div className="private-interconnect-grid-row">
                        <div className="private-interconnect-row-label">备链路</div>
                        <Form.Item name="secondary_interconnect_type" rules={[{ required: true, message: '请选择备互联方式' }]}>
                          <Select options={interconnectTypeOptions} placeholder="互联方式" />
                        </Form.Item>
                        <Form.Item name="secondary_local_interconnect_ip" rules={[{ required: true, message: '请输入备本端地址' }]}>
                          <Input placeholder="例如：172.31.252.130/30" />
                        </Form.Item>
                        <Form.Item name="secondary_remote_interconnect_ip" rules={[{ required: true, message: '请输入备对端地址' }]}>
                          <Input placeholder="例如：172.31.252.129/30" />
                        </Form.Item>
                        <Form.Item name="secondary_vlan_id" rules={secondaryInterconnectType === 'l2' ? [{ required: true, message: '请输入备VLAN ID' }] : []}>
                          <InputNumber min={1} max={4094} style={{ width: '100%' }} placeholder="例如：220" disabled={secondaryInterconnectType !== 'l2'} />
                        </Form.Item>
                        <Form.Item name="secondary_routing_mode">
                          <Select allowClear options={routingModeOptions} placeholder="路由协议" />
                        </Form.Item>
                        <Form.Item name="secondary_bfd_mode">
                          <Select options={bfdModeOptions} placeholder="探测方式" />
                        </Form.Item>
                      </div>
                    ) : null}
                  </div>
                </Col>
                <Col span={12}>
                  <Card size="small" title="本地IDC内网段" className="private-route-card">
                    <Form.List name="local_routed_networks">
                      {(fields, { add, remove }) => (
                        <Space direction="vertical" style={{ width: '100%' }} size="small">
                          <div className="private-route-inline-header">
                            <span>前缀</span>
                            <span>掩码</span>
                            <span>操作</span>
                          </div>
                          {fields.map((field) => (
                            <div key={field.key} className="private-route-inline-row">
                              <Form.Item
                                {...field}
                                name={[field.name, 'prefix']}
                                rules={[{ required: true, message: '请输入前缀' }]}
                              >
                                <Input placeholder="例如：192.168.10.0" />
                              </Form.Item>
                              <Form.Item
                                {...field}
                                name={[field.name, 'mask']}
                                rules={[{ required: true, message: '请输入掩码' }]}
                              >
                                <Input placeholder="例如：24" />
                              </Form.Item>
                              <div className="private-route-inline-actions">
                                {fields.length > 1 ? (
                                  <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                                ) : <span />}
                                <Button type="text" icon={<PlusOutlined />} onClick={() => add({ prefix: '', mask: '' })} />
                              </div>
                            </div>
                          ))}
                        </Space>
                      )}
                    </Form.List>
                  </Card>
                </Col>
                <Col span={12}>
                  <Card size="small" title="对端IDC内网段" className="private-route-card">
                    <Form.List name="remote_routed_networks">
                      {(fields, { add, remove }) => (
                        <Space direction="vertical" style={{ width: '100%' }} size="small">
                          <div className="private-route-inline-header">
                            <span>前缀</span>
                            <span>掩码</span>
                            <span>操作</span>
                          </div>
                          {fields.map((field) => (
                            <div key={field.key} className="private-route-inline-row">
                              <Form.Item
                                {...field}
                                name={[field.name, 'prefix']}
                                rules={[{ required: true, message: '请输入前缀' }]}
                              >
                                <Input placeholder="例如：172.16.10.0" />
                              </Form.Item>
                              <Form.Item
                                {...field}
                                name={[field.name, 'mask']}
                                rules={[{ required: true, message: '请输入掩码' }]}
                              >
                                <Input placeholder="例如：24" />
                              </Form.Item>
                              <div className="private-route-inline-actions">
                                {fields.length > 1 ? (
                                  <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                                ) : <span />}
                                <Button type="text" icon={<PlusOutlined />} onClick={() => add({ prefix: '', mask: '' })} />
                              </div>
                            </div>
                          ))}
                        </Space>
                      )}
                    </Form.List>
                  </Card>
                </Col>
              </>
            ) : null}
            <Col span={24}>
              <Form.Item name="description" label="备注">
                <Input.TextArea rows={3} />
              </Form.Item>
            </Col>
          </Row>

          {isPrivateLine ? null : <Card size="small" title="线路地址段" className="private-route-card" style={{ marginTop: 8 }}>
            <Form.List name="address_segments">
              {(fields, { add, remove }) => (
                <Space direction="vertical" style={{ width: '100%' }} size="small">
                  <div className="public-segment-inline-header">
                    <span>地址段/CIDR</span>
                    <span>类型</span>
                    <span>用途</span>
                    <span>网关IP</span>
                    <span>公网</span>
                    <span>操作</span>
                  </div>
                  {fields.map((field) => (
                    <div key={field.key} className="public-segment-inline-row">
                      <Form.Item
                        {...field}
                        name={[field.name, 'cidr']}
                        rules={[{ required: true, message: '请输入地址段' }]}
                      >
                        <Input placeholder="例如：1.1.1.0/30 或 2.2.2.0/26" />
                      </Form.Item>
                      <Form.Item
                        {...field}
                        name={[field.name, 'segment_type']}
                        rules={[{ required: true, message: '请选择类型' }]}
                      >
                        <Select options={segmentTypeOptions} />
                      </Form.Item>
                      <Form.Item {...field} name={[field.name, 'usage']}>
                        <Input placeholder="例如：业务公网" />
                      </Form.Item>
                      <Form.Item {...field} name={[field.name, 'gateway_ip']}>
                        <Input placeholder="可选" />
                      </Form.Item>
                      <Form.Item {...field} name={[field.name, 'is_public']} valuePropName="checked">
                        <Switch />
                      </Form.Item>
                      <div className="public-segment-inline-actions">
                        {fields.length > 1 ? (
                          <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                        ) : <span />}
                        <Button type="text" icon={<PlusOutlined />} onClick={() => add({ segment_type: 'interconnect', is_public: false })} />
                      </div>
                    </div>
                  ))}
                </Space>
              )}
            </Form.List>
          </Card>}
        </Form>
      </Modal>
    </Card>
  )
}

export default CircuitList
