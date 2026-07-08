import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import type { DragEvent } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Drawer,
  Dropdown,
  Input,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getDeviceOverview,
  getDeviceProtocolNeighbors,
  refreshMonitorDevice,
  type DeviceOverviewItem,
  type DeviceProtocolSummary,
  type ProtocolNeighbor,
} from '../../api/metrics'
import { updateDevice } from '../../api/devices'

const { Text } = Typography

const VENDOR_OPTIONS = [
  { value: '', label: '全部厂商' },
  { value: 'Aster', label: 'AsterNOS/AsterOS' },
  { value: 'H3C', label: 'H3C' },
  { value: '华为', label: '华为' },
  { value: '锐捷', label: '锐捷' },
  { value: 'Hillstone', label: 'Hillstone/山石' },
]

const DATACENTER_ALL_VALUE = '__all__'
const DATACENTER_BADGE_COLORS = [
  { background: '#f6ffed', border: '#b7eb8f', color: '#389e0d' },
  { background: '#e6f4ff', border: '#91caff', color: '#0958d9' },
  { background: '#fff7e6', border: '#ffd591', color: '#d46b08' },
  { background: '#f9f0ff', border: '#d3adf7', color: '#722ed1' },
  { background: '#fff0f6', border: '#ffadd2', color: '#c41d7f' },
  { background: '#fcffe6', border: '#eaff8f', color: '#7cb305' },
  { background: '#e6fffb', border: '#87e8de', color: '#08979c' },
  { background: '#fff1f0', border: '#ffa39e', color: '#cf1322' },
  { background: '#f0f5ff', border: '#adc6ff', color: '#1d39c4' },
  { background: '#fffbe6', border: '#ffe58f', color: '#ad6800' },
]

const CONNECTIVITY_OPTIONS = [
  { value: '', label: '全部连通性' },
  { value: 'snmp_reachable', label: 'SNMP可达' },
  { value: 'snmp_unreachable', label: 'SNMP不可达' },
  { value: 'exporter_reachable', label: 'Exporter可达' },
  { value: 'exporter_unreachable', label: 'Exporter不可达' },
  { value: 'reachable', label: '全部可达' },
  { value: 'unreachable', label: '全部不可达' },
  { value: 'unknown', label: '未知' },
  { value: 'not_configured', label: '未配置' },
]

const SORT_OPTIONS = [
  { value: 'ip_asc', label: 'IP 升序' },
  { value: 'ip_desc', label: 'IP 降序' },
  { value: 'cpu_desc', label: 'CPU 从高到低' },
  { value: 'memory_desc', label: '内存从高到低' },
  { value: 'temperature_desc', label: '温度从高到低' },
  { value: 'storage_desc', label: '存储从高到低' },
  { value: 'hardware_down_desc', label: '硬件异常优先' },
  { value: 'protocol_down_desc', label: 'BGP/OSPF 异常优先' },
  { value: 'vendor_asc', label: '厂商 A-Z' },
  { value: 'model_asc', label: '型号 A-Z' },
]

const REFRESH_OPTIONS = [
  { value: 0, label: '手动刷新' },
  { value: 300, label: '每5分钟刷新' },
  { value: 600, label: '每10分钟刷新' },
  { value: 900, label: '每15分钟刷新' },
]

const ipToNumber = (ip?: string) => {
  const parts = String(ip || '').split('.').map((item) => Number(item))
  if (parts.length !== 4 || parts.some((item) => !Number.isInteger(item) || item < 0 || item > 255)) return 0
  return parts.reduce((sum, part) => (sum << 8) + part, 0)
}

const compareIpAddress = (left?: string, right?: string) => {
  const leftValue = ipToNumber(left)
  const rightValue = ipToNumber(right)
  if (leftValue !== rightValue) return leftValue - rightValue
  return String(left || '').localeCompare(String(right || ''), undefined, { numeric: true, sensitivity: 'base' })
}

const compareText = (left?: string | number | null, right?: string | number | null) =>
  String(left ?? '').localeCompare(String(right ?? ''), 'zh-CN', { numeric: true, sensitivity: 'base' })

const extractLastInterfaceToken = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return ''
  const patterns = [
    /(?:FourHundredGigE|400G)\d+(?:\/\d+)+/i,
    /(?:TwoHundredGigE|200G)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:HundredGigE|100G)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:FiftyGigE|50G)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:Twenty-FiveGigE|TwentyFiveGigE|25G)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:Ten-GigabitEthernet|TenGigabitEthernet|10G)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:M-GigabitEthernet|MGigabitEthernet|GigabitEthernet|GE)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:XGigabitEthernet|XGE)\d+(?:\/\d+)+(?::\d+)?/i,
    /(?:Bridge-Aggregation|Eth-Trunk|Port-Channel|Vlan-interface|LoopBack|NULL|InLoopBack|Auxiliary)\S*/i,
  ]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (match) return match[0]
  }
  return text
}

const formatShortInterfaceName = (value?: string | null) => {
  const token = extractLastInterfaceToken(value)
  if (!token) return '-'
  return token
    .replace(/^FourHundredGigE/i, '400G')
    .replace(/^TwoHundredGigE/i, '200G')
    .replace(/^HundredGigE/i, '100G')
    .replace(/^FiftyGigE/i, '50G')
    .replace(/^Twenty-FiveGigE/i, '25G')
    .replace(/^TwentyFiveGigE/i, '25G')
    .replace(/^Ten-GigabitEthernet/i, '10G')
    .replace(/^TenGigabitEthernet/i, '10G')
    .replace(/^M-GigabitEthernet/i, 'GE')
    .replace(/^MGigabitEthernet/i, 'GE')
    .replace(/^GigabitEthernet/i, 'GE')
    .replace(/^XGigabitEthernet/i, 'XGE')
}

const formatDeviceOnlyName = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  return text.replace(/^to-/i, '').replace(/-(?:FourHundredGigE|TwoHundredGigE|HundredGigE|FiftyGigE|Twenty-FiveGigE|TwentyFiveGigE|Ten-GigabitEthernet|TenGigabitEthernet|GigabitEthernet|M-GigabitEthernet|MGigabitEthernet|XGigabitEthernet)\d+(?:\/\d+)+(?:[:/]\d+)?$/i, '')
}

const protocolNeighborDisplayName = (
  record?:
    | Pick<ProtocolNeighbor, 'remote_display_name' | 'remote_system'>
    | { remoteSystem?: string | null; remoteDisplayName?: string | null }
    | null
) => {
  if (!record) return '-'
  const data = record as {
    remote_display_name?: string | null
    remote_system?: string | null
    remoteDisplayName?: string | null
    remoteSystem?: string | null
  }
  const displayName = data.remote_display_name || data.remoteDisplayName
  const systemName = data.remote_system || data.remoteSystem
  return formatDeviceOnlyName(displayName || systemName)
}

const temperatureTooltip = (record: DeviceOverviewItem) => {
  const details = record.resources.temperature_details || []
  if (!details.length) return null
  const sorted = [...details]
    .filter((item) => item?.temperature !== null && item?.temperature !== undefined)
    .sort((a, b) => Number(b.temperature || 0) - Number(a.temperature || 0))
  if (!sorted.length) return null
  return (
    <div style={{ maxHeight: 320, overflow: 'auto', minWidth: 180 }}>
      {sorted.map((item, index) => (
        <div key={`${item.sensor || 'temp'}-${index}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <span>{item.sensor || '-'}</span>
          <span>{Number(item.temperature).toFixed(1)}℃</span>
        </div>
      ))}
    </div>
  )
}

const normalizePercent = (value?: number | null) => {
  if (value === undefined || value === null) return null
  const numeric = Number(value)
  return numeric
}

const hashText = (value: string) => {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0
  }
  return hash
}

const DatacenterBadge = ({ name, code }: { name?: string | null; code?: string | null }) => {
  const label = (name || '').trim()
  if (!label) return <Text type="secondary">-</Text>
  const tone = DATACENTER_BADGE_COLORS[hashText(label) % DATACENTER_BADGE_COLORS.length]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        maxWidth: '100%',
        padding: '2px 8px',
        borderRadius: 4,
        border: `1px solid ${tone.border}`,
        background: tone.background,
        color: tone.color,
        fontSize: 12,
        fontWeight: 600,
        lineHeight: 1.4,
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {code ? <span style={{ opacity: 0.8, fontWeight: 500 }}>{code}</span> : null}
    </span>
  )
}

const formatPercent = (value?: number | null) => {
  const normalized = normalizePercent(value)
  if (normalized === null) return '-'
  return `${normalized.toFixed(1)}%`
}

const percentStatus = (value?: number | null) => {
  const normalized = normalizePercent(value)
  if (normalized === null) return 'normal'
  if (normalized >= 90) return 'exception'
  if (normalized >= 75) return 'active'
  return 'success'
}

const sourceLabel = (value?: string) => {
  if (value === 'controller_api') return '控制器'
  if (value === 'snmp') return 'SNMP'
  if (value === 'asternos_exporter') return 'Exporter'
  return ''
}

const connectivitySource = (item: DeviceOverviewItem) => {
  const source = String(item.connectivity?.type || item.monitor_source || 'snmp')
  if (source === 'asternos_exporter') return 'exporter'
  if (source === 'telemetry') return 'snmp'
  return source || 'snmp'
}

const connectivityKey = (item: DeviceOverviewItem) => {
  const status = String(item.connectivity?.status || 'unknown')
  if (['reachable', 'unreachable'].includes(status)) {
    return `${connectivitySource(item)}_${status}`
  }
  return status
}

const matchesConnectivityFilter = (item: DeviceOverviewItem, value?: string) => {
  if (!value) return true
  const status = String(item.connectivity?.status || 'unknown')
  return status === value || connectivityKey(item) === value
}

const ResourceCell = ({ value, source }: { value?: number | null; source?: string }) => {
  const normalized = normalizePercent(value)
  if (normalized === null) return <Text type="secondary">-</Text>
  const label = sourceLabel(source)
  return (
    <div style={{ minWidth: 100 }}>
      <Progress
        percent={Math.max(0, Math.min(100, normalized))}
        size="small"
        status={percentStatus(value)}
        format={() => formatPercent(value)}
      />
      {label ? <Tag color="blue" style={{ marginTop: 4 }}>{label}</Tag> : null}
    </div>
  )
}

const HardwareCell = ({
  total = 0,
  down = 0,
  label,
  statusKnown = true,
}: {
  total?: number
  down?: number
  label: string
  statusKnown?: boolean
}) => {
  if (!total) return <Text type="secondary">-</Text>
  if (!statusKnown) return <Tag color="blue">已识别 {total}/{total}</Tag>
  return <Tag color={down > 0 ? 'red' : 'green'}>{label} {total - down}/{total}</Tag>
}

const ProtocolCell = ({ data }: { data: DeviceProtocolSummary }) => {
  if (!data || data.total <= 0) return <Text type="secondary">-</Text>
  const color = data.down > 0 ? 'red' : 'green'
  return (
    <Space direction="vertical" size={2} style={{ lineHeight: 1.2 }}>
      <Space size={4} wrap={false}>
        <Tag color={color}>{data.up}/{data.total}</Tag>
      </Space>
      {data.down > 0 ? <Text type="danger">异常 {data.down}</Text> : null}
    </Space>
  )
}

const ConnectivityTag = ({ item }: { item: DeviceOverviewItem }) => {
  const { status, message: detail } = item.connectivity
  const source = connectivitySource(item)
  const typeLabel = source === 'exporter' ? 'Exporter' : 'SNMP'
  const color =
    status === 'reachable'
      ? source === 'exporter'
        ? 'green'
        : 'blue'
      : status === 'unreachable'
        ? 'red'
        : status === 'not_configured' || status === 'not_monitored'
          ? 'orange'
          : 'default'
  const label =
    status === 'reachable'
      ? `${typeLabel}可达`
      : status === 'unreachable'
        ? `${typeLabel}不可达`
        : status === 'not_configured'
          ? `${typeLabel}未配置`
          : status === 'not_monitored'
            ? '未监控'
            : `${typeLabel}未知`

  return (
    <Tooltip title={detail}>
      <Tag color={color}>{label}</Tag>
    </Tooltip>
  )
}

const formatDuration = (seconds?: number | null, fallback?: string | null) => {
  if (fallback) return fallback
  if (seconds === undefined || seconds === null) return '-'
  let remaining = Math.max(0, Math.floor(seconds))
  const days = Math.floor(remaining / 86400)
  remaining %= 86400
  const hours = Math.floor(remaining / 3600)
  remaining %= 3600
  const minutes = Math.floor(remaining / 60)
  if (days > 0) return `${days}天${hours}小时`
  if (hours > 0) return `${hours}小时${minutes}分钟`
  return `${minutes}分钟`
}

const neighborColumnSearch = (
  getValue: (record: ProtocolNeighbor) => string | number | null | undefined,
  placeholder: string
) => ({
  filterDropdown: ({ setSelectedKeys, selectedKeys, confirm, clearFilters }: any) => (
    <div style={{ padding: 8, width: 220 }} onKeyDown={(event) => event.stopPropagation()}>
      <Input
        autoFocus
        allowClear
        placeholder={placeholder}
        value={selectedKeys[0]}
        onChange={(event) => setSelectedKeys(event.target.value ? [event.target.value] : [])}
        onPressEnter={() => confirm()}
        style={{ marginBottom: 8, display: 'block' }}
      />
      <Space>
        <Button type="primary" size="small" icon={<SearchOutlined />} onClick={() => confirm()}>
          搜索
        </Button>
        <Button
          size="small"
          onClick={() => {
            clearFilters?.()
            confirm()
          }}
        >
          重置
        </Button>
      </Space>
    </div>
  ),
  filterIcon: (filtered: boolean) => <SearchOutlined style={{ color: filtered ? '#1677ff' : undefined }} />,
  onFilter: (value: any, record: ProtocolNeighbor) =>
    String(getValue(record) ?? '').toLowerCase().includes(String(value ?? '').toLowerCase()),
})

const formatSoftwareVersion = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return ''

  const softwareMatch = text.match(/Software\s+Version\s+([^,\r\n]+)(?:,\s*(Release\s+[^\r\n,]+))?/i)
  if (softwareMatch) {
    return [
      `Software Version ${softwareMatch[1].trim()}`,
      softwareMatch[2]?.trim(),
    ].filter(Boolean).join(', ')
  }

  const versionMatch = text.match(/^Version\s+([^,\r\n]+)(?:,\s*(Release\s+[^\r\n,]+))?$/i)
  if (versionMatch) {
    return [
      `Software Version ${versionMatch[1].trim()}`,
      versionMatch[2]?.trim(),
    ].filter(Boolean).join(', ')
  }

  const asterosSoftwareMatch = text.match(/(?:^|[\/\s])Software\s+([A-Za-z]?\d[^\s\/,\r\n]*)/i)
  if (asterosSoftwareMatch) {
    return `Software ${asterosSoftwareMatch[1].trim()}`
  }

  const asterosVersionMatch = text.match(/^[A-Za-z]?\d[\w.-]*$/)
  if (asterosVersionMatch) {
    return `Software ${text}`
  }

  return text
}

const normalizeDeviceName = (value?: string | null) => String(value || '').trim().toLowerCase()

const isSnmpNameMismatch = (record: DeviceOverviewItem) => {
  const sysName = normalizeDeviceName(record.system_info?.sys_name)
  const enteredName = normalizeDeviceName(record.device.name || record.device.hostname)
  return Boolean(sysName && enteredName && sysName !== enteredName)
}

const normalizeModelName = (value?: string | null) =>
  String(value || '')
    .trim()
    .toLowerCase()
    .replace(/^(densivelo|h3c|yillion)\s+/i, '')
    .replace(/\s+/g, '')

const formatCollectedModel = (value?: string | null) =>
  String(value || '')
    .trim()
    .replace(/^(Densivelo|H3C|Yillion)\s+/i, '')

const isSnmpModelMismatch = (record: DeviceOverviewItem) => {
  const snmpModel = normalizeModelName(record.system_info?.snmp_model)
  const enteredModel = normalizeModelName(record.device.model)
  return Boolean(snmpModel && enteredModel && snmpModel !== enteredModel)
}


const normalizeVendorText = (value?: string | null) => {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return ''
  if (['ruijie', '锐捷', 'rgos'].some((marker) => text.includes(marker))) return 'ruijie 锐捷 rgos'
  if (['h3c', '华三', '新华三', 'comware'].some((marker) => text.includes(marker))) return 'h3c 华三 新华三 comware'
  if (['hillstone', '山石'].some((marker) => text.includes(marker))) return 'hillstone 山石'
  if (['aster', 'asternos', 'asterfusion', '星融元'].some((marker) => text.includes(marker))) return 'aster asternos asterfusion 星融元'
  return text
}

const matchesVendorFilter = (rawVendor?: string | null, keyword?: string) => {
  const key = String(keyword || '').trim().toLowerCase()
  if (!key) return true
  const normalizedVendor = normalizeVendorText(rawVendor)
  const normalizedKey = normalizeVendorText(key)
  return normalizedVendor.includes(key) || normalizedVendor.includes(normalizedKey.split(' ')[0])
}

const OVERVIEW_CACHE_KEY = 'device-overview:last-success'
const COLUMN_ORDER_STORAGE_KEY = 'device-overview:visible-column-order-v2'
const COLUMN_WIDTH_STORAGE_KEY = 'device-overview:column-widths-v1'

const DEFAULT_COLUMN_WIDTHS: Record<string, number> = {
  device: 320,
  vendor: 170,
  datacenter: 240,
  uptime: 130,
  software_version: 210,
  connectivity: 130,
  cpu: 115,
  memory: 115,
  temperature: 95,
  storage: 115,
  fan: 100,
  power: 100,
  bgp: 120,
  ospf: 120,
  updated_at: 160,
  detail: 145,
}

const MIN_COLUMN_WIDTHS: Record<string, number> = {
  device: 220,
  vendor: 130,
  datacenter: 180,
  detail: 120,
}

const ResizableHeaderCell = ({ onResizeStart, children, style, ...restProps }: any) => (
  <th
    {...restProps}
    style={{
      ...style,
      position: 'relative',
    }}
  >
    {children}
    {onResizeStart ? (
      <span
        onMouseDown={onResizeStart}
        onClick={(event: ReactMouseEvent<HTMLSpanElement>) => event.stopPropagation()}
        style={{
          position: 'absolute',
          top: 0,
          right: -4,
          width: 8,
          height: '100%',
          cursor: 'col-resize',
          userSelect: 'none',
          zIndex: 3,
        }}
      />
    ) : null}
  </th>
)

const DEFAULT_VISIBLE_COLUMN_KEYS = [
  'datacenter',
  'uptime',
  'vendor',
  'connectivity',
  'cpu',
  'memory',
  'temperature',
  'bgp',
  'ospf',
  'updated_at',
]

type DeviceOverviewCachePayload = {
  items: DeviceOverviewItem[]
  savedAt: string
}

const DeviceOverview = () => {
  const [loading, setLoading] = useState(false)
  const [overviewProgress, setOverviewProgress] = useState(0)
  const [refreshingDeviceId, setRefreshingDeviceId] = useState<number | null>(null)
  const [items, setItems] = useState<DeviceOverviewItem[]>([])
  const [search, setSearch] = useState('')
  const [vendor, setVendor] = useState('')
  const [datacenter, setDatacenter] = useState(DATACENTER_ALL_VALUE)
  const [model, setModel] = useState('')
  const [connectivity, setConnectivity] = useState('')
  const [sortKey, setSortKey] = useState('ip_asc')
  const [refreshIntervalSeconds, setRefreshIntervalSeconds] = useState(300)
  const [tablePageSize, setTablePageSize] = useState(20)
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(() => {
    try {
      const raw = window.localStorage.getItem(COLUMN_WIDTH_STORAGE_KEY)
      const saved = raw ? JSON.parse(raw) : null
      return { ...DEFAULT_COLUMN_WIDTHS, ...(saved || {}) }
    } catch {
      return DEFAULT_COLUMN_WIDTHS
    }
  })
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailProgress, setDetailProgress] = useState(0)
  const [detailDevice, setDetailDevice] = useState<DeviceOverviewItem | null>(null)
  const [neighbors, setNeighbors] = useState<{ bgp: ProtocolNeighbor[]; ospf: ProtocolNeighbor[]; lldp: ProtocolNeighbor[] }>({ bgp: [], ospf: [], lldp: [] })
  const detailCacheRef = useRef<Record<number, { at: number; neighbors: { bgp: ProtocolNeighbor[]; ospf: ProtocolNeighbor[]; lldp: ProtocolNeighbor[] } }>>({})
  const [lldpPageSize, setLldpPageSize] = useState(20)
  const [syncingField, setSyncingField] = useState<string | null>(null)
  const filtersReadyRef = useRef(false)
  const resizeStateRef = useRef<{ key: string; startX: number; startWidth: number } | null>(null)
  const [draggingColumnKey, setDraggingColumnKey] = useState<string | null>(null)
  const [dragOverColumnKey, setDragOverColumnKey] = useState<string | null>(null)
  const [visibleColumnKeys, setVisibleColumnKeys] = useState<string[]>(() => {
    try {
      const raw = window.localStorage.getItem(COLUMN_ORDER_STORAGE_KEY)
      const saved = raw ? JSON.parse(raw) : null
      return Array.isArray(saved) && saved.length > 0 ? saved.map(String) : DEFAULT_VISIBLE_COLUMN_KEYS
    } catch {
      return DEFAULT_VISIBLE_COLUMN_KEYS
    }
  })

  const getProgressHint = (progress: number) => {
    if (progress < 25) return '正在连接数据源...'
    if (progress < 55) return '正在读取设备数据...'
    if (progress < 80) return '正在汇总展示内容...'
    if (progress < 96) return '正在整理最后结果...'
    return '即将完成...'
  }

  const lldpPeerMap = useMemo(() => {
    const mapping = new Map<string, { remoteSystem: string; remotePort: string }>()
    for (const item of neighbors.lldp) {
      const localKeys = [
        formatShortInterfaceName(item.local_port_id),
        formatShortInterfaceName(item.local_port),
        String(item.local_port_num || '').trim(),
      ].filter(Boolean)
      const value = {
        remoteSystem: formatDeviceOnlyName(item.remote_system),
        remotePort: formatShortInterfaceName(item.remote_port_id || item.remote_port),
      }
      localKeys.forEach((key) => {
        if (key && key !== '-') mapping.set(key, value)
      })
    }
    return mapping
  }, [neighbors.lldp])

  const columnOptions = [
    { label: '所属机房', value: 'datacenter' },
    { label: '运行时间', value: 'uptime' },
    { label: '软件版本', value: 'software_version' },
    { label: '厂商/型号', value: 'vendor' },
    { label: '连通性', value: 'connectivity' },
    { label: 'CPU', value: 'cpu' },
    { label: '内存', value: 'memory' },
    { label: '温度', value: 'temperature' },
    { label: '存储', value: 'storage' },
    { label: '风扇', value: 'fan' },
    { label: '电源', value: 'power' },
    { label: 'BGP', value: 'bgp' },
    { label: 'OSPF', value: 'ospf' },
    { label: '更新时间', value: 'updated_at' },
  ]

  const columnLabelMap = useMemo(
    () => new Map(columnOptions.map((item) => [item.value, item.label])),
    []
  )

  const orderedColumnOptions = useMemo(() => {
    const visibleSet = new Set(visibleColumnKeys)
    const visibleItems = visibleColumnKeys
      .map((key) => columnOptions.find((item) => item.value === key))
      .filter(Boolean) as typeof columnOptions
    const hiddenItems = columnOptions.filter((item) => !visibleSet.has(item.value))
    return [...visibleItems, ...hiddenItems]
  }, [visibleColumnKeys])

  const updateVisibleColumnKeys = (updater: string[] | ((current: string[]) => string[])) => {
    setVisibleColumnKeys((current) => {
      const next = typeof updater === 'function' ? updater(current) : updater
      window.localStorage.setItem(COLUMN_ORDER_STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }

  const toggleColumnVisible = (key: string, checked: boolean) => {
    updateVisibleColumnKeys((current) => {
      if (checked) {
        return current.includes(key) ? current : [...current, key]
      }
      return current.filter((item) => item !== key)
    })
  }


  const handleResizeStart = (key: string) => (event: ReactMouseEvent<HTMLSpanElement>) => {
    event.preventDefault()
    event.stopPropagation()
    resizeStateRef.current = {
      key,
      startX: event.clientX,
      startWidth: columnWidths[key] || DEFAULT_COLUMN_WIDTHS[key] || 120,
    }
  }

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const resizeState = resizeStateRef.current
      if (!resizeState) return
      const minWidth = MIN_COLUMN_WIDTHS[resizeState.key] || 90
      const nextWidth = Math.max(minWidth, resizeState.startWidth + event.clientX - resizeState.startX)
      setColumnWidths((prev) => {
        const next = { ...prev, [resizeState.key]: nextWidth }
        window.localStorage.setItem(COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(next))
        return next
      })
    }
    const handleMouseUp = () => {
      resizeStateRef.current = null
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  const moveVisibleColumn = (sourceKey: string, targetKey: string) => {
    if (sourceKey === targetKey) return
    updateVisibleColumnKeys((current) => {
      const sourceIndex = current.indexOf(sourceKey)
      const targetIndex = current.indexOf(targetKey)
      if (sourceIndex < 0 || targetIndex < 0) return current
      const next = [...current]
      const [source] = next.splice(sourceIndex, 1)
      next.splice(targetIndex, 0, source)
      return next
    })
  }

  const loadData = async (options?: { silent?: boolean }) => {
    const silent = Boolean(options?.silent)
    let timer: number | null = null
    if (!silent || items.length === 0) {
      setLoading(true)
      let progress = 5
      setOverviewProgress(progress)
      timer = window.setInterval(() => {
        progress = Math.min(
          progress + (progress < 20 ? 4 : progress < 45 ? 5 : progress < 70 ? 4 : progress < 85 ? 2 : 1),
          95,
        )
        setOverviewProgress(progress)
      }, 700)
    }
    try {
      const result = await getDeviceOverview({
        monitored_only: true,
        include_storage: visibleColumnKeys.includes('storage'),
        include_hardware: visibleColumnKeys.includes('fan') || visibleColumnKeys.includes('power'),
        include_sessions: false,
        limit: 1000,
      })
      const nextItems = result.items || []
      setItems(nextItems)
      if (!silent || items.length === 0) {
        setOverviewProgress(100)
      }
      const payload: DeviceOverviewCachePayload = {
        items: nextItems,
        savedAt: new Date().toISOString(),
      }
      window.localStorage.setItem(OVERVIEW_CACHE_KEY, JSON.stringify(payload))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取设备总览失败')
    } finally {
      if (timer) {
        window.clearInterval(timer)
      }
      if (!silent || items.length === 0) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    let hasCachedOverview = false
    try {
      const raw = window.localStorage.getItem(OVERVIEW_CACHE_KEY)
      if (raw) {
        const payload = JSON.parse(raw) as DeviceOverviewCachePayload
        if (Array.isArray(payload.items) && payload.items.length > 0) {
          setItems(payload.items)
          hasCachedOverview = true
        }
      }
    } catch {
      window.localStorage.removeItem(OVERVIEW_CACHE_KEY)
    }
    loadData({ silent: hasCachedOverview })
  }, [])

  useEffect(() => {
    if (!filtersReadyRef.current) {
      filtersReadyRef.current = true
      return
    }
    const timer = window.setTimeout(() => {
      loadData({ silent: items.length > 0 })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [visibleColumnKeys])

  useEffect(() => {
    if (!refreshIntervalSeconds) return undefined
    const timer = window.setInterval(() => {
      loadData({ silent: true })
    }, refreshIntervalSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [refreshIntervalSeconds, visibleColumnKeys, items.length])

  const datacenterOptions = useMemo(() => {
    const unique = new Map<string, string>()
    for (const item of items) {
      const name = item.device.datacenter?.name?.trim()
      if (name) {
        unique.set(name, name)
      }
    }
    return [
      { value: DATACENTER_ALL_VALUE, label: '全部机房' },
      ...Array.from(unique.values())
        .sort((left, right) => left.localeCompare(right, 'zh-CN'))
        .map((value) => ({ value, label: value })),
    ]
  }, [items])

  const filteredItems = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    const modelKeyword = model.trim().toLowerCase()
    return items.filter((item) => {
      if (datacenter !== DATACENTER_ALL_VALUE && (item.device.datacenter?.name || '') !== datacenter) {
        return false
      }
      if (vendor && !matchesVendorFilter(item.device.vendor, vendor)) {
        return false
      }
      if (modelKeyword && !String(item.device.model || '').toLowerCase().includes(modelKeyword)) {
        return false
      }
      if (!matchesConnectivityFilter(item, connectivity)) {
        return false
      }
      if (!keyword) {
        return true
      }
      const haystack = [
        item.device.name,
        item.device.ip_address,
        item.device.vendor,
        item.device.model,
        item.device.datacenter?.name,
        item.system_info?.sys_name,
        item.system_info?.snmp_model,
        item.system_info?.software_version,
        item.system_info?.serial_number,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(keyword)
    })
  }, [connectivity, datacenter, items, model, search, vendor])

  const sortedItems = useMemo(() => {
    const protocolDownCount = (item: DeviceOverviewItem) => item.protocols.bgp.down + item.protocols.ospf.down
    const list = [...filteredItems]
    return list.sort((a, b) => {
      if (sortKey === 'ip_desc') return compareIpAddress(b.device.ip_address, a.device.ip_address)
      if (sortKey === 'cpu_desc') return (normalizePercent(b.resources.cpu_percent) || -1) - (normalizePercent(a.resources.cpu_percent) || -1)
      if (sortKey === 'memory_desc') return (normalizePercent(b.resources.memory_percent) || -1) - (normalizePercent(a.resources.memory_percent) || -1)
      if (sortKey === 'temperature_desc') return (b.resources.temperature || -1) - (a.resources.temperature || -1)
      if (sortKey === 'storage_desc') return (normalizePercent(b.resources.storage_percent) || -1) - (normalizePercent(a.resources.storage_percent) || -1)
      if (sortKey === 'hardware_down_desc') {
        const aDown = (a.hardware?.fan_down || 0) + (a.hardware?.power_down || 0)
        const bDown = (b.hardware?.fan_down || 0) + (b.hardware?.power_down || 0)
        return bDown - aDown
      }
      if (sortKey === 'protocol_down_desc') return protocolDownCount(b) - protocolDownCount(a)
      if (sortKey === 'vendor_asc') return String(a.device.vendor || '').localeCompare(String(b.device.vendor || ''))
      if (sortKey === 'model_asc') return String(a.device.model || '').localeCompare(String(b.device.model || ''))
      return compareIpAddress(a.device.ip_address, b.device.ip_address)
    })
  }, [filteredItems, sortKey])

  const stats = useMemo(() => {
    const reachable = filteredItems.filter((item) => item.connectivity.status === 'reachable').length
    const bgpDown = filteredItems.reduce((sum, item) => sum + item.protocols.bgp.down, 0)
    const ospfDown = filteredItems.reduce((sum, item) => sum + item.protocols.ospf.down, 0)
    return { total: filteredItems.length, reachable, bgpDown, ospfDown }
  }, [filteredItems])

  const openDetail = async (item: DeviceOverviewItem) => {
    setDetailDevice(item)
    setDetailOpen(true)
    setDetailProgress(0)
    const cachedDetail = detailCacheRef.current[item.device.id]
    if (cachedDetail && Date.now() - cachedDetail.at < 2 * 60 * 1000) {
      setNeighbors(cachedDetail.neighbors)
      setDetailLoading(false)
      setDetailProgress(100)
      return
    }
    setDetailLoading(true)
    let progress = 5
    setDetailProgress(progress)
    const timer = window.setInterval(() => {
      progress = Math.min(
        progress + (progress < 20 ? 4 : progress < 45 ? 5 : progress < 70 ? 4 : progress < 85 ? 2 : 1),
        95,
      )
      setDetailProgress(progress)
    }, 700)
    try {
      const result = await getDeviceProtocolNeighbors(item.device.id)
      const nextNeighbors = {
        bgp: result.neighbors.bgp || [],
        ospf: result.neighbors.ospf || [],
        lldp: result.neighbors.lldp || [],
      }
      detailCacheRef.current[item.device.id] = { at: Date.now(), neighbors: nextNeighbors }
      setNeighbors(nextNeighbors)
      setDetailProgress(100)
    } catch (error: any) {
      setNeighbors({ bgp: [], ospf: [], lldp: [] })
      message.error(error?.response?.data?.detail || '获取协议邻居失败')
    } finally {
      window.clearInterval(timer)
      setDetailLoading(false)
    }
  }

  const handleDeviceRefresh = async (item: DeviceOverviewItem) => {
    setRefreshingDeviceId(item.device.id)
    try {
      delete detailCacheRef.current[item.device.id]
      const result = await refreshMonitorDevice(item.device.id)
      message.success(result.message || '已触发后台采集')
      window.setTimeout(() => {
        loadData()
      }, 2500)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '触发后台采集失败')
    } finally {
      setRefreshingDeviceId(null)
    }
  }

  const handleSyncDeviceField = async (
    item: DeviceOverviewItem,
    field: 'name' | 'model',
    value?: string | null
  ) => {
    const nextValue = String(value || '').trim()
    if (!nextValue) {
      message.warning('采集值为空，不能同步')
      return
    }
    const syncKey = `${item.device.id}:${field}`
    setSyncingField(syncKey)
    try {
      await updateDevice(item.device.id, { [field]: nextValue })
      setItems((current) =>
        current.map((row) =>
          row.device.id === item.device.id
            ? {
                ...row,
                device: {
                  ...row.device,
                  [field]: nextValue,
                },
              }
            : row
        )
      )
      const label = field === 'name' ? '设备名称' : '型号'
      message.success(`已将${label}同步为采集值：${nextValue}`)
      loadData()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '同步采集值失败')
    } finally {
      setSyncingField(null)
    }
  }

  const bgpRemoteDeviceName = (record: ProtocolNeighbor) => {
    const key = formatShortInterfaceName(record.interface)
    const peer = lldpPeerMap.get(key)
    return protocolNeighborDisplayName(peer) || '-'
  }

  const bgpRemotePortName = (record: ProtocolNeighbor) => {
    const key = formatShortInterfaceName(record.interface)
    return lldpPeerMap.get(key)?.remotePort || '-'
  }

  const neighborColumns = [
    {
      title: '邻居',
      dataIndex: 'peer',
      key: 'peer',
      width: 130,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareIpAddress(a.peer, b.peer),
      ...neighborColumnSearch((record) => record.peer, '搜索邻居'),
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(a.state || a.status, b.state || b.status),
      ...neighborColumnSearch((record) => record.state || record.status, '搜索状态'),
      render: (_: any, record: ProtocolNeighbor) => (
        <Tag color={record.status === 'up' ? 'green' : 'red'}>{record.state || record.status}</Tag>
      ),
    },
    {
      title: '接口',
      dataIndex: 'interface',
      key: 'interface',
      width: 120,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(formatShortInterfaceName(a.interface), formatShortInterfaceName(b.interface)),
      ...neighborColumnSearch((record) => formatShortInterfaceName(record.interface), '搜索接口'),
      render: (value: string | null) => formatShortInterfaceName(value),
    },
    {
      title: '对端设备',
      key: 'remote_system',
      width: 380,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(bgpRemoteDeviceName(a), bgpRemoteDeviceName(b)),
      ...neighborColumnSearch((record) => bgpRemoteDeviceName(record), '搜索对端设备'),
      render: (_: any, record: ProtocolNeighbor) => (
        <Tooltip title={bgpRemoteDeviceName(record)}>
          <Text style={{ whiteSpace: 'nowrap', display: 'inline-block' }}>{bgpRemoteDeviceName(record)}</Text>
        </Tooltip>
      ),
    },
    {
      title: '对端端口',
      key: 'remote_port',
      width: 120,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(bgpRemotePortName(a), bgpRemotePortName(b)),
      ...neighborColumnSearch((record) => bgpRemotePortName(record), '搜索对端端口'),
      render: (_: any, record: ProtocolNeighbor) => bgpRemotePortName(record),
    },
    {
      title: '本端IP',
      key: 'local_addr',
      width: 130,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareIpAddress(a.local_addr || a.local_address || '', b.local_addr || b.local_address || ''),
      ...neighborColumnSearch((record) => record.local_addr || record.local_address, '搜索本端IP'),
      render: (_: any, record: ProtocolNeighbor) => record.local_addr || record.local_address || '-',
    },
    {
      title: <span style={{ whiteSpace: 'nowrap' }}>Remote AS</span>,
      dataIndex: 'remote_as',
      key: 'remote_as',
      width: 110,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(a.remote_as, b.remote_as),
      ...neighborColumnSearch((record) => record.remote_as, '搜索 Remote AS'),
      render: (value: string | number | null) => value || '-',
    },
    {
      title: '持续时间',
      key: 'duration',
      width: 130,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => Number(a.duration_seconds || 0) - Number(b.duration_seconds || 0),
      ...neighborColumnSearch((record) => formatDuration(record.duration_seconds, record.duration_text), '搜索持续时间'),
      render: (_: any, record: ProtocolNeighbor) => formatDuration(record.duration_seconds, record.duration_text),
    },
  ]

  const ospfNeighborColumns = [
    {
      title: '邻居',
      dataIndex: 'peer',
      key: 'peer',
      width: 180,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareIpAddress(a.peer, b.peer),
      ...neighborColumnSearch((record) => record.peer, '搜索邻居'),
    },
    {
      title: '状态',
      key: 'status',
      width: 120,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(a.state || a.status, b.state || b.status),
      ...neighborColumnSearch((record) => record.state || record.status, '搜索状态'),
      render: (_: any, record: ProtocolNeighbor) => (
        <Tag color={record.status === 'up' ? 'green' : 'red'}>{record.state || record.status}</Tag>
      ),
    },
    {
      title: '接口',
      dataIndex: 'interface',
      key: 'interface',
      width: 160,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(formatShortInterfaceName(a.interface), formatShortInterfaceName(b.interface)),
      ...neighborColumnSearch((record) => formatShortInterfaceName(record.interface), '搜索接口'),
      render: (value: string | null) => formatShortInterfaceName(value),
    },
    {
      title: '持续时间',
      key: 'duration',
      width: 160,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => Number(a.duration_seconds || 0) - Number(b.duration_seconds || 0),
      ...neighborColumnSearch((record) => formatDuration(record.duration_seconds, record.duration_text), '搜索持续时间'),
      render: (_: any, record: ProtocolNeighbor) => formatDuration(record.duration_seconds, record.duration_text),
    },
  ]

  const lldpColumns = [
    {
      title: '本端端口',
      dataIndex: 'local_port',
      key: 'local_port',
      width: 120,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(formatShortInterfaceName(a.local_port_id || a.local_port), formatShortInterfaceName(b.local_port_id || b.local_port)),
      ...neighborColumnSearch((record) => formatShortInterfaceName(record.local_port_id || record.local_port), '搜索本端端口'),
      render: (_: any, record: ProtocolNeighbor) => formatShortInterfaceName(record.local_port_id || record.local_port || record.local_port_num),
    },
    {
      title: '对端设备',
      dataIndex: 'remote_system',
      key: 'remote_system',
      width: 360,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(protocolNeighborDisplayName(a), protocolNeighborDisplayName(b)),
      ...neighborColumnSearch((record) => protocolNeighborDisplayName(record), '搜索对端设备'),
      render: (_: any, record: ProtocolNeighbor) => (
        <Tooltip title={protocolNeighborDisplayName(record)}>
          <Text style={{ whiteSpace: 'nowrap', display: 'inline-block' }}>{protocolNeighborDisplayName(record)}</Text>
        </Tooltip>
      ),
    },
    {
      title: '类型',
      dataIndex: 'remote_kind',
      key: 'remote_kind',
      width: 90,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(a.remote_kind, b.remote_kind),
      render: (value: string | null) => {
        const text = value || '未知'
        const color = text === '网络设备' ? 'blue' : text === '服务器' ? 'green' : 'default'
        return <Tag color={color}>{text}</Tag>
      },
    },
    {
      title: '对端端口',
      dataIndex: 'remote_port',
      key: 'remote_port',
      width: 120,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(formatShortInterfaceName(a.remote_port_id || a.remote_port), formatShortInterfaceName(b.remote_port_id || b.remote_port)),
      ...neighborColumnSearch((record) => formatShortInterfaceName(record.remote_port_id || record.remote_port), '搜索对端端口'),
      render: (_: any, record: ProtocolNeighbor) => formatShortInterfaceName(record.remote_port_id || record.remote_port),
    },
    {
      title: '对端管理地址',
      dataIndex: 'remote_mgmt_addr',
      key: 'remote_mgmt_addr',
      width: 130,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareIpAddress(a.remote_mgmt_addr || '', b.remote_mgmt_addr || ''),
      ...neighborColumnSearch((record) => record.remote_mgmt_addr, '搜索管理地址'),
      render: (value: string | null) => value || '-',
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      sorter: (a: ProtocolNeighbor, b: ProtocolNeighbor) => compareText(a.source, b.source),
      render: (value: string | null) => <Tag color="blue">{value || 'SNMP'}</Tag>,
    },
  ]

  const allColumns: any[] = [
    {
      title: '设备',
      key: 'device',
      fixed: 'left',
      width: columnWidths.device,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => compareIpAddress(a.device.ip_address, b.device.ip_address),
      render: (_: any, record: DeviceOverviewItem) => {
        const enteredName = record.device.name || record.device.hostname || record.device.ip_address
        const sysName = record.system_info?.sys_name
        const mismatch = isSnmpNameMismatch(record)
        return (
          <Space direction="vertical" size={2}>
            <Tooltip title={enteredName}><Text strong type={mismatch ? 'danger' : undefined} ellipsis style={{ maxWidth: (columnWidths.device || DEFAULT_COLUMN_WIDTHS.device) - 28 }}>{enteredName}</Text></Tooltip>
            <Text type="secondary">{record.device.ip_address}</Text>
            {mismatch ? (
              <Space size={6} wrap>
                <Tooltip title={`录入：${enteredName}；采集：${sysName}`}>
                  <Tag color="red">采集名称：{sysName}</Tag>
                </Tooltip>
                <Button
                  size="small"
                  type="link"
                  loading={syncingField === `${record.device.id}:name`}
                  onClick={() => handleSyncDeviceField(record, 'name', sysName)}
                >
                  同步为采集名称
                </Button>
              </Space>
            ) : null}
          </Space>
        )
      },
    },
    {
      title: '厂商/型号',
      key: 'vendor',
      width: columnWidths.vendor,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => String(a.device.model || '').localeCompare(String(b.device.model || '')),
      render: (_: any, record: DeviceOverviewItem) => {
        const snmpModel = formatCollectedModel(record.system_info?.snmp_model)
        const enteredModel = record.device.model || '-'
        const mismatch = isSnmpModelMismatch(record)
        return (
          <Space direction="vertical" size={2}>
            <Text ellipsis style={{ maxWidth: (columnWidths.vendor || DEFAULT_COLUMN_WIDTHS.vendor) - 24 }}>{record.device.vendor || '-'}</Text>
            <Tooltip title={enteredModel}><Text type={mismatch ? 'danger' : 'secondary'} ellipsis style={{ maxWidth: (columnWidths.vendor || DEFAULT_COLUMN_WIDTHS.vendor) - 24 }}>{enteredModel}</Text></Tooltip>
            {mismatch ? (
              <Space size={6} wrap>
                <Tooltip title={`录入型号：${enteredModel}；采集型号：${snmpModel}`}>
                  <Tag color="red">采集型号：{snmpModel}</Tag>
                </Tooltip>
                <Button
                  size="small"
                  type="link"
                  loading={syncingField === `${record.device.id}:model`}
                  onClick={() => handleSyncDeviceField(record, 'model', snmpModel)}
                >
                  同步为采集型号
                </Button>
              </Space>
            ) : null}
          </Space>
        )
      },
    },
    {
      title: '所属机房',
      key: 'datacenter',
      width: columnWidths.datacenter,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) =>
        String(a.device.datacenter?.name || '').localeCompare(String(b.device.datacenter?.name || ''), 'zh-CN'),
      render: (_: any, record: DeviceOverviewItem) => (
        <DatacenterBadge
          name={record.device.datacenter?.name}
          code={record.device.datacenter?.code}
        />
      ),
    },
    {
      title: '运行时间',
      key: 'uptime',
      width: columnWidths.uptime,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) =>
        (a.system_info?.uptime_seconds || -1) - (b.system_info?.uptime_seconds || -1),
      render: (_: any, record: DeviceOverviewItem) => formatDuration(record.system_info?.uptime_seconds),
    },
    {
      title: '软件版本',
      key: 'software_version',
      width: columnWidths.software_version,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) =>
        formatSoftwareVersion(a.system_info?.software_version).localeCompare(formatSoftwareVersion(b.system_info?.software_version)),
      render: (_: any, record: DeviceOverviewItem) => {
        const version = formatSoftwareVersion(record.system_info?.software_version)
        if (!version) return <Text type="secondary">-</Text>
        return (
          <Tooltip title={version}>
            <Text style={{ maxWidth: 180 }} ellipsis>{version}</Text>
          </Tooltip>
        )
      },
    },
    {
      title: '连通性',
      key: 'connectivity',
      width: columnWidths.connectivity,
      filters: CONNECTIVITY_OPTIONS.filter((item) => item.value).map((item) => ({ text: item.label, value: item.value })),
      onFilter: (value: any, record: DeviceOverviewItem) => matchesConnectivityFilter(record, String(value)),
      render: (_: any, record: DeviceOverviewItem) => <ConnectivityTag item={record} />,
    },
    {
      title: 'CPU',
      key: 'cpu',
      width: columnWidths.cpu,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.cpu_percent) || -1) - (normalizePercent(b.resources.cpu_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.cpu_percent} source={record.data_sources?.resources?.cpu_percent} />,
    },
    {
      title: '内存',
      key: 'memory',
      width: columnWidths.memory,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.memory_percent) || -1) - (normalizePercent(b.resources.memory_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.memory_percent} source={record.data_sources?.resources?.memory_percent} />,
    },
    {
      title: '温度',
      key: 'temperature',
      width: columnWidths.temperature,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (a.resources.temperature || -1) - (b.resources.temperature || -1),
      render: (_: any, record: DeviceOverviewItem) =>
        record.resources.temperature === undefined || record.resources.temperature === null
          ? <Text type="secondary">-</Text>
          : (
            <Tooltip title={temperatureTooltip(record)}>
              <Text>{Number(record.resources.temperature).toFixed(1)}℃</Text>
            </Tooltip>
          ),
    },
    {
      title: '存储',
      key: 'storage',
      width: columnWidths.storage,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.storage_percent) || -1) - (normalizePercent(b.resources.storage_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.storage_percent} />,
    },
    {
      title: '风扇',
      key: 'fan',
      width: columnWidths.fan,
      render: (_: any, record: DeviceOverviewItem) => (
        <HardwareCell
          label="正常"
          total={record.hardware?.fan_total}
          down={record.hardware?.fan_down}
          statusKnown={record.hardware?.fan_status_known}
        />
      ),
    },
    {
      title: '电源',
      key: 'power',
      width: columnWidths.power,
      render: (_: any, record: DeviceOverviewItem) => (
        <HardwareCell
          label="正常"
          total={record.hardware?.power_total}
          down={record.hardware?.power_down}
          statusKnown={record.hardware?.power_status_known}
        />
      ),
    },
    {
      title: 'BGP',
      key: 'bgp',
      width: columnWidths.bgp,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => a.protocols.bgp.down - b.protocols.bgp.down,
      render: (_: any, record: DeviceOverviewItem) => <ProtocolCell data={record.protocols.bgp} />,
    },
    {
      title: 'OSPF',
      key: 'ospf',
      width: columnWidths.ospf,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => a.protocols.ospf.down - b.protocols.ospf.down,
      render: (_: any, record: DeviceOverviewItem) => <ProtocolCell data={record.protocols.ospf} />,
    },
    {
      title: '更新时间',
      key: 'updated_at',
      width: columnWidths.updated_at,
      render: (_: any, record: DeviceOverviewItem) => dayjs(record.collected_at).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'detail',
      width: columnWidths.detail,
      fixed: 'right',
      render: (_: any, record: DeviceOverviewItem) => (
        <Space size={8}>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(record)}>
            查看
          </Button>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={refreshingDeviceId === record.device.id}
            onClick={() => handleDeviceRefresh(record)}
          >
            采集
          </Button>
        </Space>
      ),
    },
  ]

  const columnMap = new Map(allColumns.map((column) => [String(column.key), column]))
  const withHeaderDrag = (column: any) => {
    const key = String(column?.key || '')
    const draggable = visibleColumnKeys.includes(key)
    if (!draggable) return column
    return {
      ...column,
      title: (
        <Tooltip title="拖动表头可调整顺序；拖动右侧边缘可调整宽度">
          <span style={{ cursor: 'grab', userSelect: 'none', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {column.title}
            <span style={{ color: '#1677ff', fontSize: 12, opacity: draggingColumnKey === key ? 1 : 0.45 }}>↔</span>
          </span>
        </Tooltip>
      ),
      onHeaderCell: () => ({
        draggable: true,
        onResizeStart: handleResizeStart(key),
        onDragStart: (event: DragEvent<HTMLElement>) => {
          setDraggingColumnKey(key)
          setDragOverColumnKey(null)
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData('text/plain', key)
        },
        onDragEnter: (event: DragEvent<HTMLElement>) => {
          if (draggingColumnKey && draggingColumnKey !== key) {
            event.preventDefault()
            setDragOverColumnKey(key)
          }
        },
        onDragOver: (event: DragEvent<HTMLElement>) => {
          if (draggingColumnKey && draggingColumnKey !== key) {
            event.preventDefault()
            event.dataTransfer.dropEffect = 'move'
            setDragOverColumnKey(key)
          }
        },
        onDrop: (event: DragEvent<HTMLElement>) => {
          event.preventDefault()
          const sourceKey = draggingColumnKey || event.dataTransfer.getData('text/plain')
          if (sourceKey) moveVisibleColumn(sourceKey, key)
          setDraggingColumnKey(null)
          setDragOverColumnKey(null)
        },
        onDragEnd: () => {
          setDraggingColumnKey(null)
          setDragOverColumnKey(null)
        },
        style: {
          cursor: 'grab',
          background: draggingColumnKey === key
            ? '#d6e4ff'
            : dragOverColumnKey === key
              ? '#e6f4ff'
              : undefined,
          outline: dragOverColumnKey === key ? '2px dashed #1677ff' : undefined,
          outlineOffset: '-4px',
          boxShadow: dragOverColumnKey === key ? 'inset 4px 0 0 #1677ff, 0 0 0 999px rgba(22,119,255,0.04) inset' : undefined,
          transition: 'background 0.18s ease, box-shadow 0.18s ease, outline-color 0.18s ease',
        },
      }),
    }
  }
  const visibleColumns = [
    columnMap.get('device'),
    ...visibleColumnKeys.map((key) => columnMap.get(key)).filter(Boolean),
    columnMap.get('detail'),
  ].filter(Boolean).map(withHeaderDrag)

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card bodyStyle={{ padding: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <Input
            allowClear
            placeholder="搜索设备名称/IP/厂商/型号"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            style={{ flex: '1 1 300px', minWidth: 260, maxWidth: 460 }}
          />
          <Select style={{ width: 180 }} options={VENDOR_OPTIONS} value={vendor} onChange={setVendor} />
          <Select style={{ width: 180 }} options={datacenterOptions} value={datacenter} onChange={setDatacenter} />
          <Input allowClear placeholder="筛选型号" value={model} onChange={(event) => setModel(event.target.value)} style={{ width: 220 }} />
          <Select style={{ width: 180 }} options={CONNECTIVITY_OPTIONS} value={connectivity} onChange={setConnectivity} />
          <Select style={{ width: 190 }} options={SORT_OPTIONS} value={sortKey} onChange={setSortKey} />
          <Select style={{ width: 150 }} options={REFRESH_OPTIONS} value={refreshIntervalSeconds} onChange={setRefreshIntervalSeconds} />
          <Space size={10} wrap style={{ marginLeft: 'auto' }}>
            {loading ? (
              <Space size={4} style={{ color: '#1677ff', fontSize: 12 }}>
                <ReloadOutlined spin />
              </Space>
            ) : null}
            <Dropdown
              trigger={['click']}
              dropdownRender={() => (
                <Card size="small" bodyStyle={{ padding: 10, width: 260 }}>
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      勾选显示列；拖动已显示列可调整左右顺序
                    </Text>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {orderedColumnOptions.map((item) => {
                        const checked = visibleColumnKeys.includes(item.value)
                        return (
                          <div
                            key={item.value}
                            draggable={checked}
                            onDragStart={() => {
                              if (checked) setDraggingColumnKey(item.value)
                            }}
                            onDragOver={(event) => {
                              if (checked && draggingColumnKey) event.preventDefault()
                            }}
                            onDrop={(event) => {
                              event.preventDefault()
                              if (checked && draggingColumnKey) {
                                moveVisibleColumn(draggingColumnKey, item.value)
                              }
                              setDraggingColumnKey(null)
                            }}
                            onDragEnd={() => setDraggingColumnKey(null)}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              gap: 8,
                              padding: '7px 9px',
                              borderRadius: 8,
                              border: checked ? '1px solid #d6e4ff' : '1px solid #f0f0f0',
                              background: draggingColumnKey === item.value ? '#e6f4ff' : checked ? '#f8fbff' : '#fafafa',
                              cursor: checked ? 'grab' : 'default',
                              opacity: checked ? 1 : 0.65,
                            }}
                          >
                            <Checkbox
                              checked={checked}
                              onChange={(event) => toggleColumnVisible(item.value, event.target.checked)}
                            >
                              {columnLabelMap.get(item.value) || item.label}
                            </Checkbox>
                            {checked ? <Text type="secondary" style={{ fontSize: 12 }}>拖动</Text> : null}
                          </div>
                        )
                      })}
                    </div>
                  </Space>
                </Card>
              )}
            >
              <Button>列显示/排序</Button>
            </Dropdown>
            <Button icon={<ReloadOutlined />} onClick={() => loadData()} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>
        <Space wrap style={{ marginTop: 12 }}>
          <Tag color="blue">设备 {stats.total}</Tag>
          <Tag color="green">连通 {stats.reachable}</Tag>
          <Tooltip title="BGP 邻居状态不是 established 的总数">
            <Tag color={stats.bgpDown > 0 ? 'red' : 'default'}>BGP 异常 {stats.bgpDown}</Tag>
          </Tooltip>
          <Tooltip title="OSPF 邻居状态不是 full 的总数">
            <Tag color={stats.ospfDown > 0 ? 'red' : 'default'}>OSPF 异常 {stats.ospfDown}</Tag>
          </Tooltip>
        </Space>
        {loading ? (
          <div style={{ marginTop: 12 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {getProgressHint(overviewProgress)}
            </Text>
            <Progress percent={overviewProgress} size="small" status="active" showInfo={false} style={{ marginTop: 6 }} />
          </div>
        ) : null}
      </Card>

      <Card bodyStyle={{ padding: 0 }}>
        <Table<DeviceOverviewItem>
          components={{
            header: { cell: ResizableHeaderCell },
          } as any}
          rowKey={(record) => record.device.id}
          loading={loading}
          dataSource={sortedItems}
          tableLayout="fixed"
          scroll={{ x: 1700, y: 'calc(100vh - 360px)' }}
          pagination={{
            pageSize: tablePageSize,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
            onShowSizeChange: (_current, size) => setTablePageSize(size),
            onChange: (_page, size) => setTablePageSize(size),
          }}
          columns={visibleColumns}
        />
      </Card>

      <Drawer
        title={detailDevice ? `${detailDevice.device.name || detailDevice.device.ip_address} 协议邻居` : '协议邻居'}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={1450}
      >
        {detailLoading ? (
          <div style={{ marginBottom: 12 }}>
            <Text type="secondary">{getProgressHint(detailProgress)}</Text>
            <Progress percent={detailProgress} size="small" status="active" showInfo={false} />
          </div>
        ) : null}
        <Tabs
          items={[
            {
              key: 'bgp',
              label: `BGP (${neighbors.bgp.length})`,
              children: (
                <Table<ProtocolNeighbor>
                  rowKey={(record, index) => `${record.protocol}-${record.peer}-${index}`}
                  loading={detailLoading}
                  dataSource={neighbors.bgp}
                  columns={neighborColumns}
                  scroll={{ x: 1200 }}
                  pagination={false}
                  tableLayout="fixed"
                  size="small"
                />
              ),
            },
            {
              key: 'ospf',
              label: `OSPF (${neighbors.ospf.length})`,
              children: (
                <Table<ProtocolNeighbor>
                  rowKey={(record, index) => `${record.protocol}-${record.peer}-${index}`}
                  loading={detailLoading}
                  dataSource={neighbors.ospf}
                  columns={ospfNeighborColumns}
                  scroll={{ x: 720 }}
                  pagination={false}
                  tableLayout="fixed"
                  size="small"
                />
              ),
            },
            {
              key: 'lldp',
              label: `LLDP (${neighbors.lldp.length})`,
              children: (
                <Table<ProtocolNeighbor>
                  rowKey={(record, index) => `lldp-${record.local_port || record.local_port_id}-${record.remote_system || record.remote_chassis_id}-${record.remote_port || record.remote_port_id}-${index}`}
                  loading={detailLoading}
                  dataSource={neighbors.lldp}
                  columns={lldpColumns}
                  scroll={{ x: 1120 }}
                  pagination={{
                    pageSize: lldpPageSize,
                    showSizeChanger: true,
                    pageSizeOptions: ['20', '50', '100', '200'],
                    onShowSizeChange: (_current, size) => setLldpPageSize(size),
                    onChange: (_page, size) => setLldpPageSize(size),
                  }}
                  tableLayout="fixed"
                  size="small"
                />
              ),
            },
          ]}
        />
      </Drawer>
    </Space>
  )
}

export default DeviceOverview
