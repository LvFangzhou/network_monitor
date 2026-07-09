import { type Key, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Popover,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SettingOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import {
  createQualityProbeTarget,
  deleteQualityProbeTarget,
  getQualityProbeHistory,
  getQualityProbeTargets,
  runQualityProbeMtr,
  testQualityProbeTarget,
  updateQualityProbeTarget,
  type QualityProbeHistoryPoint,
  type QualityProbeTarget,
} from '../../api/metrics'
import { getDatacenters, type Datacenter } from '../../api/devices'

const { Text, Title } = Typography

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const latencyColor = (value?: number | null, threshold?: number | null) => {
  if (value === null || value === undefined) return 'default'
  if (threshold && value > threshold) return 'red'
  if (threshold && value > threshold * 0.8) return 'orange'
  return 'green'
}

const rangeOptions = [
  { label: '15分钟', value: '-15m', interval: '1s' },
  { label: '1小时', value: '-1h', interval: '5s' },
  { label: '6小时', value: '-6h', interval: '30s' },
  { label: '24小时', value: '-24h', interval: '2m' },
  { label: '7天', value: '-7d', interval: '15m' },
  { label: '30天', value: '-30d', interval: '1h' },
  { label: '365天', value: '-365d', interval: '1d' },
]

const qualityColumnOptions = [
  { label: '机房', value: 'datacenter' },
  { label: '运营商', value: 'operator' },
  { label: '启用', value: 'enabled' },
  { label: '状态', value: 'probe_status' },
  { label: '延迟', value: 'latency' },
  { label: '丢包', value: 'loss' },
  { label: '抖动', value: 'jitter' },
  { label: '阈值', value: 'threshold' },
  { label: '采样', value: 'sampling' },
  { label: '最近测试时间', value: 'last_probe_at' },
]

const defaultVisibleColumns = [
  'datacenter',
  'operator',
  'enabled',
  'probe_status',
  'latency',
  'loss',
  'jitter',
  'last_probe_at',
]

const formatChartTime = (value: string | number, rangeValue: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  if (rangeValue === '-15m' || rangeValue === '-1h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
  }
  if (rangeValue === '-6h' || rangeValue === '-24h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}-${date.getDate()}`
}

const chartTickCount = (rangeValue: string) => {
  if (rangeValue === '-15m') return 16
  if (rangeValue === '-1h') return 13
  if (rangeValue === '-6h') return 13
  if (rangeValue === '-24h') return 13
  if (rangeValue === '-7d') return 8
  if (rangeValue === '-30d') return 10
  return 13
}

const QualityChartPanel = ({ target }: { target: QualityProbeTarget }) => {
  const [rangeValue, setRangeValue] = useState('-1h')
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyData, setHistoryData] = useState<QualityProbeHistoryPoint[]>([])

  const fetchHistory = async (silent = false) => {
    const option = rangeOptions.find((item) => item.value === rangeValue) || rangeOptions[1]
    if (!silent) setHistoryLoading(true)
    try {
      const response = await getQualityProbeHistory(target.id, {
        range: option.value,
        interval: option.interval,
      })
      setHistoryData(response.data || [])
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取延迟变化曲线失败')
      setHistoryData([])
    } finally {
      if (!silent) setHistoryLoading(false)
    }
  }

  useEffect(() => {
    void fetchHistory()
    const refreshMs = Math.max(1000, Math.min(Number(target.interval_seconds || 5) * 1000, 10000))
    const timer = window.setInterval(() => {
      void fetchHistory(true)
    }, refreshMs)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id, target.interval_seconds, rangeValue])

  const chartData = useMemo(
    () => historyData
      .filter((point) => point._time)
      .map((point) => ({
        ...point,
        ts: new Date(point._time || '').getTime(),
      }))
      .filter((point) => Number.isFinite(point.ts)),
    [historyData]
  )

  return (
    <Card
      size="small"
      title={`${target.name} / ${target.target} 延迟变化`}
      extra={(
        <Space wrap>
          <Select
            value={rangeValue}
            style={{ width: 120 }}
            options={rangeOptions.map((item) => ({ label: item.label, value: item.value }))}
            onChange={setRangeValue}
          />
          <Button icon={<ReloadOutlined />} loading={historyLoading} onClick={() => fetchHistory()}>刷新</Button>
        </Space>
      )}
      styles={{ body: { paddingTop: 8 } }}
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        曲线会自动刷新；短时间范围使用更细 X 轴刻度。1 秒采样时建议每次包数设置为 1 或 2。
      </Text>
      <div style={{ height: 320, width: '100%', marginTop: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 16, right: 36, left: 8, bottom: 16 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={['dataMin', 'dataMax']}
              tick={{ fontSize: 11 }}
              minTickGap={8}
              tickCount={chartTickCount(rangeValue)}
              tickFormatter={(value) => formatChartTime(Number(value), rangeValue)}
            />
            <YAxis
              yAxisId="ms"
              width={82}
              tick={{ fontSize: 11 }}
              tickFormatter={(value) => `${value} ms`}
            />
            <YAxis
              yAxisId="percent"
              orientation="right"
              width={82}
              tick={{ fontSize: 11 }}
              domain={[0, 100]}
              tickFormatter={(value) => `${value}%`}
            />
            <ChartTooltip
              labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())}
              formatter={(value: any, name: string) => {
                const unit = name.includes('丢包') || name.includes('可用率') ? '%' : 'ms'
                return [`${Number(value).toFixed(2)} ${unit}`, name]
              }}
            />
            <Legend />
            <Line yAxisId="ms" type="monotone" dataKey="avg_latency_ms" name="延迟" stroke="#1677ff" dot={false} strokeWidth={2} connectNulls />
            <Line yAxisId="ms" type="monotone" dataKey="jitter_ms" name="抖动" stroke="#fa8c16" dot={false} strokeWidth={2} connectNulls />
            <Line yAxisId="percent" type="monotone" dataKey="packet_loss_percent" name="丢包率" stroke="#f5222d" dot={false} strokeWidth={2} connectNulls />
            <Line yAxisId="percent" type="monotone" dataKey="availability_percent" name="可用率" stroke="#52c41a" dot={false} strokeWidth={2} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!historyLoading && !chartData.length ? (
        <Text type="secondary">当前时间范围内还没有曲线数据，后台任务采集到新点后会自动刷新。</Text>
      ) : null}
    </Card>
  )
}

const QualityQuery = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [items, setItems] = useState<QualityProbeTarget[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [keyword, setKeyword] = useState('')
  const [activeFilter, setActiveFilter] = useState<string>('all')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<QualityProbeTarget | null>(null)
  const [expandedRowKeys, setExpandedRowKeys] = useState<Key[]>([])
  const [mtrLoadingId, setMtrLoadingId] = useState<number | null>(null)
  const [mtrOpen, setMtrOpen] = useState(false)
  const [mtrTitle, setMtrTitle] = useState('')
  const [mtrCommand, setMtrCommand] = useState('')
  const [mtrOutput, setMtrOutput] = useState('')
  const [visibleColumnKeys, setVisibleColumnKeys] = useState<string[]>(defaultVisibleColumns)

  const fetchItems = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const response = await getQualityProbeTargets({
        search: keyword.trim() || undefined,
        active: activeFilter === 'all' ? undefined : activeFilter === 'active',
      })
      const nextItems = response.items || []
      setItems(nextItems)
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取质量探测目标失败')
      setItems([])
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchDatacenters = async () => {
    try {
      setDatacenters(await getDatacenters())
    } catch {
      setDatacenters([])
    }
  }

  useEffect(() => {
    void fetchDatacenters()
  }, [])

  useEffect(() => {
    void fetchItems()
  }, [activeFilter])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void fetchItems(true)
    }, 5000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFilter, keyword])

  const datacenterOptions = useMemo(
    () => datacenters.filter((item) => item.is_active !== false).map((item) => ({ label: item.name, value: item.id })),
    [datacenters]
  )

  const openCreate = () => {
    setEditing(null)
    form.setFieldsValue({
      interval_seconds: 60,
      packet_count: 5,
      timeout_ms: 1000,
      latency_threshold_ms: 100,
      loss_threshold_percent: 1,
      jitter_threshold_ms: 30,
      is_active: true,
    })
    setModalOpen(true)
  }

  const openEdit = (record: QualityProbeTarget) => {
    setEditing(record)
    form.setFieldsValue({
      ...record,
      datacenter_id: record.datacenter_id || undefined,
    })
    setModalOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      if (editing) {
        await updateQualityProbeTarget(editing.id, values)
        message.success('质量探测目标已更新')
      } else {
        await createQualityProbeTarget(values)
        message.success('质量探测目标已添加')
      }
      setModalOpen(false)
      setEditing(null)
      await fetchItems()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (record: QualityProbeTarget) => {
    try {
      await deleteQualityProbeTarget(record.id)
      message.success('已删除')
      await fetchItems()
      setExpandedRowKeys((keys) => keys.filter((key) => key !== record.id))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleTest = async (record: QualityProbeTarget) => {
    setTestingId(record.id)
    try {
      const response = await testQualityProbeTarget(record.id)
      const result = response.result
      if (result.success) {
        message.success(`测试成功：平均 ${result.avg_latency_ms ?? '-'} ms，丢包 ${result.packet_loss_percent ?? '-'}%`)
      } else {
        message.warning(result.error || '测试未收到响应')
      }
      await fetchItems(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const handleMtr = async (record: QualityProbeTarget) => {
    setMtrLoadingId(record.id)
    setMtrTitle(`${record.name} / ${record.target}`)
    setMtrCommand('')
    setMtrOutput('')
    setMtrOpen(true)
    try {
      const result = await runQualityProbeMtr(record.id)
      setMtrCommand(result.command || result.tool || 'MTR')
      setMtrOutput(result.output || '无输出')
    } catch (error: any) {
      setMtrOutput(error?.response?.data?.detail || 'MTR 执行失败')
    } finally {
      setMtrLoadingId(null)
    }
  }

  const formatMetricTag = (
    value: number | null | undefined,
    unit: string,
    color: string = 'blue',
  ) => {
    if (value === null || value === undefined) return <Text type="secondary">-</Text>
    return <Tag color={color}>{value} {unit}</Tag>
  }

  const columnDefinitions: ColumnsType<QualityProbeTarget> = [
    {
      key: 'name',
      title: '探测名称',
      dataIndex: 'name',
      width: 180,
      fixed: 'left',
      render: (value: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong ellipsis={{ tooltip: value }} style={{ maxWidth: 170 }}>{value}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.target}</Text>
        </Space>
      ),
    },
    { key: 'datacenter', title: '机房', dataIndex: 'datacenter_name', width: 150, render: (value) => value || '-' },
    { key: 'operator', title: '运营商', dataIndex: 'operator_name', width: 110, render: (value) => value || '-' },
    {
      key: 'enabled',
      title: '启用',
      dataIndex: 'is_active',
      width: 90,
      render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag>,
    },
    {
      key: 'probe_status',
      title: '状态',
      width: 100,
      render: (_, record) => {
        if (record.last_success === null || record.last_success === undefined) return <Tag>未测试</Tag>
        return (
          <Space direction="vertical" size={0}>
            <Tag color={record.last_success ? 'green' : 'red'}>{record.last_success ? '正常' : '异常'}</Tag>
            {record.last_error ? <Text type="secondary" style={{ fontSize: 12 }}>{record.last_error}</Text> : null}
          </Space>
        )
      },
    },
    {
      key: 'latency',
      title: '延迟',
      width: 100,
      render: (_, record) => formatMetricTag(
        record.last_avg_latency_ms,
        'ms',
        latencyColor(record.last_avg_latency_ms, record.latency_threshold_ms)
      ),
    },
    {
      key: 'loss',
      title: '丢包',
      width: 90,
      render: (_, record) => formatMetricTag(
        record.last_packet_loss_percent,
        '%',
        (record.last_packet_loss_percent || 0) > record.loss_threshold_percent ? 'red' : 'green'
      ),
    },
    {
      key: 'jitter',
      title: '抖动',
      width: 90,
      render: (_, record) => formatMetricTag(
        record.last_jitter_ms,
        'ms',
        (record.last_jitter_ms || 0) > record.jitter_threshold_ms ? 'orange' : 'blue'
      ),
    },
    {
      key: 'threshold',
      title: '阈值',
      width: 170,
      render: (_, record) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          延迟&gt;{record.latency_threshold_ms}ms / 丢包&gt;{record.loss_threshold_percent}% / 抖动&gt;{record.jitter_threshold_ms}ms
        </Text>
      ),
    },
    {
      key: 'sampling',
      title: '采样',
      width: 140,
      render: (_, record) => `${record.interval_seconds}s / ${record.packet_count}包 / ${record.timeout_ms}ms`,
    },
    {
      key: 'last_probe_at',
      title: '最近测试时间',
      dataIndex: 'last_probe_at',
      width: 170,
      render: formatTime,
    },
    {
      key: 'action',
      title: '操作',
      width: 230,
      fixed: 'right',
      render: (_, record) => (
        <Space size={4}>
          <Button
            type="link"
            icon={<ThunderboltOutlined />}
            loading={testingId === record.id}
            onClick={() => handleTest(record)}
          >
            立即测试
          </Button>
          <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Button type="link" loading={mtrLoadingId === record.id} onClick={() => handleMtr(record)}>MTR</Button>
          <Popconfirm title="确认删除这个探测目标？" onConfirm={() => handleDelete(record)}>
            <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const columns = columnDefinitions.filter((column) => {
    const key = String(column.key || '')
    return key === 'name' || key === 'action' || visibleColumnKeys.includes(key)
  })

  const columnPopover = (
    <div style={{ width: 180 }}>
      <Checkbox.Group
        value={visibleColumnKeys}
        onChange={(values) => setVisibleColumnKeys(values.map(String))}
      >
        <Space direction="vertical">
          {qualityColumnOptions.map((item) => (
            <Checkbox key={item.value} value={item.value}>{item.label}</Checkbox>
          ))}
        </Space>
      </Checkbox.Group>
    </div>
  )

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 质量查询</div>
      <Card
        title={(
          <Space direction="vertical" size={0}>
            <Title level={4} style={{ margin: 0 }}>公网质量探测</Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              录入公网 IP 或域名后，可以从服务器侧发起 ICMP 探测，记录延迟、丢包和抖动。
            </Text>
          </Space>
        )}
        extra={(
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchItems()}>刷新</Button>
            <Popover content={columnPopover} trigger="click" placement="bottomRight">
              <Button icon={<SettingOutlined />}>列展示</Button>
            </Popover>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增目标</Button>
          </Space>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Select
              value={activeFilter}
              style={{ width: 120 }}
              onChange={setActiveFilter}
              options={[
                { label: '全部状态', value: 'all' },
                { label: '启用', value: 'active' },
                { label: '停用', value: 'inactive' },
              ]}
            />
            <Input.Search
              allowClear
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onSearch={() => fetchItems()}
              placeholder="搜索名称、目标、机房、运营商"
              style={{ width: 360 }}
            />
          </Space>

          <Table
            rowKey="id"
            loading={loading}
            columns={columns}
            dataSource={items}
            scroll={{ x: 1500 }}
            expandable={{
              expandedRowKeys,
              onExpandedRowsChange: (keys) => setExpandedRowKeys(keys as Key[]),
              expandedRowRender: (record) => <QualityChartPanel target={record} />,
              columnWidth: 42,
            }}
            pagination={{
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
              defaultPageSize: 20,
              showTotal: (total) => `共 ${total} 个探测目标`,
            }}
          />
        </Space>
      </Card>

      <Modal
        title={editing ? '编辑质量探测目标' : '新增质量探测目标'}
        open={modalOpen}
        onOk={handleSave}
        confirmLoading={saving}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
        }}
        width={760}
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item
              name="name"
              label="探测名称"
              rules={[{ required: true, message: '请输入探测名称' }]}
              style={{ width: 330 }}
            >
              <Input placeholder="例如：湖北宜昌-电信DNS" />
            </Form.Item>
            <Form.Item
              name="target"
              label="目标 IP / 域名"
              rules={[{ required: true, message: '请输入目标 IP 或域名' }]}
              style={{ width: 330 }}
            >
              <Input placeholder="例如：114.114.114.114 或 www.example.com" />
            </Form.Item>
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="datacenter_id" label="机房" style={{ width: 220 }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="选择机房" options={datacenterOptions} />
            </Form.Item>
            <Form.Item name="operator_name" label="运营商" style={{ width: 180 }}>
              <Input placeholder="电信/联通/移动/BGP" />
            </Form.Item>
            <Form.Item name="is_active" label="是否启用" valuePropName="checked" style={{ width: 120 }}>
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="interval_seconds" label="采样间隔(s)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={3600} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="packet_count" label="每次包数" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={20} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="timeout_ms" label="超时(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={200} max={10000} style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="latency_threshold_ms" label="延迟阈值(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="loss_threshold_percent" label="丢包阈值(%)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="jitter_threshold_ms" label="抖动阈值(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={0} max={10000} style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Form.Item name="description" label="备注">
            <Input.TextArea rows={3} placeholder="可填写用途、运营商、线路背景等信息" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`MTR - ${mtrTitle || '探测目标'}`}
        open={mtrOpen}
        width={900}
        footer={<Button onClick={() => setMtrOpen(false)}>关闭</Button>}
        onCancel={() => setMtrOpen(false)}
      >
        {mtrCommand ? <Text type="secondary">{mtrCommand}</Text> : null}
        <pre style={{
          marginTop: 12,
          padding: 12,
          minHeight: 260,
          maxHeight: 520,
          overflow: 'auto',
          borderRadius: 8,
          background: '#0f172a',
          color: '#d1e7ff',
          whiteSpace: 'pre-wrap',
        }}
        >
          {mtrOutput || (mtrLoadingId ? 'MTR 执行中...' : '暂无输出')}
        </pre>
      </Modal>
    </Space>
  )
}

export default QualityQuery
