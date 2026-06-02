import { useEffect, useMemo, useRef, useState } from 'react'
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
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getDeviceOverview,
  getDeviceProtocolNeighbors,
  refreshMonitorDevice,
  type DeviceOverviewItem,
  type DeviceProtocolSummary,
  type ProtocolNeighbor,
} from '../../api/metrics'

const { Text } = Typography

const VENDOR_OPTIONS = [
  { value: '', label: '全部厂商' },
  { value: 'Aster', label: 'AsterNOS/AsterOS' },
  { value: 'H3C', label: 'H3C' },
  { value: '华为', label: '华为' },
  { value: '锐捷', label: '锐捷' },
  { value: 'Hillstone', label: 'Hillstone/山石' },
]

const CONNECTIVITY_OPTIONS = [
  { value: '', label: '全部连通性' },
  { value: 'reachable', label: '可达' },
  { value: 'unreachable', label: '不可达' },
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
  { value: 'protocol_down_desc', label: '协议失败优先' },
  { value: 'vendor_asc', label: '厂商 A-Z' },
  { value: 'model_asc', label: '型号 A-Z' },
]

const ipToNumber = (ip?: string) => {
  const parts = String(ip || '').split('.').map((item) => Number(item))
  if (parts.length !== 4 || parts.some((item) => Number.isNaN(item))) return 0
  return parts.reduce((sum, part) => (sum << 8) + part, 0)
}

const normalizePercent = (value?: number | null) => {
  if (value === undefined || value === null) return null
  const numeric = Number(value)
  return numeric <= 1 ? numeric * 100 : numeric
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

const ResourceCell = ({ value }: { value?: number | null }) => {
  const normalized = normalizePercent(value)
  if (normalized === null) return <Text type="secondary">-</Text>
  return (
    <div style={{ minWidth: 100 }}>
      <Progress
        percent={Math.max(0, Math.min(100, normalized))}
        size="small"
        status={percentStatus(value)}
        format={() => formatPercent(value)}
      />
    </div>
  )
}

const HardwareCell = ({ total = 0, down = 0, label }: { total?: number; down?: number; label: string }) => {
  if (!total) return <Text type="secondary">-</Text>
  return <Tag color={down > 0 ? 'red' : 'green'}>{label} {total - down}/{total}</Tag>
}

const ProtocolCell = ({ data }: { data: DeviceProtocolSummary }) => {
  if (!data || data.total <= 0) return <Text type="secondary">-</Text>
  const color = data.down > 0 ? 'red' : 'green'
  return (
    <Space size={4}>
      <Tag color={color}>{data.up}/{data.total}</Tag>
      {data.down > 0 ? <Text type="danger">失败 {data.down}</Text> : null}
    </Space>
  )
}

const ConnectivityTag = ({ item }: { item: DeviceOverviewItem }) => {
  const { status, message: detail } = item.connectivity
  const typeLabel = item.monitor_source === 'asternos_exporter' ? 'Exporter' : 'SNMP'
  const color =
    status === 'reachable'
      ? 'green'
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

const DeviceOverview = () => {
  const [loading, setLoading] = useState(false)
  const [refreshingDeviceId, setRefreshingDeviceId] = useState<number | null>(null)
  const [items, setItems] = useState<DeviceOverviewItem[]>([])
  const [search, setSearch] = useState('')
  const [vendor, setVendor] = useState('')
  const [model, setModel] = useState('')
  const [connectivity, setConnectivity] = useState('')
  const [sortKey, setSortKey] = useState('ip_asc')
  const [tablePageSize, setTablePageSize] = useState(20)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailDevice, setDetailDevice] = useState<DeviceOverviewItem | null>(null)
  const [neighbors, setNeighbors] = useState<{ bgp: ProtocolNeighbor[]; ospf: ProtocolNeighbor[] }>({ bgp: [], ospf: [] })
  const filtersReadyRef = useRef(false)
  const [visibleColumnKeys, setVisibleColumnKeys] = useState<string[]>([
    'vendor',
    'connectivity',
    'cpu',
    'memory',
    'temperature',
    'bgp',
    'ospf',
    'updated_at',
  ])

  const columnOptions = [
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

  const loadData = async (
    nextSearch = search,
    nextVendor = vendor,
    nextModel = model,
    nextConnectivity = connectivity
  ) => {
    setLoading(true)
    try {
      const result = await getDeviceOverview({
        search: nextSearch.trim() || undefined,
        vendor: nextVendor || undefined,
        model: nextModel.trim() || undefined,
        connectivity: nextConnectivity || undefined,
        monitored_only: true,
        include_storage: visibleColumnKeys.includes('storage'),
        include_hardware: visibleColumnKeys.includes('fan') || visibleColumnKeys.includes('power'),
        include_sessions: false,
        limit: 500,
      })
      setItems(result.items || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取设备总览失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData('', '', '', '')
  }, [])

  useEffect(() => {
    if (!filtersReadyRef.current) {
      filtersReadyRef.current = true
      return
    }
    const timer = window.setTimeout(() => {
      loadData(search, vendor, model, connectivity)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [search, vendor, model, connectivity, visibleColumnKeys])

  const sortedItems = useMemo(() => {
    const protocolDownCount = (item: DeviceOverviewItem) => item.protocols.bgp.down + item.protocols.ospf.down
    const list = [...items]
    return list.sort((a, b) => {
      if (sortKey === 'ip_desc') return ipToNumber(b.device.ip_address) - ipToNumber(a.device.ip_address)
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
      return ipToNumber(a.device.ip_address) - ipToNumber(b.device.ip_address)
    })
  }, [items, sortKey])

  const stats = useMemo(() => {
    const reachable = items.filter((item) => item.connectivity.status === 'reachable').length
    const protocolDown = items.reduce((sum, item) => sum + item.protocols.bgp.down + item.protocols.ospf.down, 0)
    return { total: items.length, reachable, protocolDown }
  }, [items])

  const openDetail = async (item: DeviceOverviewItem) => {
    setDetailDevice(item)
    setDetailOpen(true)
    setDetailLoading(true)
    try {
      const result = await getDeviceProtocolNeighbors(item.device.id)
      setNeighbors(result.neighbors)
    } catch (error: any) {
      setNeighbors({ bgp: [], ospf: [] })
      message.error(error?.response?.data?.detail || '获取协议邻居失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDeviceRefresh = async (item: DeviceOverviewItem) => {
    setRefreshingDeviceId(item.device.id)
    try {
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

  const neighborColumns = [
    {
      title: '邻居',
      dataIndex: 'peer',
      key: 'peer',
      width: 160,
    },
    {
      title: '状态',
      key: 'status',
      width: 120,
      render: (_: any, record: ProtocolNeighbor) => (
        <Tag color={record.status === 'up' ? 'green' : 'red'}>{record.state || record.status}</Tag>
      ),
    },
    {
      title: '接口',
      dataIndex: 'interface',
      key: 'interface',
      width: 180,
      render: (value: string | null) => value || '-',
    },
    {
      title: 'Remote AS',
      dataIndex: 'remote_as',
      key: 'remote_as',
      width: 120,
      render: (value: string | number | null) => value || '-',
    },
    {
      title: '持续时间',
      key: 'duration',
      width: 150,
      render: (_: any, record: ProtocolNeighbor) => formatDuration(record.duration_seconds, record.duration_text),
    },
  ]

  const allColumns: any[] = [
    {
      title: '设备',
      key: 'device',
      fixed: 'left',
      width: 220,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => ipToNumber(a.device.ip_address) - ipToNumber(b.device.ip_address),
      render: (_: any, record: DeviceOverviewItem) => (
        <Space direction="vertical" size={0}>
          <Text strong>{record.device.name || record.device.hostname || record.device.ip_address}</Text>
          <Text type="secondary">{record.device.ip_address}</Text>
        </Space>
      ),
    },
    {
      title: '厂商/型号',
      key: 'vendor',
      width: 180,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => String(a.device.model || '').localeCompare(String(b.device.model || '')),
      render: (_: any, record: DeviceOverviewItem) => (
        <Space direction="vertical" size={0}>
          <Text>{record.device.vendor || '-'}</Text>
          <Text type="secondary">{record.device.model || '-'}</Text>
        </Space>
      ),
    },
    {
      title: '连通性',
      key: 'connectivity',
      width: 140,
      filters: CONNECTIVITY_OPTIONS.filter((item) => item.value).map((item) => ({ text: item.label, value: item.value })),
      onFilter: (value: any, record: DeviceOverviewItem) => record.connectivity.status === value,
      render: (_: any, record: DeviceOverviewItem) => <ConnectivityTag item={record} />,
    },
    {
      title: 'CPU',
      key: 'cpu',
      width: 130,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.cpu_percent) || -1) - (normalizePercent(b.resources.cpu_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.cpu_percent} />,
    },
    {
      title: '内存',
      key: 'memory',
      width: 130,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.memory_percent) || -1) - (normalizePercent(b.resources.memory_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.memory_percent} />,
    },
    {
      title: '温度',
      key: 'temperature',
      width: 100,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (a.resources.temperature || -1) - (b.resources.temperature || -1),
      render: (_: any, record: DeviceOverviewItem) =>
        record.resources.temperature === undefined || record.resources.temperature === null
          ? <Text type="secondary">-</Text>
          : <Text>{Number(record.resources.temperature).toFixed(1)}℃</Text>,
    },
    {
      title: '存储',
      key: 'storage',
      width: 130,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => (normalizePercent(a.resources.storage_percent) || -1) - (normalizePercent(b.resources.storage_percent) || -1),
      render: (_: any, record: DeviceOverviewItem) => <ResourceCell value={record.resources.storage_percent} />,
    },
    {
      title: '风扇',
      key: 'fan',
      width: 110,
      render: (_: any, record: DeviceOverviewItem) => (
        <HardwareCell label="正常" total={record.hardware?.fan_total} down={record.hardware?.fan_down} />
      ),
    },
    {
      title: '电源',
      key: 'power',
      width: 110,
      render: (_: any, record: DeviceOverviewItem) => (
        <HardwareCell label="正常" total={record.hardware?.power_total} down={record.hardware?.power_down} />
      ),
    },
    {
      title: 'BGP',
      key: 'bgp',
      width: 120,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => a.protocols.bgp.down - b.protocols.bgp.down,
      render: (_: any, record: DeviceOverviewItem) => <ProtocolCell data={record.protocols.bgp} />,
    },
    {
      title: 'OSPF',
      key: 'ospf',
      width: 120,
      sorter: (a: DeviceOverviewItem, b: DeviceOverviewItem) => a.protocols.ospf.down - b.protocols.ospf.down,
      render: (_: any, record: DeviceOverviewItem) => <ProtocolCell data={record.protocols.ospf} />,
    },
    {
      title: '更新时间',
      key: 'updated_at',
      width: 170,
      render: (_: any, record: DeviceOverviewItem) => dayjs(record.collected_at).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'detail',
      width: 150,
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

  const visibleColumns = allColumns.filter((column) => column.key === 'device' || column.key === 'detail' || visibleColumnKeys.includes(column.key))

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
          <Input allowClear placeholder="筛选型号" value={model} onChange={(event) => setModel(event.target.value)} style={{ width: 220 }} />
          <Select style={{ width: 180 }} options={CONNECTIVITY_OPTIONS} value={connectivity} onChange={setConnectivity} />
          <Select style={{ width: 190 }} options={SORT_OPTIONS} value={sortKey} onChange={setSortKey} />
          <Space size={10} wrap style={{ marginLeft: 'auto' }}>
            <Dropdown
              trigger={['click']}
              dropdownRender={() => (
                <Card size="small" bodyStyle={{ padding: 10, width: 160 }}>
                  <Checkbox.Group
                    options={columnOptions}
                    value={visibleColumnKeys}
                    onChange={(values) => setVisibleColumnKeys(values.map(String))}
                  />
                </Card>
              )}
            >
              <Button>显示/隐藏列</Button>
            </Dropdown>
            <Button icon={<ReloadOutlined />} onClick={() => loadData()} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>
        <Space wrap style={{ marginTop: 12 }}>
          <Tag color="blue">设备 {stats.total}</Tag>
          <Tag color="green">连通 {stats.reachable}</Tag>
          <Tag color={stats.protocolDown > 0 ? 'red' : 'default'}>协议失败 {stats.protocolDown}</Tag>
        </Space>
      </Card>

      <Card bodyStyle={{ padding: 0 }}>
        <Table<DeviceOverviewItem>
          rowKey={(record) => record.device.id}
          loading={loading}
          dataSource={sortedItems}
          scroll={{ x: 1500 }}
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
        width={860}
      >
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
                  pagination={false}
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
                  columns={neighborColumns}
                  pagination={false}
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
