import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Checkbox, Descriptions, Drawer, Input, Popover, Segmented, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import { AppstoreOutlined, EyeOutlined, ReloadOutlined, SearchOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { getLosslessTelemetryDevices, getLosslessTelemetrySnapshot } from '../../api/metrics'

const { Text } = Typography

const formatRate = (value: any, unit: 'bps' | 'pps') => {
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  if (unit === 'pps') return `${number.toLocaleString()} pps`
  if (Math.abs(number) >= 1e9) return `${(number / 1e9).toFixed(2)} Gbps`
  if (Math.abs(number) >= 1e6) return `${(number / 1e6).toFixed(2)} Mbps`
  if (Math.abs(number) >= 1e3) return `${(number / 1e3).toFixed(2)} Kbps`
  return `${number.toFixed(0)} bps`
}

const formatSpeed = (value: any) => formatRate(value, 'bps')
const displayNumber = (value: any) => value === null || value === undefined ? '-' : Number(value).toLocaleString()
const displayPercent = (value: any) => value === null || value === undefined || !Number.isFinite(Number(value)) ? '-' : `${Number(value).toFixed(2)}%`

const buildLosslessPortMapUrl = (snapshot: any, search: string, status: string) => {
  const device = snapshot?.device
  if (!device?.name) return ''
  const params = new URLSearchParams({
    orgId: '1',
    from: 'now-10m',
    to: 'now',
    refresh: '60s',
    theme: 'light',
    'var-device_name': device.name,
    'var-device_ip': device.ip_address || '',
    'var-interface_name': '',
    'var-port_search': search.trim(),
    'var-port_status': status,
    viewPanel: '7',
  })
  return `/grafana-app/d/network-interface-overview/network-interface-overview?${params.toString()}&kiosk`
}

const sortableVisibleColumns = (columns: any[], visible: string[]) => columns
  .filter((column) => visible.includes(String(column.key || column.dataIndex || '')))
  .map((column) => {
    const field = String(column.dataIndex || column.key || '')
    return {
      ...column,
      key: column.key || field,
      sorter: column.sorter || ((left: any, right: any) => {
        const leftValue = left?.[field]
        const rightValue = right?.[field]
        const leftNumber = Number(leftValue)
        const rightNumber = Number(rightValue)
        if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber
        return String(leftValue ?? '').localeCompare(String(rightValue ?? ''), undefined, { numeric: true })
      }),
    }
  })

const telemetryPortColumnOptions = [
  ['interface_name', '端口'], ['speed_bps', '速率'], ['oper_status', '运行状态'], ['admin_status', '管理状态'],
  ['status_consistent', '状态一致'], ['optical_power', '光功率'], ['in_utilization_percent', '入向使用率'],
  ['out_utilization_percent', '出向使用率'], ['in_bps', '入向流速'], ['out_bps', '出向流速'],
  ['in_pps', '入向包速'], ['out_pps', '出向包速'], ['in_broadcast_pps', '入广播包速'],
  ['out_broadcast_pps', '出广播包速'], ['in_error_packets', '入错误包'],
  ['in_no_buffer_packets', '入No Buffer丢包'], ['out_no_buffer_packets', '出No Buffer丢包'],
  ['ecn_marked_packets', 'ECN标记累计'], ['wred_dropped_packets', 'WRED丢弃累计'],
  ['in_pause_packets', 'Pause接收累计'], ['out_pause_packets', 'Pause发送累计'], ['queues', '队列'],
] as const

const telemetryQueueColumnOptions = [
  ['queue_id', '队列'], ['queue_out_unicast_bps', '出向单播流速'], ['queue_out_unicast_pps', '出向单播包速'],
  ['ingress_buffer_used', '入Buffer使用量'], ['egress_unicast_buffer_used', '出单播Buffer'],
  ['egress_multicast_buffer_used', '出组播Buffer'], ['headroom_used', 'Headroom使用量'],
  ['ingress_interval_peak', '入向区间峰值'], ['egress_interval_peak', '出向区间峰值'],
  ['queue_out_no_buffer_packets', '出向No Buffer丢包'], ['queue_out_no_buffer_pps', '出向No Buffer速率'],
  ['queue_pfc_send_pps', 'PFC发送速率'], ['queue_pfc_recv_pps', 'PFC接收速率'],
  ['queue_pfc_send_packets', 'PFC发送累计'], ['queue_pfc_recv_packets', 'PFC接收累计'],
  ['direction_0_queue_peak_bytes', '方向0队列峰值'], ['direction_1_queue_peak_bytes', '方向1队列峰值'],
] as const

const defaultVisiblePortColumns = [
  'interface_name',
  'oper_status',
  'in_utilization_percent',
  'out_utilization_percent',
  'in_bps',
  'out_bps',
  'in_pps',
  'out_pps',
  'ecn_marked_packets',
  'in_pause_packets',
  'out_pause_packets',
  'queues',
]

const telemetryLosslessCapabilities = [
  {
    category: 'PFC',
    metrics: 'PFC TX/RX、Pause 帧、PFC No-drop、PFC Deadlock',
    paths: 'pfcstatistics / pfcspeeds / pfcports/port / portnodrops / portdeadlocks',
    refresh: '60秒 / 事件',
    status: '已接入页面',
    priority: '高',
  },
  {
    category: 'Buffer',
    metrics: '端口/队列入向、出向、共享 Buffer、Headroom 使用量与超限次数',
    paths: 'commbufferusages / commheadroomusages / ingressdrops / egressdrops',
    refresh: '60秒',
    status: '已接入页面',
    priority: '高',
  },
  {
    category: 'Queue',
    metrics: '队列深度、队列使用率、队列丢包、队列长度',
    paths: 'qstat/queuestat / qos/interfaces/interface/input/queues/queue/state',
    refresh: '60秒',
    status: '已接入页面',
    priority: '高',
  },
  {
    category: 'ECN / WRED',
    metrics: 'ECN 标记速率、WRED 丢弃速率、Tail Drop',
    paths: 'ecnandwredstatistics / wred/ifqueuewreds / dropparameters',
    refresh: '60秒',
    status: '已接入页面',
    priority: '高',
  },
  {
    category: '无损事件',
    metrics: 'Queue Drop、Buffer Overrun、资源告警、Telemetry 系统事件',
    paths: 'portquedropevent / portqueoverrunevent / resourceevent / telemetryftrace/genevent',
    refresh: '实时事件',
    status: '已收到，待接入告警中心',
    priority: '高',
  },
  {
    category: 'FEC / BER / ESNR',
    metrics: 'Pre-FEC BER、ESNR、FEC 相关健康指标',
    paths: 'ifmgr/iffecdata / optical-channel/state/pre-fec-ber / optical-channel/state/esnr',
    refresh: '300秒',
    status: '已收到，待关联模块信息',
    priority: '中',
  },
  {
    category: 'MQC / QoS',
    metrics: '策略、分类、行为、匹配包数、匹配字节、丢弃、Remark',
    paths: 'mqc/rules / globalcategorypolicyaccount / ifcategorypolicyaccount / ifpolicyaccount',
    refresh: '60秒 / 300秒',
    status: '已收到，待专题展示',
    priority: '中',
  },
]

const statusColorMap: Record<string, string> = {
  '已接入页面': 'green',
  '已收到，待结构化展示': 'blue',
  '已收到，待接入告警中心': 'orange',
  '已收到，待关联模块信息': 'purple',
  '已收到，待专题展示': 'cyan',
  '设备实际未上送': 'default',
}

const capabilityPathMarkers: Record<string, string[]> = {
  PFC: ['pfcstatistics', 'pfcspeeds', 'pfcports', 'portdeadlocks'],
  Buffer: ['commbufferusages', 'commheadroomusages', 'ingressdrops', 'egressdrops'],
  Queue: ['qstat/queuestat', 'qos/interfaces'],
  'ECN / WRED': ['ecnandwredstatistics', 'wred/'],
  无损事件: ['portquedropevent', 'portqueoverrunevent', 'resourceevent', 'telemetryftrace'],
  'FEC / BER / ESNR': ['iffecdata', 'pre-fec-ber', '/esnr'],
  'MQC / QoS': ['mqc/'],
}

const LosslessInfoQuery = () => {
  const [telemetryDevices, setTelemetryDevices] = useState<any[]>([])
  const [telemetryDeviceId, setTelemetryDeviceId] = useState<number>()
  const [telemetrySearch, setTelemetrySearch] = useState('')
  const [telemetryPortMapSearch, setTelemetryPortMapSearch] = useState('')
  const [telemetryLoading, setTelemetryLoading] = useState(false)
  const [telemetrySnapshot, setTelemetrySnapshot] = useState<any>({ ports: [], path_status: [] })
  const [telemetryStatusFilter, setTelemetryStatusFilter] = useState('all')
  const [telemetrySortKey, setTelemetrySortKey] = useState('interface_name')
  const [telemetrySortOrder, setTelemetrySortOrder] = useState<'asc' | 'desc'>('asc')
  const [telemetryViewMode, setTelemetryViewMode] = useState<'port-map' | 'table'>('table')
  const [telemetryDetailPortName, setTelemetryDetailPortName] = useState<string>()
  const [telemetryDetailOpen, setTelemetryDetailOpen] = useState(false)
  const [visiblePortColumns, setVisiblePortColumns] = useState<string[]>(defaultVisiblePortColumns)
  const [visibleQueueColumns, setVisibleQueueColumns] = useState<string[]>(telemetryQueueColumnOptions.map(([value]) => value))

  const loadTelemetryDevices = async () => {
    try {
      const result = await getLosslessTelemetryDevices()
      const items = result.items || []
      setTelemetryDevices(items)
      setTelemetryDeviceId((current) => current || items[0]?.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取Telemetry设备失败')
    }
  }

  const loadTelemetrySnapshot = async () => {
    if (!telemetryDeviceId) return
    setTelemetryLoading(true)
    try {
      setTelemetrySnapshot(await getLosslessTelemetrySnapshot(telemetryDeviceId))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取Telemetry无损数据失败')
    } finally {
      setTelemetryLoading(false)
    }
  }

  useEffect(() => {
    loadTelemetryDevices()
  }, [])

  useEffect(() => {
    if (telemetryDeviceId) loadTelemetrySnapshot()
  }, [telemetryDeviceId])

  useEffect(() => {
    const timer = window.setTimeout(() => setTelemetryPortMapSearch(telemetrySearch), 400)
    return () => window.clearTimeout(timer)
  }, [telemetrySearch])

  const telemetryPorts = useMemo(() => {
    const rows = [...(telemetrySnapshot.ports || [])]
    const keyword = telemetrySearch.trim().toLowerCase()
    const filtered = rows.filter((row) => {
      if (keyword && !String(row.interface_name || '').toLowerCase().includes(keyword)) return false
      if (telemetryStatusFilter === 'up') return row.oper_status === 'up'
      if (telemetryStatusFilter === 'down') return row.oper_status === 'down'
      if (telemetryStatusFilter === 'mismatch') return row.status_consistent === false
      return true
    })
    filtered.sort((left, right) => {
      const leftValue = left?.[telemetrySortKey]
      const rightValue = right?.[telemetrySortKey]
      let result = 0
      if (telemetrySortKey === 'interface_name') {
        result = String(leftValue || '').localeCompare(String(rightValue || ''), undefined, { numeric: true })
      } else {
        const leftNumber = Number(leftValue)
        const rightNumber = Number(rightValue)
        if (!Number.isFinite(leftNumber) && !Number.isFinite(rightNumber)) result = 0
        else if (!Number.isFinite(leftNumber)) result = 1
        else if (!Number.isFinite(rightNumber)) result = -1
        else result = leftNumber - rightNumber
      }
      return telemetrySortOrder === 'asc' ? result : -result
    })
    return filtered
  }, [telemetrySnapshot.ports, telemetrySearch, telemetryStatusFilter, telemetrySortKey, telemetrySortOrder])

  const telemetryDetailPort = useMemo(
    () => (telemetrySnapshot.ports || []).find((row: any) => row.interface_name === telemetryDetailPortName),
    [telemetrySnapshot.ports, telemetryDetailPortName],
  )
  const actualCapabilities = useMemo(() => {
    const receivedPaths = (telemetrySnapshot.path_status || [])
      .filter((item: any) => item.received)
      .map((item: any) => String(item.sensor_path || '').toLowerCase())
    return telemetryLosslessCapabilities.map((item) => {
      const markers = capabilityPathMarkers[item.category] || []
      const received = markers.some((marker) => receivedPaths.some((path: string) => path.includes(marker)))
      return { ...item, status: received ? item.status : '设备实际未上送' }
    })
  }, [telemetrySnapshot.path_status])

  const openTelemetryPortDetail = (interfaceName: string) => {
    setTelemetryDetailPortName(interfaceName)
    setTelemetryDetailOpen(true)
  }

  const columnVisibilityContent = (
    <Space direction="vertical" size={12} style={{ width: 420 }}>
      <div>
        <Text strong>端口表列</Text>
        <div style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
          <Checkbox.Group
            value={visiblePortColumns}
            onChange={(values) => setVisiblePortColumns(values.map(String))}
            options={telemetryPortColumnOptions.map(([value, label]) => ({ value, label }))}
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 12px' }}
          />
        </div>
      </div>
      <Space>
        <Button size="small" onClick={() => setVisiblePortColumns(telemetryPortColumnOptions.map(([value]) => value))}>全部显示</Button>
        <Button size="small" onClick={() => setVisiblePortColumns(defaultVisiblePortColumns)}>默认列</Button>
      </Space>
      <div>
        <Text strong>队列展开表列</Text>
        <div style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
          <Checkbox.Group
            value={visibleQueueColumns}
            onChange={(values) => setVisibleQueueColumns(values.map(String))}
            options={telemetryQueueColumnOptions.map(([value, label]) => ({ value, label }))}
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 12px' }}
          />
        </div>
      </div>
      <Button size="small" onClick={() => setVisibleQueueColumns(telemetryQueueColumnOptions.map(([value]) => value))}>队列列全部显示</Button>
    </Space>
  )

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Tabs
        items={[
          {
            key: 'local-telemetry',
            label: '本机Telemetry数据',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Card>
                  <Space wrap>
                    <Select
                      showSearch
                      optionFilterProp="label"
                      style={{ minWidth: 460 }}
                      placeholder="选择已接入Telemetry的H3C设备"
                      value={telemetryDeviceId}
                      options={telemetryDevices.map((item) => ({
                        value: item.id,
                        label: `${item.name} / ${item.ip_address} / ${item.model || '-'}`,
                      }))}
                      onChange={setTelemetryDeviceId}
                    />
                    <Input
                      allowClear
                      prefix={<SearchOutlined />}
                      style={{ width: 220 }}
                      placeholder="筛选端口"
                      value={telemetrySearch}
                      onChange={(event) => setTelemetrySearch(event.target.value)}
                      onPressEnter={loadTelemetrySnapshot}
                    />
                    <Select
                      style={{ width: 130 }}
                      value={telemetryStatusFilter}
                      onChange={setTelemetryStatusFilter}
                      options={[
                        { value: 'all', label: '全部状态' },
                        { value: 'up', label: '仅运行Up' },
                        { value: 'down', label: '仅运行Down' },
                        { value: 'mismatch', label: '仅状态不一致' },
                      ]}
                    />
                    {telemetryViewMode === 'table' ? (
                      <>
                        <Select
                          style={{ width: 150 }}
                          value={telemetrySortKey}
                          onChange={setTelemetrySortKey}
                          options={[
                            { value: 'interface_name', label: '按端口排序' },
                            { value: 'speed_bps', label: '按端口速率' },
                            { value: 'in_utilization_percent', label: '按入向使用率' },
                            { value: 'out_utilization_percent', label: '按出向使用率' },
                            { value: 'in_bps', label: '按入向流速' },
                            { value: 'out_bps', label: '按出向流速' },
                            { value: 'ecn_marked_packets', label: '按ECN标记' },
                            { value: 'wred_dropped_packets', label: '按WRED丢弃' },
                          ]}
                        />
                        <Select
                          style={{ width: 100 }}
                          value={telemetrySortOrder}
                          onChange={setTelemetrySortOrder}
                          options={[{ value: 'asc', label: '升序' }, { value: 'desc', label: '降序' }]}
                        />
                      </>
                    ) : null}
                    <Segmented
                      value={telemetryViewMode}
                      onChange={(value) => setTelemetryViewMode(value as 'port-map' | 'table')}
                      options={[
                        { value: 'port-map', label: '端口图', icon: <AppstoreOutlined /> },
                        { value: 'table', label: '详细表格', icon: <UnorderedListOutlined /> },
                      ]}
                    />
                    {telemetryViewMode === 'port-map' ? (
                      <Select
                        showSearch
                        allowClear
                        optionFilterProp="label"
                        style={{ minWidth: 300 }}
                        placeholder="选择端口查看全部指标"
                        value={telemetryDetailPortName}
                        options={(telemetrySnapshot.ports || []).map((row: any) => ({
                          value: row.interface_name,
                          label: `${row.interface_name || '-'} / ${(row.oper_status || 'unknown').toUpperCase()} / ${formatSpeed(row.speed_bps)}`,
                        }))}
                        onChange={(value) => {
                          if (value) openTelemetryPortDetail(value)
                          else setTelemetryDetailPortName(undefined)
                        }}
                      />
                    ) : null}
                    {telemetryViewMode === 'table' ? (
                      <Popover trigger="click" placement="bottomRight" content={columnVisibilityContent}>
                        <Button icon={<EyeOutlined />}>隐藏/显示列</Button>
                      </Popover>
                    ) : null}
                    <Button onClick={() => {
                      setTelemetrySearch('')
                      setTelemetryStatusFilter('all')
                      setTelemetrySortKey('interface_name')
                      setTelemetrySortOrder('asc')
                    }}>重置筛选</Button>
                    <Button icon={<ReloadOutlined />} onClick={loadTelemetrySnapshot} loading={telemetryLoading}>刷新数据</Button>
                    <Tag color="green">已接入 {telemetryDevices.length} 台</Tag>
                    {telemetrySnapshot.collected_at && <Text type="secondary">采集时间：{new Date(telemetrySnapshot.collected_at).toLocaleString()}</Text>}
                  </Space>
                </Card>
                <Alert
                  type="info"
                  showIcon
                  message="数据直接来自本平台接收的 H3C Telemetry"
                  description="当前展示设备上报的真实值。ECN/WRED/Pause 当前为累计计数；速率需要连续采样计算。光功率不在本批无损路径中，未用其他字段代替。QSTAT方向暂保留厂商原始0/1枚举。"
                />
                <Card title={telemetryViewMode === 'port-map'
                  ? `Grafana 端口状态图（共 ${telemetrySnapshot.total || 0}）`
                  : `端口实时指标（筛选后 ${telemetryPorts.length} / 共 ${telemetrySnapshot.total || 0}）`}>
                  {telemetryViewMode === 'port-map' ? (
                    buildLosslessPortMapUrl(telemetrySnapshot, telemetryPortMapSearch, telemetryStatusFilter) ? (
                      <iframe
                        key={`${telemetrySnapshot.device?.id || telemetryDeviceId}-${telemetryPortMapSearch}-${telemetryStatusFilter}-port-map`}
                        title={`${telemetrySnapshot.device?.name || '设备'}端口状态与带宽`}
                        src={buildLosslessPortMapUrl(telemetrySnapshot, telemetryPortMapSearch, telemetryStatusFilter)}
                        style={{ width: '100%', height: 720, border: 0, display: 'block' }}
                        allowFullScreen
                      />
                    ) : (
                      <Alert type="info" showIcon message="请选择设备，等待端口状态数据加载" />
                    )
                  ) : (
                    <Table<any>
                    size="small"
                    rowKey={(row) => `${row.interface_index}-${row.interface_name}`}
                    loading={telemetryLoading}
                    dataSource={telemetryPorts}
                    scroll={{ x: 'max-content', y: 620 }}
                    pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
                    expandable={{
                      rowExpandable: (row) => Boolean(row.queues?.length),
                      expandedRowRender: (row) => (
                        <Table<any>
                          size="small"
                          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
                          rowKey={(queue) => `${row.interface_name}-${queue.queue_id}`}
                          dataSource={row.queues || []}
                          scroll={{ x: 'max-content' }}
                          columns={sortableVisibleColumns([
                            { title: '队列', dataIndex: 'queue_id', width: 70, fixed: 'left' },
                            { title: '出向单播流速', dataIndex: 'queue_out_unicast_bps', width: 140, render: (v: any) => formatRate(v, 'bps') },
                            { title: '出向单播包速', dataIndex: 'queue_out_unicast_pps', width: 140, render: (v: any) => formatRate(v, 'pps') },
                            { title: '入Buffer使用量', dataIndex: 'ingress_buffer_used', width: 140, render: displayNumber },
                            { title: '出单播Buffer', dataIndex: 'egress_unicast_buffer_used', width: 140, render: displayNumber },
                            { title: '出组播Buffer', dataIndex: 'egress_multicast_buffer_used', width: 140, render: displayNumber },
                            { title: 'Headroom使用量', dataIndex: 'headroom_used', width: 150, render: displayNumber },
                            { title: '入向区间峰值', dataIndex: 'ingress_interval_peak', width: 140, render: displayNumber },
                            { title: '出向区间峰值', dataIndex: 'egress_interval_peak', width: 140, render: displayNumber },
                            { title: '出向No Buffer丢包', dataIndex: 'queue_out_no_buffer_packets', width: 170, render: displayNumber },
                            { title: '出向No Buffer速率', dataIndex: 'queue_out_no_buffer_pps', width: 170, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC发送速率', dataIndex: 'queue_pfc_send_pps', width: 140, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC接收速率', dataIndex: 'queue_pfc_recv_pps', width: 140, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC发送累计', dataIndex: 'queue_pfc_send_packets', width: 130, render: displayNumber },
                            { title: 'PFC接收累计', dataIndex: 'queue_pfc_recv_packets', width: 130, render: displayNumber },
                            { title: '方向0队列峰值', dataIndex: 'direction_0_queue_peak_bytes', width: 150, render: displayNumber },
                            { title: '方向1队列峰值', dataIndex: 'direction_1_queue_peak_bytes', width: 150, render: displayNumber },
                          ], visibleQueueColumns)}
                        />
                      ),
                    }}
                    columns={sortableVisibleColumns([
                      { title: '端口', dataIndex: 'interface_name', width: 165, fixed: 'left', ellipsis: true },
                      { title: '速率', dataIndex: 'speed_bps', width: 110, render: formatSpeed },
                      { title: '运行状态', dataIndex: 'oper_status', width: 95, filters: [{ text: 'Up', value: 'up' }, { text: 'Down', value: 'down' }], onFilter: (value: any, row: any) => row.oper_status === value, render: (v: any) => <Tag color={v === 'up' ? 'green' : 'red'}>{v || '-'}</Tag> },
                      { title: '管理状态', dataIndex: 'admin_status', width: 95, filters: [{ text: 'Up', value: 'up' }, { text: 'Down', value: 'down' }], onFilter: (value: any, row: any) => row.admin_status === value, render: (v: any) => <Tag color={v === 'up' ? 'green' : 'default'}>{v || '-'}</Tag> },
                      { title: '状态一致', dataIndex: 'status_consistent', width: 95, filters: [{ text: '一致', value: 'true' }, { text: '不一致', value: 'false' }], onFilter: (value: any, row: any) => String(row.status_consistent) === value, render: (v: any) => v === undefined ? '-' : <Tag color={v ? 'green' : 'orange'}>{v ? '是' : '否'}</Tag> },
                      { title: '光功率', key: 'optical_power', width: 90, sorter: false, render: () => '-' },
                      { title: '入向使用率', dataIndex: 'in_utilization_percent', width: 120, render: (v: any) => v === undefined ? '-' : `${v}%` },
                      { title: '出向使用率', dataIndex: 'out_utilization_percent', width: 120, render: (v: any) => v === undefined ? '-' : `${v}%` },
                      { title: '入向流速', dataIndex: 'in_bps', width: 130, render: (v: any) => formatRate(v, 'bps') },
                      { title: '出向流速', dataIndex: 'out_bps', width: 130, render: (v: any) => formatRate(v, 'bps') },
                      { title: '入向包速', dataIndex: 'in_pps', width: 130, render: (v: any) => formatRate(v, 'pps') },
                      { title: '出向包速', dataIndex: 'out_pps', width: 130, render: (v: any) => formatRate(v, 'pps') },
                      { title: '入广播包速', dataIndex: 'in_broadcast_pps', width: 130, render: (v: any) => formatRate(v, 'pps') },
                      { title: '出广播包速', dataIndex: 'out_broadcast_pps', width: 130, render: (v: any) => formatRate(v, 'pps') },
                      { title: '入错误包', dataIndex: 'in_error_packets', width: 110, render: displayNumber },
                      { title: '入No Buffer丢包', dataIndex: 'in_no_buffer_packets', width: 150, render: displayNumber },
                      { title: '出No Buffer丢包', dataIndex: 'out_no_buffer_packets', width: 150, render: displayNumber },
                      { title: 'ECN标记累计', dataIndex: 'ecn_marked_packets', width: 130, render: displayNumber },
                      { title: 'WRED丢弃累计', dataIndex: 'wred_dropped_packets', width: 140, render: displayNumber },
                      { title: 'Pause接收累计', dataIndex: 'in_pause_packets', width: 140, render: displayNumber },
                      { title: 'Pause发送累计', dataIndex: 'out_pause_packets', width: 140, render: displayNumber },
                      { title: '队列', dataIndex: 'queues', width: 90, render: (v: any) => v?.length ? <Tag color="blue">{v.length}</Tag> : '-' },
                    ], visiblePortColumns)}
                    />
                  )}
                </Card>
                <Drawer
                  title={telemetryDetailPort
                    ? `${telemetrySnapshot.device?.ip_address || '-'} / ${telemetryDetailPort.interface_name} 完整实时指标`
                    : '端口完整实时指标'}
                  width="min(1180px, 92vw)"
                  open={telemetryDetailOpen}
                  onClose={() => setTelemetryDetailOpen(false)}
                  destroyOnClose={false}
                >
                  {telemetryDetailPort ? (
                    <Space direction="vertical" size={16} style={{ width: '100%' }}>
                      <Alert
                        type="info"
                        showIcon
                        message={`采集时间：${telemetrySnapshot.collected_at ? new Date(telemetrySnapshot.collected_at).toLocaleString() : '-'}`}
                        description="累计指标展示设备当前累计值；速率指标展示最近一次上报值。没有上报的字段保持为“-”。"
                      />
                      <Descriptions bordered size="small" column={4}>
                        <Descriptions.Item label="端口">{telemetryDetailPort.interface_name || '-'}</Descriptions.Item>
                        <Descriptions.Item label="端口速率">{formatSpeed(telemetryDetailPort.speed_bps)}</Descriptions.Item>
                        <Descriptions.Item label="运行状态"><Tag color={telemetryDetailPort.oper_status === 'up' ? 'green' : 'red'}>{String(telemetryDetailPort.oper_status || 'unknown').toUpperCase()}</Tag></Descriptions.Item>
                        <Descriptions.Item label="管理状态"><Tag color={telemetryDetailPort.admin_status === 'up' ? 'green' : 'default'}>{String(telemetryDetailPort.admin_status || 'unknown').toUpperCase()}</Tag></Descriptions.Item>
                        <Descriptions.Item label="入向使用率">{displayPercent(telemetryDetailPort.in_utilization_percent)}</Descriptions.Item>
                        <Descriptions.Item label="出向使用率">{displayPercent(telemetryDetailPort.out_utilization_percent)}</Descriptions.Item>
                        <Descriptions.Item label="入向流速">{formatRate(telemetryDetailPort.in_bps, 'bps')}</Descriptions.Item>
                        <Descriptions.Item label="出向流速">{formatRate(telemetryDetailPort.out_bps, 'bps')}</Descriptions.Item>
                        <Descriptions.Item label="入向包速">{formatRate(telemetryDetailPort.in_pps, 'pps')}</Descriptions.Item>
                        <Descriptions.Item label="出向包速">{formatRate(telemetryDetailPort.out_pps, 'pps')}</Descriptions.Item>
                        <Descriptions.Item label="入广播包速">{formatRate(telemetryDetailPort.in_broadcast_pps, 'pps')}</Descriptions.Item>
                        <Descriptions.Item label="出广播包速">{formatRate(telemetryDetailPort.out_broadcast_pps, 'pps')}</Descriptions.Item>
                        <Descriptions.Item label="入错误包">{displayNumber(telemetryDetailPort.in_error_packets)}</Descriptions.Item>
                        <Descriptions.Item label="入No Buffer丢包">{displayNumber(telemetryDetailPort.in_no_buffer_packets)}</Descriptions.Item>
                        <Descriptions.Item label="出No Buffer丢包">{displayNumber(telemetryDetailPort.out_no_buffer_packets)}</Descriptions.Item>
                        <Descriptions.Item label="状态一致">{telemetryDetailPort.status_consistent === undefined ? '-' : telemetryDetailPort.status_consistent ? '是' : '否'}</Descriptions.Item>
                        <Descriptions.Item label="ECN标记累计">{displayNumber(telemetryDetailPort.ecn_marked_packets)}</Descriptions.Item>
                        <Descriptions.Item label="WRED丢弃累计">{displayNumber(telemetryDetailPort.wred_dropped_packets)}</Descriptions.Item>
                        <Descriptions.Item label="Pause接收累计">{displayNumber(telemetryDetailPort.in_pause_packets)}</Descriptions.Item>
                        <Descriptions.Item label="Pause发送累计">{displayNumber(telemetryDetailPort.out_pause_packets)}</Descriptions.Item>
                      </Descriptions>
                      <Card size="small" title={`队列指标（${telemetryDetailPort.queues?.length || 0} 个队列）`}>
                        <Table<any>
                          size="small"
                          rowKey={(queue) => `${telemetryDetailPort.interface_name}-${queue.queue_id}`}
                          dataSource={telemetryDetailPort.queues || []}
                          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
                          scroll={{ x: 'max-content', y: 480 }}
                          columns={[
                            { title: '队列', dataIndex: 'queue_id', width: 70, fixed: 'left' },
                            { title: '出向单播流速', dataIndex: 'queue_out_unicast_bps', width: 145, render: (v: any) => formatRate(v, 'bps') },
                            { title: '出向单播包速', dataIndex: 'queue_out_unicast_pps', width: 145, render: (v: any) => formatRate(v, 'pps') },
                            { title: '入Buffer使用量', dataIndex: 'ingress_buffer_used', width: 145, render: displayNumber },
                            { title: '出单播Buffer', dataIndex: 'egress_unicast_buffer_used', width: 140, render: displayNumber },
                            { title: '出组播Buffer', dataIndex: 'egress_multicast_buffer_used', width: 140, render: displayNumber },
                            { title: 'Headroom使用量', dataIndex: 'headroom_used', width: 150, render: displayNumber },
                            { title: '入向区间峰值', dataIndex: 'ingress_interval_peak', width: 145, render: displayNumber },
                            { title: '出向区间峰值', dataIndex: 'egress_interval_peak', width: 145, render: displayNumber },
                            { title: '出向No Buffer丢包', dataIndex: 'queue_out_no_buffer_packets', width: 175, render: displayNumber },
                            { title: '出向No Buffer速率', dataIndex: 'queue_out_no_buffer_pps', width: 175, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC发送速率', dataIndex: 'queue_pfc_send_pps', width: 145, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC接收速率', dataIndex: 'queue_pfc_recv_pps', width: 145, render: (v: any) => formatRate(v, 'pps') },
                            { title: 'PFC发送累计', dataIndex: 'queue_pfc_send_packets', width: 135, render: displayNumber },
                            { title: 'PFC接收累计', dataIndex: 'queue_pfc_recv_packets', width: 135, render: displayNumber },
                            { title: '方向0队列峰值', dataIndex: 'direction_0_queue_peak_bytes', width: 155, render: displayNumber },
                            { title: '方向1队列峰值', dataIndex: 'direction_1_queue_peak_bytes', width: 155, render: displayNumber },
                          ]}
                          locale={{ emptyText: '该端口当前没有队列级数据' }}
                        />
                      </Card>
                    </Space>
                  ) : <Alert type="info" showIcon message="请选择一个端口" />}
                </Drawer>
              </Space>
            ),
          },
          {
            key: 'telemetry-plan',
            label: 'Telemetry 接入规划',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Alert
                  type="info"
                  showIcon
                  message="这些是当前已经从交换机 Telemetry 收到、但还没有全部解析到无损页面的能力项。"
                  description="后续可以按优先级逐步落表：先做 PFC / Buffer / Queue / ECN / WRED，再接入无损告警，最后补充 FEC/BER、MQC/QoS 等专题信息。"
                />
                <Card title="待接入无损 Telemetry 数据源">
                  <Table
                    rowKey={(record) => record.category}
                    dataSource={actualCapabilities}
                    pagination={false}
                    scroll={{ x: 1200 }}
                    columns={[
                      {
                        title: '类别',
                        dataIndex: 'category',
                        width: 140,
                        fixed: 'left',
                        render: (value) => <Text strong>{value}</Text>,
                      },
                      { title: '可展示指标', dataIndex: 'metrics', width: 320 },
                      {
                        title: 'Telemetry Path',
                        dataIndex: 'paths',
                        width: 360,
                        render: (value) => <Text code style={{ whiteSpace: 'normal' }}>{value}</Text>,
                      },
                      { title: '建议刷新', dataIndex: 'refresh', width: 120 },
                      {
                        title: '状态',
                        dataIndex: 'status',
                        width: 170,
                        render: (value) => <Tag color={statusColorMap[value] || 'default'}>{value}</Tag>,
                      },
                      {
                        title: '优先级',
                        dataIndex: 'priority',
                        width: 90,
                        render: (value) => <Tag color={value === '高' ? 'red' : 'gold'}>{value}</Tag>,
                      },
                    ]}
                  />
                </Card>
                <Card title="建议落地顺序">
                  <Space direction="vertical" size={8}>
                    <Text>1. 先把 PFC、Buffer、Queue、ECN/WRED 做成端口 + 队列维度表格，支持设备/IP/接口/队列筛选。</Text>
                    <Text>2. 将 Queue Drop、PFC Deadlock、Buffer Overrun 这类事件接入告警中心，减少 SNMP 轮询告警压力。</Text>
                    <Text>3. 把 BER / ESNR / FEC 与模块信息查询联动，用于判断 400G 链路质量。</Text>
                    <Text>4. MQC / QoS 暂作为专题页，后续用于分析策略命中、丢包和 Remark 情况。</Text>
                  </Space>
                </Card>
              </Space>
            ),
          },
        ]}
      />
    </Space>
  )
}

export default LosslessInfoQuery
