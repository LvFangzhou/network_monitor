import { type UIEvent, useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Col, DatePicker, Descriptions, Empty, Input, Row, Select, Space, Spin, Table, Tabs, Tag, message } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, EditOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
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

const formatDateTimeText = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return '-'
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

  if (loading) return <Spin style={{ display: 'block', margin: '100px auto' }} />
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

  const fetchRows = async () => {
    setLoading(true)
    try {
      const result = await getDeviceConnections(deviceId)
      setRows(result.items || [])
      setNotice(result.message || '')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取连接信息失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchRows() }, [deviceId])

  const filtered = useMemo(() => {
    const key = keyword.trim().toLowerCase()
    if (!key) return rows
    return rows.filter((item) => Object.values(item).some((value) => String(value ?? '').toLowerCase().includes(key)))
  }, [rows, keyword])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Space>
        <Input allowClear prefix={<SearchOutlined />} placeholder="搜索接口/描述/对端" value={keyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 260 }} />
        <Button icon={<ReloadOutlined />} onClick={fetchRows}>刷新</Button>
      </Space>
      {notice ? <Alert type="info" showIcon message={notice} /> : null}
      <Table<DeviceConnectionRow>
        loading={loading}
        rowKey={(row) => row.index || row.name}
        dataSource={filtered}
        size="small"
        scroll={{ x: 1500 }}
        pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
        columns={[
          {
            title: '本地接口名称',
            dataIndex: 'name',
            width: 210,
            fixed: 'left',
            sorter: textSorter<DeviceConnectionRow>('name'),
            render: (value, row) => (
              <Space direction="vertical" size={0}>
                <span>{value || '-'}</span>
                {row.logical_type ? <span style={{ color: '#8c8c8c', fontSize: 12 }}>{row.logical_type}</span> : null}
              </Space>
            ),
          },
          { title: '描述', dataIndex: 'description', width: 260, ellipsis: true, sorter: textSorter<DeviceConnectionRow>('description') },
          { title: '速率', dataIndex: 'speed_bps', width: 120, render: formatBps, sorter: numberSorter<DeviceConnectionRow>('speed_bps') },
          { title: 'MTU', dataIndex: 'mtu', width: 90, sorter: numberSorter<DeviceConnectionRow>('mtu') },
          {
            title: '接口IP',
            dataIndex: 'ip_address',
            width: 180,
            sorter: textSorter<DeviceConnectionRow>('ip_address'),
            render: (value) => <span style={{ whiteSpace: 'nowrap' }}>{value || '-'}</span>,
          },
          { title: '接口状态', dataIndex: 'oper_status', width: 100, render: statusTag, sorter: textSorter<DeviceConnectionRow>('oper_status') },
          { title: '接口管理状态', dataIndex: 'admin_status', width: 120, render: statusTag, sorter: textSorter<DeviceConnectionRow>('admin_status') },
          { title: '对端设备', dataIndex: 'remote_device', width: 240, ellipsis: true, sorter: textSorter<DeviceConnectionRow>('remote_device') },
          { title: '对端接口', dataIndex: 'remote_interface', width: 160, sorter: textSorter<DeviceConnectionRow>('remote_interface') },
          { title: '对端管理IP', dataIndex: 'remote_management_ip', width: 150, sorter: textSorter<DeviceConnectionRow>('remote_management_ip') },
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
    selected.forEach((item) => {
      const index = Number(item.index)
      if (!index || histories[item.index]) return
      getMonitorInterfaceHistory(deviceId, index, { range: '-6h', interval: '2m', rate_window: '5m' })
        .then((result) => setHistories((prev) => ({ ...prev, [item.index]: result.data || [] })))
        .catch(() => setHistories((prev) => ({ ...prev, [item.index]: [] })))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected.map((item) => item.index).join(','), deviceId])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Space wrap>
        <Input allowClear prefix={<SearchOutlined />} placeholder="筛选接口" value={keyword} onChange={(event) => setKeyword(event.target.value)} style={{ width: 220 }} />
        <span style={{ color: '#8c8c8c' }}>已展示 {selected.length}/{filteredInterfaces.length} 个接口，向下滚动自动加载更多</span>
      </Space>
      <Spin spinning={loading}>
        <div onScroll={handleTrafficScroll} style={{ maxHeight: 'calc(100vh - 300px)', overflowY: 'auto', overflowX: 'hidden', paddingRight: 4 }}>
          <Row gutter={[16, 16]}>
            {selected.map((item) => {
              const data = (histories[item.index] || []).map((point) => ({
                time: normalizeChartTime(point._time),
                in_bps: Number(point.in_bps || 0),
                out_bps: Number(point.out_bps || 0),
              }))
              const title = `${item.name}${item.description ? ` / ${item.description}` : ''}`
              return (
                <Col xs={24} xl={12} key={item.index}>
                  <Card
                    size="small"
                    title={<div title={title} style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>}
                    styles={{ header: { minWidth: 0 }, body: { overflow: 'hidden' } }}
                  >
                    {data.length ? (
                      <ResponsiveContainer width="100%" height={220}>
                        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="time" minTickGap={28} />
                          <YAxis tickFormatter={formatBps} width={76} />
                          <ChartTooltip formatter={(value) => formatBps(Number(value))} />
                          <Line type="monotone" dataKey="in_bps" name="In" stroke="#52c41a" dot={false} strokeWidth={1.8} />
                          <Line type="monotone" dataKey="out_bps" name="Out" stroke="#1677ff" dot={false} strokeWidth={1.8} />
                        </LineChart>
                      </ResponsiveContainer>
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

  const fetchRows = async () => {
    setLoading(true)
    try {
      const result = await getDeviceSyslog(deviceId, {
        limit: 50,
        search: search || undefined,
        start_time: range?.[0]?.toISOString(),
        end_time: range?.[1]?.toISOString(),
      })
      setRows(result.items || [])
      setTotal(result.total || 0)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { fetchRows() }, [deviceId])
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space>
        <Input allowClear placeholder="关键字搜索日志正文" value={search} onChange={(event) => setSearch(event.target.value)} onPressEnter={fetchRows} style={{ width: 280 }} />
        <RangePicker showTime value={range as any} onChange={(value) => setRange(value as any)} />
        <Button type="primary" onClick={fetchRows}>查询</Button>
      </Space>
      <Table<DeviceLogRow>
        loading={loading}
        rowKey={(row, index) => `${row.id || row.time}-${index}`}
        dataSource={rows}
        size="small"
        pagination={{ total, pageSize: 20, showSizeChanger: true }}
        columns={[
          { title: '时间', dataIndex: 'time', width: 180, render: formatDateTimeText },
          { title: '日志级别', dataIndex: 'severity', width: 100 },
          { title: '日志正文', dataIndex: 'message', ellipsis: true },
          { title: '原始日志', dataIndex: 'raw_message', ellipsis: true },
        ]}
      />
    </Space>
  )
}

const ConfigTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceConfigBackupRow[]>([])

  const fetchRows = async () => {
    setLoading(true)
    try {
      const result = await getDeviceConfigBackups(deviceId, { limit: 50 })
      setRows(result.items || [])
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { fetchRows() }, [deviceId])

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
      pagination={{ pageSize: 20, showSizeChanger: true }}
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
  const fetchRows = async () => {
    setLoading(true)
    try {
      const result = await getDevicePerformance(deviceId, { range: '-24h', interval: '5m' })
      setSeries(result.series || [])
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { fetchRows() }, [deviceId])
  return (
    <Spin spinning={loading}>
      <Row gutter={[16, 16]}>
        {series.map((item) => {
          const meta = performanceMeta(item.name)
          return (
          <Col span={24} key={item.name}>
            <Card size="small" title={meta.title}>
              {(item.data || []).length ? (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={(item.data || []).map((point: any) => ({ time: normalizeChartTime(point.time), value: Number(point.value || 0) }))}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="time" minTickGap={28} />
                    <YAxis tickFormatter={(value) => formatMetricValue(value, meta.unit)} />
                    <ChartTooltip formatter={(value) => formatMetricValue(value, meta.unit)} />
                    <Line type="monotone" dataKey="value" stroke="#f5222d" dot={false} strokeWidth={1.8} />
                  </LineChart>
                </ResponsiveContainer>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无性能数据" />}
            </Card>
          </Col>
          )
        })}
      </Row>
    </Spin>
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
  return (
    <Table<DeviceHardwareRow>
      loading={loading}
      rowKey={(row) => `${row.component_type}-${row.component}`}
      dataSource={rows}
      size="small"
      pagination={{ pageSize: 20, showSizeChanger: true }}
      columns={[
        { title: '硬件类型', dataIndex: 'component_type', width: 140 },
        { title: '模块/名称', dataIndex: 'component' },
        { title: '状态', dataIndex: 'up', width: 120, render: (_, row) => hardwareStatusTag(row) },
        { title: '在位', dataIndex: 'present', width: 100 },
        { title: '速率', dataIndex: 'speed', width: 120 },
        { title: '采集时间', dataIndex: 'time', width: 180, render: formatDateTimeText },
      ]}
    />
  )
}

const TacacsTab = ({ deviceId }: { deviceId: number }) => {
  const [loading, setLoading] = useState(false)
  const [rows, setRows] = useState<DeviceTacacsRow[]>([])
  const [search, setSearch] = useState('')
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [operationType, setOperationType] = useState<string | undefined>()
  const fetchRows = async () => {
    setLoading(true)
    try {
      const result = await getDeviceTacacs(deviceId, {
        limit: 100,
        search: search || undefined,
        start_time: range?.[0]?.format('YYYY-MM-DD HH:mm:ss'),
        end_time: range?.[1]?.format('YYYY-MM-DD HH:mm:ss'),
      })
      const items = result.items || []
      setRows(operationType ? items.filter((item) => item.operation_type === operationType) : items)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { fetchRows() }, [deviceId])
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space>
        <Input allowClear placeholder="搜索用户/命令/原文" value={search} onChange={(event) => setSearch(event.target.value)} onPressEnter={fetchRows} style={{ width: 280 }} />
        <RangePicker showTime value={range as any} onChange={(value) => setRange(value as any)} />
        <Select allowClear placeholder="操作类型" value={operationType} onChange={setOperationType} style={{ width: 140 }} options={[{ value: '查询操作' }, { value: '配置操作' }, { value: '审计类操作' }, { value: '登录' }, { value: '退出' }]} />
        <Button type="primary" onClick={fetchRows}>查询</Button>
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
