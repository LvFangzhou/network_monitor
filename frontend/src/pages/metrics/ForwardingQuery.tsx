import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Spin, Statistic, Table, Tabs, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  getForwardingArp,
  getForwardingDevices,
  getForwardingHistory,
  getForwardingRoutes,
  getForwardingSummary,
  refreshForwardingDevice,
  type ForwardingDevice,
} from '../../api/metrics'

const { Text, Title } = Typography

const timeText = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const routeProtocolText = (row: any) => {
  const cliProtocol = String(row.protocol || row.flags || '').replace(/[^A-Za-z]/g, '').toUpperCase()
  const cliNames: Record<string, string> = {
    C: '直连', K: '内核', S: '静态', O: 'OSPF', B: 'BGP', R: 'RIP', I: 'IS-IS',
  }
  if (cliNames[cliProtocol]) return cliNames[cliProtocol]
  const protocolNames: Record<number, string> = {
    1: '其他', 2: '直连', 3: '静态/网管', 4: 'ICMP', 5: 'EGP', 7: 'Hello',
    8: 'RIP', 9: 'IS-IS', 13: 'OSPF', 14: 'BGP', 16: 'EIGRP', 17: 'DVMRP',
  }
  const protocolId = Number(row.protocol_id)
  return protocolNames[protocolId] || '-'
}

const textCompare = (left: any, right: any) => String(left ?? '').localeCompare(
  String(right ?? ''),
  'zh-CN',
  { numeric: true, sensitivity: 'base' },
)

const columnSelectFilter = (rows: any[], valueGetter: (row: any) => any) => {
  const values = Array.from(new Set(
    rows.map((row) => String(valueGetter(row) ?? '').trim()).filter(Boolean),
  )).sort(textCompare)
  return {
    filters: values.map((value) => ({ text: value, value })),
    filterSearch: true,
    filterMultiple: true,
    onFilter: (value: any, row: any) => String(valueGetter(row) ?? '') === String(value),
  }
}

interface ForwardingQueryProps {
  fixedDeviceId?: number
  embedded?: boolean
}

const ForwardingQuery = ({ fixedDeviceId, embedded = false }: ForwardingQueryProps) => {
  const [devices, setDevices] = useState<ForwardingDevice[]>([])
  const [selectedDeviceId, setSelectedDeviceId] = useState<number>()
  const [activeTab, setActiveTab] = useState('arp')
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [summary, setSummary] = useState<any>({ tables: {} })
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [history, setHistory] = useState<any[]>([])
  const loadRequestRef = useRef(0)

  const deviceId = fixedDeviceId || selectedDeviceId
  const selectedDevice = useMemo(() => devices.find((item) => item.id === deviceId), [deviceId, devices])

  const loadDevices = async () => {
    if (fixedDeviceId) return
    try {
      const result = await getForwardingDevices()
      setDevices(result.items || [])
      setSelectedDeviceId((current) => current || result.items?.[0]?.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取转发表设备失败')
    }
  }

  const loadData = async (search = keyword, tab = activeTab) => {
    if (!deviceId) return
    const requestId = ++loadRequestRef.current
    setLoading(true)
    try {
      const tableName = tab === 'ipv4' ? 'ipv4_routes' : 'arp'
      const [summaryResult, tableResult, historyResult] = await Promise.all([
        getForwardingSummary(deviceId),
        tab === 'arp'
          ? getForwardingArp(deviceId, { search: search || undefined, limit: 1000 })
          : getForwardingRoutes(deviceId, { search: search || undefined, group_prefix: true, limit: 1000 }),
        getForwardingHistory(deviceId, { table: tableName, range: '-24h', interval: '5m' }),
      ])
      if (requestId !== loadRequestRef.current) return
      setSummary(summaryResult || { tables: {} })
      setRows(tableResult.items || [])
      setTotal(tableResult.total || 0)
      setHistory(historyResult.data || [])
    } catch (error: any) {
      if (requestId === loadRequestRef.current) {
        message.error(error?.response?.data?.detail || '读取转发表数据失败')
      }
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false)
    }
  }

  const refreshLive = async () => {
    if (!deviceId || refreshing) return
    const before = [summary.tables?.arp?.collected_at, summary.tables?.ipv4_routes?.collected_at]
      .filter(Boolean).sort().pop() || ''
    setRefreshing(true)
    try {
      await refreshForwardingDevice(deviceId)
      message.info('已提交实时采集，完成后会自动刷新')
      const deadline = Date.now() + 180_000
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 3000))
        const next = await getForwardingSummary(deviceId)
        const latest = [next.tables?.arp?.collected_at, next.tables?.ipv4_routes?.collected_at]
          .filter(Boolean).sort().pop() || ''
        if (latest && latest !== before) {
          await loadData()
          message.success('转发表实时采集完成')
          return
        }
      }
      message.warning('采集仍在后台执行，可稍后再次查看')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '提交实时采集失败')
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => { void loadDevices() }, [fixedDeviceId])
  useEffect(() => {
    if (!deviceId) return
    setRows([])
    setTotal(0)
    void loadData('', activeTab)
  }, [deviceId, activeTab])

  const currentTable = activeTab === 'ipv4' ? summary.tables?.ipv4_routes : summary.tables?.arp
  const currentSummary = currentTable?.summary || {}

  const arpColumns = [
    { title: 'IP地址', dataIndex: 'ip_address', width: 150, sorter: (a: any, b: any) => textCompare(a.ip_address, b.ip_address), ...columnSelectFilter(rows, (row) => row.ip_address) },
    { title: 'MAC地址', dataIndex: 'mac_address', width: 170, sorter: (a: any, b: any) => textCompare(a.mac_address, b.mac_address), ...columnSelectFilter(rows, (row) => row.mac_address) },
    { title: '接口', dataIndex: 'interface', width: 220, ellipsis: true, sorter: (a: any, b: any) => textCompare(a.interface, b.interface), ...columnSelectFilter(rows, (row) => row.interface) },
    { title: 'VRF索引', dataIndex: 'vrf_index', width: 90, sorter: (a: any, b: any) => Number(a.vrf_index || 0) - Number(b.vrf_index || 0), ...columnSelectFilter(rows, (row) => row.vrf_index) },
    { title: 'ARP类型', dataIndex: 'arp_type', width: 90, sorter: (a: any, b: any) => Number(a.arp_type || 0) - Number(b.arp_type || 0), ...columnSelectFilter(rows, (row) => Number(row.arp_type) < 0 ? '-' : `类型 ${row.arp_type}`), render: (value: number) => <Tag>{value < 0 ? '-' : `类型 ${value}`}</Tag> },
    { title: '状态', dataIndex: 'state', width: 100, sorter: (a: any, b: any) => textCompare(a.state || (a.mac_address ? '已解析' : 'Incomplete'), b.state || (b.mac_address ? '已解析' : 'Incomplete')), ...columnSelectFilter(rows, (row) => row.state || (row.mac_address ? '已解析' : 'Incomplete')), render: (value: string, record: any) => value || (record.mac_address ? <Tag color="green">已解析</Tag> : <Tag color="red">Incomplete</Tag>) },
  ]

  const routeColumns = [
    { title: 'VRF', dataIndex: 'vrf', width: 120, ellipsis: true, sorter: (a: any, b: any) => textCompare(a.vrf || 'default', b.vrf || 'default'), ...columnSelectFilter(rows, (row) => row.vrf || 'default'), render: (value: string) => value || 'default' },
    { title: 'Prefix', dataIndex: 'prefix', width: 190, sorter: (a: any, b: any) => textCompare(a.prefix, b.prefix), ...columnSelectFilter(rows, (row) => row.prefix) },
    {
      title: '下一跳',
      dataIndex: 'next_hop',
      width: 190,
      sorter: (a: any, b: any) => textCompare(a.next_hop, b.next_hop),
      ...columnSelectFilter(rows, (row) => row.next_hop || '直连'),
      render: (_: string, row: any) => {
        const hops = row.next_hops || [{ next_hop: row.next_hop }]
        const firstHop = hops[0]?.next_hop
        return <span>{!firstHop || firstHop === '0.0.0.0' ? '直连' : firstHop}{hops.length > 1 ? `（共 ${hops.length} 条）` : ''}</span>
      },
    },
    {
      title: '出接口',
      dataIndex: 'interface',
      width: 230,
      sorter: (a: any, b: any) => textCompare(a.interface, b.interface),
      ...columnSelectFilter(rows, (row) => row.interface),
      render: (_: string, row: any) => {
        const hops = row.next_hops || [{ interface: row.interface }]
        return <span>{hops[0]?.interface || '-'}{hops.length > 1 ? '（展开查看全部）' : ''}</span>
      },
    },
    { title: '路由来源', width: 100, sorter: (a: any, b: any) => textCompare(routeProtocolText(a), routeProtocolText(b)), ...columnSelectFilter(rows, routeProtocolText), render: (_: any, row: any) => routeProtocolText(row) },
    { title: 'ECMP', dataIndex: 'ecmp_count', width: 75, sorter: (a: any, b: any) => Number(a.ecmp_count || 0) - Number(b.ecmp_count || 0), ...columnSelectFilter(rows, (row) => Number(row.ecmp_count || 0) > 1 ? row.ecmp_count : '-'), render: (value: number) => Number(value || 0) > 1 ? value : '-' },
    { title: '优先级', dataIndex: 'preference', width: 90, sorter: (a: any, b: any) => Number(a.preference || 0) - Number(b.preference || 0), ...columnSelectFilter(rows, (row) => Number(row.preference) > 0 ? row.preference : '-'), render: (value: number) => Number(value) > 0 ? value : '-' },
    { title: 'Metric', dataIndex: 'metric', width: 80, sorter: (a: any, b: any) => Number(a.metric || 0) - Number(b.metric || 0), ...columnSelectFilter(rows, (row) => Number(row.metric) >= 0 ? row.metric : '-'), render: (value: number) => Number(value) >= 0 ? value : '-' },
    { title: '黑洞', dataIndex: 'blackhole', width: 75, sorter: (a: any, b: any) => Number(Boolean(a.blackhole)) - Number(Boolean(b.blackhole)), ...columnSelectFilter(rows, (row) => row.blackhole ? '是' : '否'), render: (value: boolean) => value ? <Tag color="red">是</Tag> : '-' },
  ]

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {!embedded ? <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 转发表查询</div> : null}
      <Card>
        {!embedded ? (
          <Space direction="vertical" size={0} style={{ marginBottom: 14 }}>
            <Title level={4} style={{ margin: 0 }}>ARP与FIB查询</Title>
            <Text type="secondary">明细优先读取12小时缓存；H3C、锐捷、山石使用SNMP，AsterNOS使用Exporter汇总与CLI明细。</Text>
          </Space>
        ) : null}
        <Space wrap>
          {!fixedDeviceId ? (
            <Select
              showSearch
              optionFilterProp="label"
              value={deviceId}
              style={{ width: 430 }}
              placeholder="选择已收到ARP/FIB数据的设备"
              options={devices.map((item) => ({ label: `${item.ip_address} / ${item.name}`, value: item.id }))}
              onChange={setSelectedDeviceId}
            />
          ) : null}
          <Input.Search
            allowClear
            value={keyword}
            style={{ width: 360 }}
            placeholder={activeTab === 'arp' ? '搜索IP、MAC或接口' : '输入目的IP可执行最长掩码匹配，也可搜索Prefix/下一跳/接口'}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => void loadData(value)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void refreshLive()} loading={refreshing}>手动刷新</Button>
        </Space>
      </Card>

      <Spin spinning={loading}>
        {!currentTable?.received ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="info"
            showIcon
            message="当前设备还没有转发表缓存"
            description="点击“手动刷新”会从设备实时读取；后台也会在每天 00:30 和 12:30 分批预热。"
          />
        ) : currentTable?.message ? (
          <Alert
            style={{ marginBottom: 12 }}
            type="success"
            showIcon
            message={`数据来源：${currentTable.source || '-'}`}
            description={currentTable.message}
          />
        ) : null}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
          <Card size="small"><Statistic title="当前条目" value={currentSummary.total || 0} /></Card>
          {activeTab === 'arp' ? (
            <>
              <Card size="small"><Statistic title="Incomplete" value={currentSummary.incomplete || 0} valueStyle={{ color: currentSummary.incomplete ? '#cf1322' : undefined }} /></Card>
              <Card size="small"><Statistic title="本轮新增" value={currentSummary.added || 0} /></Card>
              <Card size="small"><Statistic title="本轮删除" value={currentSummary.removed || 0} /></Card>
              <Card size="small"><Statistic title="MAC变化" value={currentSummary.mac_changed || 0} valueStyle={{ color: currentSummary.mac_changed ? '#d46b08' : undefined }} /></Card>
            </>
          ) : (
            <>
              <Card size="small"><Statistic title="Prefix数量" value={currentSummary.prefix_total || 0} /></Card>
              <Card size="small"><Statistic title="ECMP Prefix" value={currentSummary.ecmp_prefixes || 0} /></Card>
              <Card size="small"><Statistic title="本轮删除" value={currentSummary.removed || 0} valueStyle={{ color: currentSummary.removed ? '#d46b08' : undefined }} /></Card>
              <Card size="small"><Statistic title="黑洞路由" value={currentSummary.blackhole_routes || 0} /></Card>
            </>
          )}
        </div>

        <Card style={{ marginTop: 12 }}>
          <Tabs
            activeKey={activeTab}
            onChange={(value) => { setKeyword(''); setActiveTab(value) }}
            items={[
              { key: 'arp', label: 'ARP邻居表' },
              { key: 'ipv4', label: 'IPv4 FIB/路由' },
            ]}
          />
          <Text type="secondary">
            {selectedDevice ? `${selectedDevice.name}（${selectedDevice.ip_address}） · ` : ''}最近采集 {timeText(currentTable?.collected_at)} · 当前筛选 {total} 条
          </Text>
          <Table
            key={activeTab}
            style={{ marginTop: 10 }}
            size="small"
            rowKey={(row) => activeTab === 'arp' ? `${row.vrf_index}-${row.ip_address}-${row.mac_address}` : `${row.vrf}-${row.prefix}-${row.next_hop}-${row.interface}`}
            columns={activeTab === 'arp' ? arpColumns : routeColumns}
            dataSource={rows}
            expandable={activeTab === 'arp' ? undefined : {
              rowExpandable: (row: any) => (row.next_hops || []).length > 1,
              expandedRowRender: (row: any) => (
                <Table
                  size="small"
                  rowKey={(hop: any, index?: number) => `${hop.next_hop}-${hop.interface}-${index || 0}`}
                  pagination={false}
                  dataSource={row.next_hops || []}
                  columns={[
                    { title: '下一跳', dataIndex: 'next_hop', width: 190, render: (value: string) => !value || value === '0.0.0.0' ? '直连' : value },
                    { title: '出接口', dataIndex: 'interface', width: 240, render: (value: string) => value || '-' },
                    { title: 'Preference', dataIndex: 'preference', width: 110 },
                    { title: 'Metric', dataIndex: 'metric', width: 90 },
                  ]}
                />
              ),
            }}
            scroll={{ x: activeTab === 'arp' ? 850 : 1350 }}
            pagination={{ showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], defaultPageSize: 50, showTotal: (value) => `共 ${value} 条` }}
          />
        </Card>

        <Card title="过去24小时数量趋势" style={{ marginTop: 12 }}>
          {history.length ? (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={history} margin={{ top: 10, right: 24, bottom: 8, left: 12 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" tickFormatter={(value) => timeText(value).slice(5, 16)} minTickGap={36} />
                <YAxis allowDecimals={false} />
                <Tooltip labelFormatter={timeText} />
                <Legend />
                <Line type="monotone" dataKey="total" name="条目数量" stroke="#1677ff" dot={false} connectNulls />
                {activeTab !== 'arp' ? <Line type="monotone" dataKey="prefix_total" name="Prefix数量" stroke="#52c41a" dot={false} connectNulls /> : null}
              </LineChart>
            </ResponsiveContainer>
          ) : <Text type="secondary">历史趋势将在后续采集写入后出现。</Text>}
        </Card>
      </Spin>
    </Space>
  )
}

export default ForwardingQuery
