import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  getControllerAssets,
  getControllerOptions,
  getLosslessBufferDetails,
  getLosslessOverrunDevices,
  type ControllerOption,
} from '../../api/controller'

const { Text } = Typography
const LOSSLESS_REFRESH_INTERVAL_MS = 120 * 1000

const hourOptions = [
  { value: 3, label: '最近3小时' },
  { value: 24, label: '最近24小时' },
  { value: 72, label: '最近3天' },
  { value: 168, label: '最近7天' },
]

const sortOptions = [
  { value: 'outDroppedPkts', label: '出方向丢弃包' },
  { value: 'inDroppedPkts', label: '入方向丢弃包' },
  { value: 'ecnMarkedCount', label: 'ECN标记' },
  { value: 'wredDroppedCount', label: 'WRED丢弃' },
  { value: 'pfcRecv', label: 'PFC接收' },
  { value: 'pfcSend', label: 'PFC发送' },
  { value: 'egressOverrunCounters', label: '出Buffer超限' },
  { value: 'ingressOverrunCounters', label: '入Buffer超限' },
  { value: 'headroomOverrunCounters', label: 'Headroom超限' },
]

const telemetryLosslessCapabilities = [
  {
    category: 'PFC',
    metrics: 'PFC TX/RX、Pause 帧、PFC No-drop、PFC Deadlock',
    paths: 'pfcstatistics / pfcspeeds / pfcports/port / portnodrops / portdeadlocks',
    refresh: '60秒 / 事件',
    status: '已收到，待结构化展示',
    priority: '高',
  },
  {
    category: 'Buffer',
    metrics: '端口/队列入向、出向、共享 Buffer、Headroom 使用量与超限次数',
    paths: 'commbufferusages / commheadroomusages / ingressdrops / egressdrops',
    refresh: '60秒',
    status: '已收到，待结构化展示',
    priority: '高',
  },
  {
    category: 'Queue',
    metrics: '队列深度、队列使用率、队列丢包、队列长度',
    paths: 'qstat/queuestat / qos/interfaces/interface/input/queues/queue/state',
    refresh: '60秒',
    status: '已收到，待结构化展示',
    priority: '高',
  },
  {
    category: 'ECN / WRED',
    metrics: 'ECN 标记速率、WRED 丢弃速率、Tail Drop',
    paths: 'ecnandwredstatistics / wred/ifqueuewreds / dropparameters',
    refresh: '60秒',
    status: '已收到，待结构化展示',
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
}

const LosslessInfoQuery = () => {
  const [controllers, setControllers] = useState<ControllerOption[]>([])
  const [controllerId, setControllerId] = useState<string>()
  const [hours, setHours] = useState(3)
  const [overrunLoading, setOverrunLoading] = useState(false)
  const [overrunItems, setOverrunItems] = useState<any[]>([])
  const [assetSearch, setAssetSearch] = useState('')
  const [assetOptions, setAssetOptions] = useState<any[]>([])
  const [assetId, setAssetId] = useState<string>()
  const [assetLoading, setAssetLoading] = useState(false)
  const [bufferLoading, setBufferLoading] = useState(false)
  const [bufferItems, setBufferItems] = useState<any[]>([])
  const [bufferTotal, setBufferTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [ifIndex, setIfIndex] = useState('')
  const [sortColumn, setSortColumn] = useState('outDroppedPkts')

  const controllerOptions = useMemo(
    () => controllers.map((item) => ({ value: item.id, label: `${item.name}（${item.base_url}）` })),
    [controllers],
  )

  const loadControllers = async () => {
    try {
      const result = await getControllerOptions()
      setControllers(result.items || [])
      setControllerId((current) => current || result.items?.[0]?.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取控制器列表失败')
    }
  }

  const loadOverrunDevices = async () => {
    setOverrunLoading(true)
    try {
      const result = await getLosslessOverrunDevices({ controller_id: controllerId, hours, tag: hours <= 3 ? '3h' : 'custom' })
      setOverrunItems(result.items || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '查询拥塞设备失败')
    } finally {
      setOverrunLoading(false)
    }
  }

  const searchAssets = async () => {
    setAssetLoading(true)
    try {
      const result = await getControllerAssets({ controller_id: controllerId, page: 1, page_size: 20, search: assetSearch.trim() || undefined })
      setAssetOptions((result.items || []).map((item) => ({
        value: item.id,
        label: `${item.name || '-'} / ${item.ip || '-'} / ${item.model || '-'}`,
        raw: item,
      })))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '查询控制器资产失败')
    } finally {
      setAssetLoading(false)
    }
  }

  const loadBufferDetails = async (nextPage = page, nextPageSize = pageSize) => {
    if (!assetId) {
      message.warning('请先选择一个控制器资产')
      return
    }
    setBufferLoading(true)
    try {
      const result = await getLosslessBufferDetails({
        controller_id: controllerId,
        asset_id: assetId,
        page: nextPage,
        page_size: nextPageSize,
        hours,
        if_index: ifIndex.trim() || undefined,
        sort_column: sortColumn,
        order_type: 'desc',
      })
      setBufferItems(result.items || [])
      setBufferTotal(result.total || 0)
      setPage(nextPage)
      setPageSize(nextPageSize)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '查询Buffer/队列详情失败')
    } finally {
      setBufferLoading(false)
    }
  }

  useEffect(() => {
    loadControllers()
  }, [])

  useEffect(() => {
    if (controllerId) {
      loadOverrunDevices()
      searchAssets()
    }
  }, [controllerId])

  useEffect(() => {
    if (!controllerId) return undefined
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      loadOverrunDevices()
      if (assetId) {
        loadBufferDetails(page, pageSize)
      }
    }, LOSSLESS_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [controllerId, hours, assetId, page, pageSize, ifIndex, sortColumn])

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space wrap>
          <Select
            style={{ minWidth: 320 }}
            placeholder="选择控制器"
            value={controllerId}
            options={controllerOptions}
            onChange={setControllerId}
          />
          <Select style={{ width: 130 }} value={hours} options={hourOptions} onChange={setHours} />
          <Tag color="blue">每120秒自动刷新</Tag>
          <Button icon={<ReloadOutlined />} onClick={loadOverrunDevices} disabled={!controllerId}>
            刷新拥塞概览
          </Button>
        </Space>
      </Card>

      <Tabs
        items={[
          {
            key: 'overrun',
            label: '拥塞设备概览',
            children: (
              <Card title={`拥塞设备概览（${overrunItems.length} 台）`}>
                <Table
                  rowKey={(record) => record.ip || record.name}
                  loading={overrunLoading}
                  dataSource={overrunItems}
                  pagination={{ pageSize: 20, showSizeChanger: true }}
                  columns={[
                    { title: '设备名称', dataIndex: 'name', render: (value) => value || '-' },
                    { title: '设备IP', dataIndex: 'ip', width: 180 },
                    { title: '状态', width: 120, render: () => <Tag color="orange">存在拥塞</Tag> },
                  ]}
                  locale={{ emptyText: '当前时间范围内没有控制器返回的拥塞设备' }}
                />
              </Card>
            ),
          },
          {
            key: 'buffer',
            label: '端口/队列详情',
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Card>
                  <Space wrap>
                    <Input
                      allowClear
                      prefix={<SearchOutlined />}
                      placeholder="搜索资产名称/IP/型号"
                      style={{ width: 260 }}
                      value={assetSearch}
                      onChange={(event) => setAssetSearch(event.target.value)}
                      onPressEnter={searchAssets}
                    />
                    <Button onClick={searchAssets} loading={assetLoading}>搜索资产</Button>
                    <Select
                      showSearch
                      filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
                      style={{ minWidth: 420 }}
                      placeholder="选择控制器资产"
                      value={assetId}
                      options={assetOptions}
                      onChange={setAssetId}
                    />
                    <Input
                      allowClear
                      placeholder="端口名称，可选"
                      style={{ width: 180 }}
                      value={ifIndex}
                      onChange={(event) => setIfIndex(event.target.value)}
                      onPressEnter={() => loadBufferDetails(1, pageSize)}
                    />
                    <Select style={{ width: 160 }} value={sortColumn} options={sortOptions} onChange={setSortColumn} />
                    <Button type="primary" onClick={() => loadBufferDetails(1, pageSize)}>查询详情</Button>
                  </Space>
                </Card>
                <Card title={`端口/队列 Buffer 详情${bufferTotal ? `（共 ${bufferTotal} 条）` : ''}`}>
                  <Table
                    rowKey={(record) => record.ifIndex}
                    loading={bufferLoading}
                    dataSource={bufferItems}
                    scroll={{ x: 1700 }}
                    expandable={{
                      expandedRowRender: (record) => (
                        <Table<any>
                          size="small"
                          rowKey={(queue) => `${record.ifIndex}-${queue.queName}`}
                          dataSource={record.queInfoList || []}
                          pagination={false}
                          columns={[
                            { title: '队列', dataIndex: 'queName', width: 80 },
                            { title: '入Buffer使用率', dataIndex: 'ingressUsed' },
                            { title: '单播Buffer使用率', dataIndex: 'unicastUsed' },
                            { title: '组播Buffer使用率', dataIndex: 'mutilcastUsed' },
                            { title: 'Headroom使用率', dataIndex: 'headroomUsages' },
                            { title: '出丢弃包', dataIndex: 'outDroppedPkts' },
                            { title: 'PFC接收速率', dataIndex: 'pfcInPps' },
                            { title: 'PFC发送速率', dataIndex: 'pfcOutPps' },
                          ]}
                        />
                      ),
                    }}
                    pagination={{
                      current: page,
                      pageSize,
                      total: bufferTotal,
                      showSizeChanger: true,
                      pageSizeOptions: [10, 20, 50, 100],
                      showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
                      onChange: loadBufferDetails,
                    }}
                    columns={[
                      { title: '端口', dataIndex: 'ifIndex', width: 180, fixed: 'left' },
                      { title: 'ECN标记', dataIndex: 'ecnMarkedCount', width: 120 },
                      { title: 'WRED丢弃', dataIndex: 'wredDroppedCount', width: 120 },
                      { title: '入丢弃包', dataIndex: 'inDroppedPkts', width: 120 },
                      { title: '出丢弃包', dataIndex: 'outDroppedPkts', width: 120 },
                      { title: 'PFC接收', dataIndex: 'pfcRecv', width: 120 },
                      { title: 'PFC发送', dataIndex: 'pfcSend', width: 120 },
                      { title: '入Buffer最大使用率', dataIndex: 'maxIngressBufferUsage', width: 160 },
                      { title: '单播Buffer最大使用率', dataIndex: 'maxUnicastUsage', width: 170 },
                      { title: '组播Buffer最大使用率', dataIndex: 'maxMutilcastUsage', width: 170 },
                      { title: '入Buffer超限', dataIndex: 'ingressOverrunCounters', width: 130 },
                      { title: '出Buffer超限', dataIndex: 'egressOverrunCounters', width: 130 },
                      { title: 'Headroom超限', dataIndex: 'headroomOverrunCounters', width: 130 },
                      { title: '队列', dataIndex: 'queInfoList', width: 100, render: (value) => value?.length ? <Tag color="blue">{value.length} 队列</Tag> : <Text type="secondary">-</Text> },
                    ]}
                  />
                </Card>
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
                    dataSource={telemetryLosslessCapabilities}
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
