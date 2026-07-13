import { Suspense, lazy, type MouseEvent as ReactMouseEvent, type UIEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Col, DatePicker, Descriptions, Empty, Input, Progress, Row, Select, Space, Spin, Table, Tabs, Tag, Typography, message } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, EditOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getDevice,
  getDeviceConfigBackups,
  getDeviceConnections,
  getDeviceHardware,
  getDevicePerformance,
  getDeviceSyslog,
  getDeviceTacacs,
  type Device,
  type DeviceConfigBackupRow,
  type DeviceConnectionRow,
  type DeviceHardwareRow,
  type DeviceLogRow,
  type DeviceTacacsRow,
} from '../../api/devices'
import { getConfigBackupResult } from '../../api/configBackups'
import { getMonitorInterfaceHistory, type MonitorHistoryPoint } from '../../api/metrics'
import { useAuthStore } from '../../store/auth'

const { RangePicker } = DatePicker
const { Text } = Typography

const InterfaceTrafficChart = lazy(() => import('./DeviceDetailCharts').then((module) => ({ default: module.InterfaceTrafficChart })))
const MetricTrendChart = lazy(() => import('./DeviceDetailCharts').then((module) => ({ default: module.MetricTrendChart })))
const InterfaceDiscardChart = lazy(() => import('./DeviceDetailCharts').then((module) => ({ default: module.InterfaceDiscardChart })))

const formatDateTimeText = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  if (dayjs(text).isValid()) return dayjs(text).format('YYYY-MM-DD HH:mm:ss')
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})/)
  if (match) return `${match[1]} ${match[2]}`
  return text.replace('T', ' ').replace(/\.\d+.*$/, '').replace(/(?:Z|[+-]\d{2}:?\d{2})$/, '') || '-'
}

const statusMap: Record<string, { color: string; label: string }> = {
  active: { color: 'success', label: '上线' },
  inactive: { color: 'default', label: '离线' },
  in_stock: { color: 'default', label: '库存' },
  deployed: { color: 'processing', label: '上架' },
  online: { color: 'success', label: '上线' },
  offline: { color: 'default', label: '离线' },
}

const formatBps = (value?: number | null) => {
  const num = Number(value || 0)
  if (!num) return '0 bps'
  if (num >= 1e12) return `${(num / 1e12).toFixed(2)} Tbps`
  if (num >= 1e9) return `${(num / 1e9).toFixed(2)} Gbps`
  if (num >= 1e6) return `${(num / 1e6).toFixed(2)} Mbps`
  if (num >= 1e3) return `${(num / 1e3).toFixed(2)} Kbps`
  return `${num.toFixed(0)} bps`
}

const textSorter = <T extends Record<string, any>>(field: keyof T) => (a: T, b: T) =>
  String(a[field] ?? '').localeCompare(String(b[field] ?? ''), 'zh-Hans-CN', { numeric: true })

const numberSorter = <T extends Record<string, any>>(field: keyof T) => (a: T, b: T) =>
  Number(a[field] ?? 0) - Number(b[field] ?? 0)

const performanceMeta = (name?: string) => {
  const normalized = String(name || '').toLowerCase()
  if (normalized === 'cpu') return { title: 'CPU使用率', unit: '%' }
  if (normalized === 'memory') return { title: '内存使用率', unit: '%' }
  if (normalized.includes('temp') || normalized.includes('temperature') || normalized.includes('温度')) return { title: '温度', unit: '℃' }
  return { title: name || '性能指标', unit: '' }
}

const formatMetricValue = (value: any, unit = '') => {
  const num = Number(value || 0)
  const text = Number.isInteger(num) ? String(num) : num.toFixed(2)
  return `${text}${unit}`
}

const normalizeChartTime = (value?: string) => {
  if (!value) return ''
  return dayjs(value).isValid() ? dayjs(value).format('MM-DD HH:mm') : String(value)
}

const historyRangeOptions = [
  { label: '最近15分钟', value: '-15m' },
  { label: '最近1小时', value: '-1h' },
  { label: '最近6小时', value: '-6h' },
  { label: '最近24小时', value: '-24h' },
  { label: '最近7天', value: '-7d' },
  { label: '自定义时间', value: 'custom' },
]

const trafficIntervalOptions = [
  { label: '30秒粒度', value: '30s' },
  { label: '1分钟粒度', value: '1m' },
  { label: '2分钟粒度', value: '2m' },
  { label: '5分钟粒度', value: '5m' },
  { label: '15分钟粒度', value: '15m' },
]

const performanceIntervalOptions = [
  { label: '1分钟粒度', value: '1m' },
  { label: '5分钟粒度', value: '5m' },
  { label: '15分钟粒度', value: '15m' },
  { label: '1小时粒度', value: '1h' },
]

const buildHistoryParams = (
  rangeValue: string,
  interval: string,
  customRange: [dayjs.Dayjs, dayjs.Dayjs] | null,
) => {
  if (rangeValue === 'custom' && customRange?.[0] && customRange?.[1]) {
    return {
      range: '-24h',
      interval,
      start_ts: customRange[0].valueOf(),
      end_ts: customRange[1].valueOf(),
    }
  }
  return { range: rangeValue === 'custom' ? '-24h' : rangeValue, interval }
}

const historyParamsKey = (params: Record<string, any>) => JSON.stringify(params)

const normalizeInterfaceKey = (value?: string | null) => {
  let text = String(value || '').trim().toLowerCase().replace(/\s+/g, '')
  const replacements: Record<string, string> = {
    fourhundredgigabitethernet: '400g',
    fourhundredgige: '400g',
    fourhundredge: '400g',
    'fourhundred-gigabitethernet': '400g',
    '400ge': '400g',
    hundredgigabitethernet: 'hge',
    hundredgige: 'hge',
    hundredge: 'hge',
    'hundred-gigabitethernet': 'hge',
    '100ge': 'hge',
    'ten-gigabitethernet': 'tengige',
    tengigabitethernet: 'tengige',
    tengige: 'tengige',
    xgigabitethernet: 'tengige',
    xge: 'tengige',
    te: 'tengige',
  }
  Object.entries(replacements).forEach(([from, to]) => {
    text = text.split(from).join(to)
  })
  return text.replace(/[^a-z0-9/._-]/g, '')
}

const shortInterfaceName = (value?: string | null) => {
  const text = String(value || '').trim()
  return text
    .replace(/^FourHundredGigabitEthernet/i, '400GE')
    .replace(/^FourHundredGigE/i, '400GE')
    .replace(/^FourHundredGE/i, '400GE')
    .replace(/^HundredGigabitEthernet/i, '100GE')
    .replace(/^HundredGigE/i, '100GE')
    .replace(/^HundredGE/i, '100GE')
    .replace(/^Ten-GigabitEthernet/i, '10GE')
    .replace(/^TenGigabitEthernet/i, '10GE')
    .replace(/^XGigabitEthernet/i, '10GE')
}

const buildInterfaceTitle = (item: DeviceConnectionRow) => {
  const name = shortInterfaceName(item.name || item.description || '-')
  const desc = String(item.description || '').trim()
  if (!desc || normalizeInterfaceKey(desc) === normalizeInterfaceKey(item.name)) return name
  return `${name} / ${desc}`
}

const cleanDisplayDescription = (item: DeviceConnectionRow) => {
  const desc = String(item.description || '').trim()
  if (!desc) return ''
  const withoutSuffix = desc.replace(/\s+Interface$/i, '').trim()
  if (normalizeInterfaceKey(desc) === normalizeInterfaceKey(item.name)) return ''
  if (withoutSuffix !== desc && normalizeInterfaceKey(withoutSuffix) === normalizeInterfaceKey(item.name)) return ''
  return desc
}

const isPhysicalInterfaceName = (value?: string | null) => {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return false
  if (/^(bridge-aggregation|route-aggregation|vlan|vsi|loop|inloopback|null|register-tunnel|tun|tunnel)/i.test(text)) return false
  return /(fourhundred|fhgigabit|400ge|hundred|100ge|ten-gigabit|tengigabit|10ge|xgigabit|gigabitethernet|mgigabit|m-gigabit)/i.test(text)
}

const isAsterNOSVendor = (vendor?: string) => {
  const value = (vendor || '').toLowerCase()
  return value.includes('asternos') || value.includes('asterfusion') || value.includes('asteros') || value.includes('星融元')
}

const statusTag = (value?: string) => {
  const normalized = String(value || '').toLowerCase()
  const color = normalized === 'up' || normalized === '1' || normalized === 'normal' ? 'success' : normalized === 'down' || normalized === '0' ? 'error' : 'default'
  return <Tag color={color}>{value || '-'}</Tag>
}

const hardwareStatusTag = (row: DeviceHardwareRow) => {
  const known = row.status_known !== undefined && row.status_known !== null ? String(row.status_known) !== '0' : row.up !== undefined && row.up !== null
  if (!known) return <Tag color="default">未知</Tag>
  return statusTag(String(row.up) === '1' ? 'normal' : 'down')
}

const formatOptionalNumber = (value?: number | string | null, unit = '') => {
  if (value === undefined || value === null || value === '') return '-'
  const num = Number(value)
  if (!Number.isFinite(num)) return String(value)
  const text = Number.isInteger(num) ? String(num) : num.toFixed(2)
  return `${text}${unit}`
}

type LoadingStage = { percent: number; text: string }

const useStagedProgress = (active: boolean, stages: LoadingStage[]) => {
  const [percent, setPercent] = useState(stages[0]?.percent || 8)

  useEffect(() => {
    if (!active) {
      setPercent(100)
      return undefined
    }
    setPercent(stages[0]?.percent || 8)
    const timer = window.setInterval(() => {
      setPercent((prev) => {
        const nextStage = stages.find((stage) => stage.percent > prev)
        if (!nextStage) return Math.min(prev + 1, 96)
        const step = nextStage.percent - prev > 20 ? 4 : nextStage.percent - prev > 8 ? 2 : 1
        return Math.min(prev + step, nextStage.percent, 96)
      })
    }, 650)
    return () => window.clearInterval(timer)
  }, [active, stages])

  const stage = [...stages].reverse().find((item) => percent >= item.percent) || stages[0]
  return { percent: active ? Math.min(percent, 96) : 100, stageText: stage?.text || '正在加载...' }
}

const LoadingProgressCard = ({
  active,
  stages,
  title,
  description,
  embedded = false,
}: {
  active: boolean
  stages: LoadingStage[]
  title: string
  description?: string
  embedded?: boolean
}) => {
  const { percent, stageText } = useStagedProgress(active, stages)
  return (
    <Card size={embedded ? 'small' : 'default'} style={embedded ? { background: '#fafafa' } : { maxWidth: 760, margin: '72px auto' }}>
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space>
          <Spin size="small" />
          <Text strong>{title}</Text>
        </Space>
        <Progress percent={percent} status="active" strokeColor={{ from: '#1677ff', to: '#52c41a' }} />
        <Text>{stageText}</Text>
        {description ? <Text type="secondary">{description}</Text> : null}
      </Space>
    </Card>
  )
}

const DEVICE_DETAIL_LOADING_STAGES: LoadingStage[] = [
  { percent: 10, text: '正在加载设备详情页面...' },
  { percent: 28, text: '正在读取设备基础信息...' },
  { percent: 52, text: '正在加载厂商、机房、监控方式等关联信息...' },
  { percent: 76, text: '正在准备连接、流量、Syslog、配置、硬件等子模块...' },
  { percent: 92, text: '即将进入设备详情...' },
]

const CONNECTION_LOADING_STAGES: LoadingStage[] = [
  { percent: 8, text: '正在读取接口快照缓存...' },
  { percent: 28, text: '如果缓存缺失，会尝试通过 SNMP / Exporter 实时补采接口信息...' },
  { percent: 50, text: '正在合并 LLDP 邻居与对端设备信息...' },
  { percent: 70, text: '正在解析最近一次配置备份中的描述、IP、MTU、VLAN 信息...' },
  { percent: 88, text: '正在整理接口列表并准备展示...' },
]

const connectionSourceText = (source?: string) => {
  const map: Record<string, string> = {
    cache: '数据来源：后台接口快照缓存',
    snmp_live: '数据来源：缓存缺失，本次通过 SNMP 实时补采',
    exporter_live: '数据来源：缓存缺失，本次通过 Exporter 实时读取',
    cache_miss: '数据来源：暂无可用接口快照，且实时补采未返回有效数据',
    exporter_miss: '数据来源：Exporter 暂无可用接口数据',
  }
  return source ? map[source] || `数据来源：${source}` : ''
}

const CONNECTION_COLUMN_DEFAULT_WIDTHS = {
  name: 210,
  description: 340,
  speed_bps: 110,
  mtu: 70,
  ip_address: 150,
  oper_status: 84,
  admin_status: 104,
  remote_device: 330,
  remote_interface: 150,
  remote_management_ip: 126,
}

type ConnectionColumnKey = keyof typeof CONNECTION_COLUMN_DEFAULT_WIDTHS
type ConnectionColumnWidths = Record<ConnectionColumnKey, number>

const CONNECTION_COLUMN_MIN_WIDTHS: Record<ConnectionColumnKey, number> = {
  name: 140,
  description: 160,
  speed_bps: 90,
  mtu: 60,
  ip_address: 120,
  oper_status: 76,
  admin_status: 92,
  remote_device: 180,
  remote_interface: 110,
  remote_management_ip: 120,
}

const CONNECTION_COLUMN_STORAGE_KEY = 'device-detail-connection-column-widths-v1'

const loadConnectionColumnWidths = (): ConnectionColumnWidths => {
  if (typeof window === 'undefined') return CONNECTION_COLUMN_DEFAULT_WIDTHS
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CONNECTION_COLUMN_STORAGE_KEY) || '{}')
    return { ...CONNECTION_COLUMN_DEFAULT_WIDTHS, ...parsed }
  } catch {
    return CONNECTION_COLUMN_DEFAULT_WIDTHS
  }
}

const DeviceDetail = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const deviceId = Number(id)
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('connections')

  useEffect(() => {
    fetchDevice()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const fetchDevice = async () => {
    setLoading(true)
    try {
      const data = await getDevice(deviceId)
      setDevice(data)
    } catch (error) {
      message.error('获取设备失败')
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <LoadingProgressCard
        active={loading}
        stages={DEVICE_DETAIL_LOADING_STAGES}
        title="正在打开设备详情"
        description="这一步主要读取设备基础资料；连接、流量、硬件等明细会在进入页面后按需加载。"
      />
    )
  }
  if (!device) return <div>设备不存在</div>

  const statusConfig = statusMap[device.status] || { color: 'default', label: device.status }
  const monitorSourceLabel = isAsterNOSVendor(device.vendor)
    ? 'AsterNOS Exporter'
    : device.gnmi?.enabled
      ? 'SNMP / Telemetry'
      : device.is_monitored ? 'SNMP' : '-'

  return (
    <Card
      title={
        <Space>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/devices')} />
          <span>{device.name}</span>
          <Tag color={statusConfig.color}>{statusConfig.label}</Tag>
        </Space>
      }
      extra={canModify ? (
        <Button type="primary" icon={<EditOutlined />} onClick={() => navigate(`/devices/edit/${device.id}`)}>编辑</Button>
      ) : null}
      styles={{ body: { paddingTop: 12 } }}
    >
      <Descriptions bordered size="small" column={4} labelStyle={{ width: 110, color: '#666' }}>
        <Descriptions.Item label="设备名称">{device.name}</Descriptions.Item>
        <Descriptions.Item label="管理IP">{device.ip_address}</Descriptions.Item>
        <Descriptions.Item label="厂商/型号">{[device.vendor, device.model].filter(Boolean).join(' / ') || '-'}</Descriptions.Item>
        <Descriptions.Item label="序列号">{device.serial_number || '-'}</Descriptions.Item>
        <Descriptions.Item label="所属机房">{device.datacenter ? `${device.datacenter.name}${device.datacenter.code ? ` (${device.datacenter.code})` : ''}` : '-'}</Descriptions.Item>
        <Descriptions.Item label="设备类型">{device.device_type || '-'}</Descriptions.Item>
        <Descriptions.Item label="设备角色">{device.device_role || '-'}</Descriptions.Item>
        <Descriptions.Item label="监控方式">{monitorSourceLabel}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{formatDateTimeText(device.created_at)}</Descriptions.Item>
        <Descriptions.Item label="机房位置">{device.datacenter?.location || '-'}</Descriptions.Item>
        <Descriptions.Item label="网络负责人">{device.datacenter?.network_owner || '-'}</Descriptions.Item>
        <Descriptions.Item label="负责人邮箱">{device.datacenter?.network_owner_email || device.datacenter?.contact_email || '-'}</Descriptions.Item>
      </Descriptions>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        style={{ marginTop: 16 }}
        items={[
          { key: 'connections', label: '连接', children: <ConnectionsTab deviceId={device.id} /> },
          { key: 'traffic', label: '流量', children: <TrafficTab deviceId={device.id} /> },
          { key: 'syslog', label: 'Syslog', children: <SyslogTab deviceId={device.id} /> },
          { key: 'config', label: '配置', children: <ConfigTab deviceId={device.id} /> },
          { key: 'performance', label: '性能', children: <PerformanceTab deviceId={device.id} /> },
          { key: 'hardware', label: '硬件', children: <HardwareTab deviceId={device.id} /> },
          { key: 'tacacs', label: 'Tacacs', children: <TacacsTab deviceId={device.id} /> },
        ]}
      />
    </Card>
  )
}

const ConnectionsTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceConnectionRow[]>([])
  const [keyword, setKeyword] = useState('')
  const [notice, setNotice] = useState('')
  const [source, setSource] = useState('')
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [columnWidths, setColumnWidths] = useState<ConnectionColumnWidths>(loadConnectionColumnWidths)

  const fetchRows = async (forceRefresh = false) => {
    setLoading(true)
    try {
      const result = await getDeviceConnections(deviceId, forceRefresh ? { force_refresh: true } : undefined)
      setRows((result.items || []).map((item) => ({ ...item, description: cleanDisplayDescription(item) })))
      setNotice(result.message || '')
      setSource(result.source || '')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取连接信息失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchRows() }, [deviceId])

  useEffect(() => {
    window.localStorage.setItem(CONNECTION_COLUMN_STORAGE_KEY, JSON.stringify(columnWidths))
  }, [columnWidths])

  const resizeColumn = (key: ConnectionColumnKey) => (event: ReactMouseEvent<HTMLSpanElement>) => {
    event.preventDefault()
    event.stopPropagation()
    const startX = event.clientX
    const startWidth = columnWidths[key]
    const minWidth = CONNECTION_COLUMN_MIN_WIDTHS[key]
    const handleMouseMove = (moveEvent: MouseEvent) => {
      const nextWidth = Math.max(minWidth, startWidth + moveEvent.clientX - startX)
      setColumnWidths((prev) => ({ ...prev, [key]: nextWidth }))
    }
    const handleMouseUp = () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  const columnTitle = (key: ConnectionColumnKey, title: string) => (
    <span style={{ position: 'relative', display: 'block', width: '100%', minWidth: 0, paddingRight: 18 }}>
      <span>{title}</span>
      <span
        title="拖动调整列宽"
        onMouseDown={resizeColumn(key)}
        onClick={(event) => event.stopPropagation()}
        style={{
          position: 'absolute',
          right: 0,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 18,
          height: 30,
          cursor: 'col-resize',
          zIndex: 20,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(255,255,255,0.01)',
        }}
      >
        <span style={{ width: 2, height: 24, borderRadius: 2, background: '#8c8c8c', boxShadow: '0 0 0 1px rgba(255,255,255,0.65)' }} />
      </span>
    </span>
  )

  const shouldShowLogicalType = (value?: string) => {
    const normalized = String(value || '').trim().toLowerCase()
    return Boolean(normalized && normalized !== 'ethernet')
  }

  const tableScrollX = useMemo(() => (Object.values(columnWidths) as number[]).reduce((sum, width) => sum + Number(width || 0), 0) + 40, [columnWidths])

  const filtered = useMemo(() => {
    const key = keyword.trim().toLowerCase()
    if (!key) return rows
    return rows.filter((item) => Object.values(item).some((value) => String(value ?? '').toLowerCase().includes(key)))
  }, [rows, keyword])

  useEffect(() => {
    setPagination((prev) => ({ ...prev, current: 1 }))
  }, [deviceId, keyword])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Space>
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索接口/描述/对端" value={keyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 260 }} />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchRows(true)}>手动刷新</Button>
        <Text type="secondary">拖动表头右侧边缘可调整列宽</Text>
      </Space>
      {loading ? (
        <LoadingProgressCard
          active={loading}
          stages={CONNECTION_LOADING_STAGES}
          title="正在加载连接信息"
          description="连接信息会把接口状态、LLDP 邻居和配置备份解析结果合并，所以缓存缺失或设备响应慢时会多等一会儿。"
          embedded
        />
      ) : null}
      {!loading && source ? <Alert type="success" showIcon message={connectionSourceText(source)} /> : null}
      {notice ? <Alert type="info" showIcon message={notice} /> : null}
      <Table<DeviceConnectionRow>
        loading={loading}
        rowKey={(row) => row.index || row.name}
        dataSource={filtered}
        size="small"
        scroll={{ x: tableScrollX }}
        pagination={{
          current: pagination.current,
          pageSize: pagination.pageSize,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          showTotal: (total) => `共 ${total} 条`,
          onChange: (current, pageSize) => setPagination({ current, pageSize }),
          onShowSizeChange: (_, pageSize) => setPagination({ current: 1, pageSize }),
        }}
        columns={[
          {
            title: columnTitle('name', '本地接口名称'),
            dataIndex: 'name',
            width: columnWidths.name,
            fixed: 'left',
            sorter: textSorter<DeviceConnectionRow>('name'),
            render: (value, row) => (
              <Space direction="vertical" size={0}>
                <span>{value || '-'}</span>
                {shouldShowLogicalType(row.logical_type) ? <span style={{ color: '#8c8c8c', fontSize: 12 }}>{row.logical_type}</span> : null}
              </Space>
            ),
          },
          { title: columnTitle('description', '描述'), dataIndex: 'description', width: columnWidths.description, ellipsis: true, sorter: textSorter<DeviceConnectionRow>('description') },
          { title: columnTitle('speed_bps', '速率'), dataIndex: 'speed_bps', width: columnWidths.speed_bps, render: formatBps, sorter: numberSorter<DeviceConnectionRow>('speed_bps') },
          { title: columnTitle('mtu', 'MTU'), dataIndex: 'mtu', width: columnWidths.mtu, sorter: numberSorter<DeviceConnectionRow>('mtu') },
          {
            title: columnTitle('ip_address', '接口IP'),
            dataIndex: 'ip_address',
            width: columnWidths.ip_address,
            sorter: textSorter<DeviceConnectionRow>('ip_address'),
            ellipsis: true,
            render: (value) => <span title={value || ''} style={{ whiteSpace: 'nowrap' }}>{value || '-'}</span>,
          },
          { title: columnTitle('oper_status', '接口状态'), dataIndex: 'oper_status', width: columnWidths.oper_status, render: statusTag, sorter: textSorter<DeviceConnectionRow>('oper_status') },
          { title: columnTitle('admin_status', '接口管理状态'), dataIndex: 'admin_status', width: columnWidths.admin_status, render: statusTag, sorter: textSorter<DeviceConnectionRow>('admin_status') },
          { title: columnTitle('remote_device', '对端设备'), dataIndex: 'remote_device', width: columnWidths.remote_device, ellipsis: true, sorter: textSorter<DeviceConnectionRow>('remote_device') },
          { title: columnTitle('remote_interface', '对端接口'), dataIndex: 'remote_interface', width: columnWidths.remote_interface, sorter: textSorter<DeviceConnectionRow>('remote_interface') },
          { title: columnTitle('remote_management_ip', '对端管理IP'), dataIndex: 'remote_management_ip', width: columnWidths.remote_management_ip, sorter: textSorter<DeviceConnectionRow>('remote_management_ip') },
        ]}
      />
    </Space>
  )
}

const TrafficTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [interfaces, setInterfaces] = useState<DeviceConnectionRow[]>([])
  const [keyword, setKeyword] = useState('')
  const [visibleCount, setVisibleCount] = useState(6)
  const [histories, setHistories] = useState<Record<string, MonitorHistoryPoint[]>>({})
  const [rangeValue, setRangeValue] = useState('-6h')
  const [intervalValue, setIntervalValue] = useState('2m')
  const [customRange, setCustomRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [queryParams, setQueryParams] = useState(() => buildHistoryParams('-6h', '2m', null))
  const queryKey = useMemo(() => historyParamsKey(queryParams), [queryParams])
  const historyRequestSeqRef = useRef(0)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const result = await getDeviceConnections(deviceId)
        setInterfaces(result.items || [])
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [deviceId])

  const filteredInterfaces = useMemo(() => {
    const key = keyword.trim().toLowerCase()
    return interfaces
      .filter((item) => !key || String(item.name || '').toLowerCase().includes(key))
      .sort((a, b) => (String(a.oper_status).toLowerCase() === 'up' ? 0 : 1) - (String(b.oper_status).toLowerCase() === 'up' ? 0 : 1))
  }, [interfaces, keyword])

  const selected = useMemo(() => filteredInterfaces.slice(0, visibleCount), [filteredInterfaces, visibleCount])

  useEffect(() => {
    setVisibleCount(6)
  }, [deviceId, keyword])

  useEffect(() => {
    setHistories({})
    setRangeValue('-6h')
    setIntervalValue('2m')
    setCustomRange(null)
    setQueryParams(buildHistoryParams('-6h', '2m', null))
  }, [deviceId])

  const queryHistory = (
    nextRangeValue = rangeValue,
    nextIntervalValue = intervalValue,
    nextCustomRange = customRange,
  ) => {
    if (nextRangeValue === 'custom' && !nextCustomRange) {
      message.warning('请选择历史开始和结束时间')
      return
    }
    setHistories({})
    setQueryParams(buildHistoryParams(nextRangeValue, nextIntervalValue, nextCustomRange))
  }

  const loadMoreTrafficCards = () => {
    setVisibleCount((prev) => Math.min(prev + 6, filteredInterfaces.length))
  }

  const handleTrafficScroll = (event: UIEvent<HTMLDivElement>) => {
    const target = event.currentTarget
    if (target.scrollTop + target.clientHeight >= target.scrollHeight - 120 && selected.length < filteredInterfaces.length) {
      loadMoreTrafficCards()
    }
  }

  useEffect(() => {
    const requestSeq = historyRequestSeqRef.current + 1
    historyRequestSeqRef.current = requestSeq
    selected.forEach((item) => {
      const index = Number(item.index)
      const cacheKey = `${queryKey}:${item.index}`
      if (!index || histories[cacheKey]) return
      getMonitorInterfaceHistory(deviceId, index, { ...queryParams, rate_window: '5m' })
        .then((result) => {
          if (historyRequestSeqRef.current !== requestSeq) return
          setHistories((prev) => ({ ...prev, [cacheKey]: result.data || [] }))
        })
        .catch(() => {
          if (historyRequestSeqRef.current !== requestSeq) return
          setHistories((prev) => ({ ...prev, [cacheKey]: [] }))
        })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected.map((item) => item.index).join(','), deviceId, queryKey])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Space wrap>
        <Input allowClear prefix={<SearchOutlined />} placeholder="筛选接口" value={keyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 220 }} />
        <Select value={rangeValue} onChange={(value) => { setRangeValue(value); if (value !== 'custom') queryHistory(value, intervalValue, customRange) }} options={historyRangeOptions} style={{ width: 130 }} />
        {rangeValue === 'custom' ? <RangePicker showTime value={customRange as any} onChange={(value) => { const next = value as any; setCustomRange(next); if (next) queryHistory('custom', intervalValue, next) }} /> : null}
        <Select value={intervalValue} onChange={(value) => { setIntervalValue(value); queryHistory(rangeValue, value, customRange) }} options={trafficIntervalOptions} style={{ width: 120 }} />
        <Button type="primary" onClick={() => queryHistory()}>查看历史</Button>
        <Button icon={<ReloadOutlined />} onClick={() => queryHistory()}>刷新曲线</Button>
        <span style={{ color: '#8c8c8c' }}>已展示 {selected.length}/{filteredInterfaces.length} 个接口，向下滚动自动加载更多</span>
      </Space>
      <Spin spinning={loading}>
        <div onScroll={handleTrafficScroll} style={{ maxHeight: 'calc(100vh - 300px)', overflowY: 'auto', overflowX: 'hidden', paddingRight: 4 }}>
          <Row gutter={[16, 16]}>
            {selected.map((item) => {
              const cacheKey = `${queryKey}:${item.index}`
              const data = (histories[cacheKey] || []).map((point) => ({
                time: normalizeChartTime(point._time),
                in_bps: Number(point.in_bps || 0),
                out_bps: Number(point.out_bps || 0),
              }))
              const title = buildInterfaceTitle(item)
              return (
                <Col xs={24} xl={12} key={item.index}>
                  <Card
                    size="small"
                    title={<div title={title} style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>}
                    styles={{ header: { minWidth: 0 }, body: { overflow: 'hidden' } }}
                  >
                    {data.length ? (
                      <Suspense fallback={<Spin style={{ display: 'block', margin: '72px auto' }} />}>
                        <InterfaceTrafficChart data={data} formatBps={formatBps} />
                      </Suspense>
                    ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无流量数据" />}
                  </Card>
                </Col>
              )
            })}
          </Row>
          {selected.length < filteredInterfaces.length ? (
            <div style={{ textAlign: 'center', padding: '12px 0' }}>
              <Button onClick={loadMoreTrafficCards}>加载更多接口</Button>
            </div>
          ) : null}
        </div>
      </Spin>
    </Space>
  )
}

const SyslogTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceLogRow[]>([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const requestSeqRef = useRef(0)

  const fetchRows = async (nextPagination = pagination, nextRange = range, nextSearch = search) => {
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    setLoading(true)
    try {
      const result = await getDeviceSyslog(deviceId, {
        skip: (nextPagination.current - 1) * nextPagination.pageSize,
        limit: nextPagination.pageSize,
        search: nextSearch || undefined,
        start_time: nextRange?.[0]?.toISOString(),
        end_time: nextRange?.[1]?.toISOString(),
      })
      if (requestSeqRef.current !== requestSeq) return
      setRows(result.items || [])
      setTotal(result.total || 0)
    } finally {
      if (requestSeqRef.current === requestSeq) setLoading(false)
    }
  }

  const handleSearch = () => {
    const next = { ...pagination, current: 1 }
    setPagination(next)
    fetchRows(next)
  }

  useEffect(() => {
    const next = { ...pagination, current: 1 }
    setPagination(next)
    fetchRows(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId])

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space>
        <Input allowClear placeholder="关键字搜索日志正文" value={search} onChange={(event) => setSearch(event.target.value)} onPressEnter={handleSearch} style={{ width: 280 }} />
        <RangePicker showTime value={range as any} onChange={(value) => { const next = value as any; setRange(next); const page = { ...pagination, current: 1 }; setPagination(page); fetchRows(page, next, search) }} />
        <Button type="primary" onClick={handleSearch}>查询</Button>
      </Space>
      <Table<DeviceLogRow>
        loading={loading}
        rowKey={(row, index) => `${row.id || row.time}-${index}`}
        dataSource={rows}
        size="small"
        pagination={{
          total,
          current: pagination.current,
          pageSize: pagination.pageSize,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          showTotal: (value) => `共 ${value} 条`,
          onChange: (current, pageSize) => {
            const next = { current, pageSize }
            setPagination(next)
            fetchRows(next)
          },
          onShowSizeChange: (_, pageSize) => {
            const next = { current: 1, pageSize }
            setPagination(next)
            fetchRows(next)
          },
        }}
        columns={[
          { title: '时间', dataIndex: 'time', width: 180, render: formatDateTimeText },
          { title: '日志级别', dataIndex: 'severity', width: 100 },
          { title: '日志正文', dataIndex: 'message', ellipsis: true, render: (value, row) => value || row.raw_message || '-' },
        ]}
      />
    </Space>
  )
}

const ConfigTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceConfigBackupRow[]>([])
  const [total, setTotal] = useState(0)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })

  const fetchRows = async (nextPagination = pagination) => {
    setLoading(true)
    try {
      const result = await getDeviceConfigBackups(deviceId, {
        skip: (nextPagination.current - 1) * nextPagination.pageSize,
        limit: nextPagination.pageSize,
      })
      setRows(result.items || [])
      setTotal(result.total || 0)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    const next = { ...pagination, current: 1 }
    setPagination(next)
    fetchRows(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId])

  const download = async (row: DeviceConfigBackupRow) => {
    const result = await getConfigBackupResult(row.id)
    const blob = new Blob([result.config_content || ''], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.config_name
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Table<DeviceConfigBackupRow>
      loading={loading}
      rowKey="id"
      dataSource={rows}
      size="small"
      pagination={{
        total,
        current: pagination.current,
        pageSize: pagination.pageSize,
        showSizeChanger: true,
        pageSizeOptions: ['10', '20', '50', '100'],
        showTotal: (value) => `共 ${value} 条`,
        onChange: (current, pageSize) => {
          const next = { current, pageSize }
          setPagination(next)
          fetchRows(next)
        },
        onShowSizeChange: (_, pageSize) => {
          const next = { current: 1, pageSize }
          setPagination(next)
          fetchRows(next)
        },
      }}
      columns={[
        { title: '设备名称', dataIndex: 'device_name' },
        { title: 'IP', dataIndex: 'device_ip', width: 150 },
        { title: '机房', dataIndex: 'datacenter_name', width: 220 },
        { title: '配置名称', dataIndex: 'config_name', ellipsis: true },
        { title: '时间', dataIndex: 'finished_at', width: 180, render: formatDateTimeText },
        { title: '操作', width: 100, render: (_, row) => <Button size="small" icon={<DownloadOutlined />} onClick={() => download(row)}>下载</Button> },
      ]}
    />
  )
}

const PerformanceTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [series, setSeries] = useState<any[]>([])
  const [discardInterfaces, setDiscardInterfaces] = useState<DeviceConnectionRow[]>([])
  const [selectedDiscardIndexes, setSelectedDiscardIndexes] = useState<string[]>([])
  const [discardHistories, setDiscardHistories] = useState<Record<string, MonitorHistoryPoint[]>>({})
  const [rangeValue, setRangeValue] = useState('-24h')
  const [intervalValue, setIntervalValue] = useState('5m')
  const [customRange, setCustomRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const requestSeqRef = useRef(0)
  const discardRequestSeqRef = useRef(0)
  const discardQueryParams = useMemo(() => buildHistoryParams(rangeValue, intervalValue, customRange), [rangeValue, intervalValue, customRange])
  const discardQueryKey = useMemo(() => historyParamsKey(discardQueryParams), [discardQueryParams])
  const fetchRows = async (
    nextRangeValue = rangeValue,
    nextIntervalValue = intervalValue,
    nextCustomRange = customRange,
  ) => {
    if (nextRangeValue === 'custom' && !nextCustomRange) {
      message.warning('请选择历史开始和结束时间')
      return
    }
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    setLoading(true)
    try {
      const result = await getDevicePerformance(deviceId, buildHistoryParams(nextRangeValue, nextIntervalValue, nextCustomRange))
      if (requestSeqRef.current !== requestSeq) return
      setSeries(result.series || [])
    } finally {
      if (requestSeqRef.current === requestSeq) setLoading(false)
    }
  }
  useEffect(() => {
    setRangeValue('-24h')
    setIntervalValue('5m')
    setCustomRange(null)
    setDiscardHistories({})
    fetchRows('-24h', '5m', null)
    getDeviceConnections(deviceId)
      .then((result) => {
        const rows = (result.items || []).filter((item) => Number(item.index) > 0)
        const sorted = [...rows].sort((a, b) => {
          const statusA = String(a.oper_status || '').toLowerCase() === 'up' ? 0 : 1
          const statusB = String(b.oper_status || '').toLowerCase() === 'up' ? 0 : 1
          const physicalA = isPhysicalInterfaceName(a.name) ? 0 : 1
          const physicalB = isPhysicalInterfaceName(b.name) ? 0 : 1
          return statusA - statusB || physicalA - physicalB || String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', { numeric: true })
        })
        setDiscardInterfaces(sorted)
        setSelectedDiscardIndexes(sorted.slice(0, 2).map((item) => String(item.index)))
      })
      .catch(() => {
        setDiscardInterfaces([])
        setSelectedDiscardIndexes([])
      })
    /* getDevicePerformance(deviceId, { range: '-24h', interval: '5m' })
      .then((result) => setSeries(result.series || []))
      .finally(() => setLoading(false)) */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId])

  useEffect(() => {
    const requestSeq = discardRequestSeqRef.current + 1
    discardRequestSeqRef.current = requestSeq
    setDiscardHistories({})
    selectedDiscardIndexes.forEach((indexText) => {
      const index = Number(indexText)
      if (!index) return
      getMonitorInterfaceHistory(deviceId, index, { ...discardQueryParams, group: 'errors', rate_window: '5m' })
        .then((result) => {
          if (discardRequestSeqRef.current !== requestSeq) return
          setDiscardHistories((prev) => ({ ...prev, [`${discardQueryKey}:${indexText}`]: result.data || [] }))
        })
        .catch(() => {
          if (discardRequestSeqRef.current !== requestSeq) return
          setDiscardHistories((prev) => ({ ...prev, [`${discardQueryKey}:${indexText}`]: [] }))
        })
    })
  }, [deviceId, selectedDiscardIndexes.join(','), discardQueryKey])

  const discardOptions = useMemo(() => discardInterfaces.map((item) => ({
    label: buildInterfaceTitle({ ...item, description: cleanDisplayDescription(item) }),
    value: String(item.index),
  })), [discardInterfaces])

  const discardInterfaceByIndex = useMemo(() => {
    const map = new Map<string, DeviceConnectionRow>()
    discardInterfaces.forEach((item) => map.set(String(item.index), item))
    return map
  }, [discardInterfaces])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Space wrap>
        <Select value={rangeValue} onChange={(value) => { setRangeValue(value); if (value !== 'custom') fetchRows(value, intervalValue, customRange) }} options={historyRangeOptions} style={{ width: 130 }} />
        {rangeValue === 'custom' ? <RangePicker showTime value={customRange as any} onChange={(value) => { const next = value as any; setCustomRange(next); if (next) fetchRows('custom', intervalValue, next) }} /> : null}
        <Select value={intervalValue} onChange={(value) => { setIntervalValue(value); fetchRows(rangeValue, value, customRange) }} options={performanceIntervalOptions} style={{ width: 120 }} />
        <Button type="primary" onClick={() => fetchRows()}>查看历史</Button>
        <Button icon={<ReloadOutlined />} onClick={() => fetchRows()}>刷新曲线</Button>
      </Space>
      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
        {series.map((item) => {
          const meta = performanceMeta(item.name)
          return (
          <Col span={24} key={item.name}>
            <Card size="small" title={meta.title}>
              {(item.data || []).length ? (
                <Suspense fallback={<Spin style={{ display: 'block', margin: '92px auto' }} />}>
                  <MetricTrendChart
                    data={(item.data || []).map((point: any) => ({ time: normalizeChartTime(point.time), value: Number(point.value || 0) }))}
                    unit={meta.unit}
                    formatMetricValue={formatMetricValue}
                  />
                </Suspense>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无性能数据" />}
            </Card>
          </Col>
          )
        })}
        </Row>
        <Card size="small" title="接口丢弃趋势">
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder="选择要查看丢弃趋势的接口"
              value={selectedDiscardIndexes}
              onChange={(value) => setSelectedDiscardIndexes(value.slice(0, 8))}
              options={discardOptions}
              style={{ width: '100%' }}
            />
            {selectedDiscardIndexes.length ? (
              <Row gutter={[16, 16]}>
                {selectedDiscardIndexes.map((indexText) => {
                  const iface = discardInterfaceByIndex.get(indexText)
                  const title = iface ? buildInterfaceTitle({ ...iface, description: cleanDisplayDescription(iface) }) : `接口 ${indexText}`
                  const data = (discardHistories[`${discardQueryKey}:${indexText}`] || []).map((point) => ({
                    time: normalizeChartTime(point._time),
                    in_discards_delta: Number(point.in_discards_delta || point.queue_ingress_dropped_pkts_delta || 0),
                    out_discards_delta: Number(point.out_discards_delta || point.queue_egress_dropped_pkts_delta || 0),
                  }))
                  return (
                    <Col xs={24} xl={12} key={indexText}>
                      <Card size="small" title={<div title={title} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>}>
                        {data.length ? (
                          <Suspense fallback={<Spin style={{ display: 'block', margin: '72px auto' }} />}>
                            <InterfaceDiscardChart data={data} />
                          </Suspense>
                        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无丢弃数据" />}
                      </Card>
                    </Col>
                  )
                })}
              </Row>
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择接口查看丢弃趋势" />}
          </Space>
        </Card>
      </Spin>
    </Space>
  )
}

const HardwareTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceHardwareRow[]>([])
  useEffect(() => {
    setLoading(true)
    getDeviceHardware(deviceId)
      .then((result) => setRows(result.items || []))
      .finally(() => setLoading(false))
  }, [deviceId])

  const groupedRows = useMemo(() => {
    const byType = (type: string) => rows.filter((row) => String(row.component_type || '').toLowerCase() === type)
    return {
      power: byType('power'),
      fan: byType('fan'),
      module: byType('module'),
    }
  }, [rows])

  const baseColumns = [
    { title: '名称', dataIndex: 'component', ellipsis: true },
    { title: '状态', dataIndex: 'up', width: 110, render: (_: any, row: DeviceHardwareRow) => hardwareStatusTag(row) },
    { title: '在位', dataIndex: 'present', width: 90, render: (value: any) => value === undefined || value === null ? '-' : String(value) },
    { title: '原始状态', dataIndex: 'state', width: 110, render: (value: any) => formatOptionalNumber(value) },
    { title: '采集时间', dataIndex: 'time', width: 170, render: formatDateTimeText },
  ]

  const renderHardwareTable = (
    title: string,
    dataSource: DeviceHardwareRow[],
    extraColumns: any[] = [],
  ) => (
    <Card size="small" title={`${title}（${dataSource.length}）`}>
      <Table<DeviceHardwareRow>
        loading={loading}
        rowKey={(row) => `${row.component_type}-${row.component}`}
        dataSource={dataSource}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
        columns={[baseColumns[0], ...extraColumns, ...baseColumns.slice(1)]}
        locale={{ emptyText: '暂无数据' }}
      />
    </Card>
  )

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {renderHardwareTable('电源', groupedRows.power, [
        { title: '输入功率', dataIndex: 'power_input', width: 120, render: (value: any) => formatOptionalNumber(value, ' W') },
      ])}
      {renderHardwareTable('风扇', groupedRows.fan, [
        { title: '转速', dataIndex: 'speed', width: 120, render: (value: any) => formatOptionalNumber(value) },
      ])}
      {renderHardwareTable('模块', groupedRows.module, [
        { title: 'RX功率', dataIndex: 'rx_power', width: 120, render: (value: any) => formatOptionalNumber(value, ' dBm') },
        { title: 'TX功率', dataIndex: 'tx_power', width: 120, render: (value: any) => formatOptionalNumber(value, ' dBm') },
        { title: '温度', dataIndex: 'temperature', width: 110, render: (value: any) => formatOptionalNumber(value, '℃') },
      ])}
    </Space>
  )
}

const TacacsTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceTacacsRow[]>([])
  const [search, setSearch] = useState('')
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [operationType, setOperationType] = useState<string | undefined>()
  const requestSeqRef = useRef(0)
  const fetchRows = async (
    nextRange = range,
    nextSearch = search,
    nextOperationType = operationType,
  ) => {
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    setLoading(true)
    try {
      const result = await getDeviceTacacs(deviceId, {
        limit: 100,
        search: nextSearch || undefined,
        start_time: nextRange?.[0]?.format('YYYY-MM-DD HH:mm:ss'),
        end_time: nextRange?.[1]?.format('YYYY-MM-DD HH:mm:ss'),
      })
      if (requestSeqRef.current !== requestSeq) return
      const items = result.items || []
      setRows(nextOperationType ? items.filter((item) => item.operation_type === nextOperationType) : items)
    } finally {
      if (requestSeqRef.current === requestSeq) setLoading(false)
    }
  }
  useEffect(() => { fetchRows() }, [deviceId])
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space>
        <Input allowClear placeholder="搜索用户/命令/原文" value={search} onChange={(event) => setSearch(event.target.value)} onPressEnter={() => fetchRows()} style={{ width: 280 }} />
        <RangePicker showTime value={range as any} onChange={(value) => { const next = value as any; setRange(next); fetchRows(next, search, operationType) }} />
        <Select allowClear placeholder="操作类型" value={operationType} onChange={(value) => { setOperationType(value); fetchRows(range, search, value) }} style={{ width: 140 }} options={[{ value: '查询操作' }, { value: '配置操作' }, { value: '审计类操作' }, { value: '登录' }, { value: '退出' }]} />
        <Button type="primary" onClick={() => fetchRows()}>查询</Button>
      </Space>
      <Table<DeviceTacacsRow>
        loading={loading}
        rowKey={(row, index) => `${row.time}-${row.username}-${index}`}
        dataSource={rows}
        size="small"
        scroll={{ x: 1400 }}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        columns={[
          { title: '用户ID', dataIndex: 'username', width: 120, fixed: 'left' },
          { title: '用户IP', dataIndex: 'client_ip', width: 140 },
          { title: '时间', dataIndex: 'time', width: 180 },
          { title: '登录时间', dataIndex: 'login_time', width: 180 },
          { title: '操作类型', dataIndex: 'operation_type', width: 120 },
          { title: '操作指令', dataIndex: 'command', width: 260, ellipsis: true },
          { title: '完整日志记录', dataIndex: 'raw', ellipsis: true },
        ]}
      />
    </Space>
  )
}

export default DeviceDetail
