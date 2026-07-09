import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { LineChartOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { getCircuits, type Circuit } from '../../api/resources'

const { Text } = Typography

const getTrafficTargets = (record: Circuit) => {
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
    record.aggregation_monitor_device_id && record.aggregation_monitor_device_ip && record.aggregation_interface_name
      ? {
          deviceId: record.aggregation_monitor_device_id,
          deviceIp: record.aggregation_monitor_device_ip,
          deviceName: record.aggregation_monitor_device_name,
          portName: record.aggregation_interface_name,
          side: 'aggregation',
        }
      : null,
  ].filter(Boolean)

  return targets as Array<{
    deviceId?: number
    deviceIp?: string
    deviceName?: string
    portName: string
    side?: string
  }>
}

const TrafficQuery = () => {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<Circuit[]>([])
  const [keyword, setKeyword] = useState('')
  const [lineType, setLineType] = useState<string>('all')

  const fetchItems = async () => {
    setLoading(true)
    try {
      const result = await getCircuits({
        limit: 1000,
        line_type: lineType === 'all' ? undefined : lineType,
        search: keyword.trim() || undefined,
      })
      setItems(result.items || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取线路失败')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchItems()
  }, [lineType])

  const filteredItems = useMemo(() => {
    const text = keyword.trim().toLowerCase()
    if (!text) return items
    return items.filter((item) => [
      item.name,
      item.operator_name,
      item.datacenter_name,
      item.customer_name,
      item.primary_device_name,
      item.primary_device_ip,
      item.primary_port_name,
      item.secondary_device_name,
      item.secondary_device_ip,
      item.secondary_port_name,
      item.aggregation_monitor_device_name,
      item.aggregation_monitor_device_ip,
      item.aggregation_interface_name,
    ].some((value) => String(value || '').toLowerCase().includes(text)))
  }, [items, keyword])

  const openTraffic = (record: Circuit) => {
    const targets = getTrafficTargets(record)
    if (!targets.length) {
      message.warning('该线路还没有绑定可监控的交换机端口')
      return
    }
    navigate('/port-query', {
      state: {
        circuitMonitorTargets: targets,
        sourceCircuitName: record.name,
        sourceCircuitType: record.line_type === 'private_line' ? '专线' : '公网',
      },
    })
  }

  const columns: ColumnsType<Circuit> = [
    {
      title: '线路名称',
      dataIndex: 'name',
      width: 260,
      render: (value: string, record) => (
        <Space direction="vertical" size={0} style={{ maxWidth: 260 }}>
          <Text strong ellipsis={{ tooltip: value }}>{value}</Text>
          <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: record.customer_name || '' }}>
            {record.customer_name || '-'}
          </Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'line_type',
      width: 90,
      render: (value: string) => <Tag color={value === 'private_line' ? 'blue' : 'green'}>{value === 'private_line' ? '专线' : '公网'}</Tag>,
    },
    { title: '机房', dataIndex: 'datacenter_name', width: 150, ellipsis: true },
    { title: '运营商', dataIndex: 'operator_name', width: 110, ellipsis: true },
    {
      title: '带宽',
      dataIndex: 'bandwidth_mbps',
      width: 100,
      render: (value: number) => value ? `${value} Mbps` : '-',
    },
    {
      title: '监控端口',
      width: 320,
      render: (_, record) => {
        const targets = getTrafficTargets(record)
        return targets.length ? (
          <Space direction="vertical" size={2}>
            {targets.map((target) => (
              <Text key={`${target.deviceIp}-${target.portName}-${target.side}`} style={{ fontSize: 12 }} ellipsis={{ tooltip: `${target.deviceName || target.deviceIp} / ${target.portName}` }}>
                {target.deviceName || target.deviceIp} / {target.portName}
              </Text>
            ))}
          </Space>
        ) : <Text type="secondary">未绑定</Text>
      },
    },
    {
      title: '操作',
      width: 120,
      fixed: 'right',
      render: (_, record) => (
        <Button type="link" icon={<LineChartOutlined />} onClick={() => openTraffic(record)}>
          查看流量
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 流量查询</div>
      <Card
        title="公网/专线流量查询"
        extra={(
          <Button icon={<ReloadOutlined />} loading={loading} onClick={fetchItems}>刷新</Button>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Select
              value={lineType}
              style={{ width: 140 }}
              options={[
                { label: '全部线路', value: 'all' },
                { label: '公网', value: 'internet' },
                { label: '专线', value: 'private_line' },
              ]}
              onChange={setLineType}
            />
            <Input.Search
              allowClear
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onSearch={() => fetchItems()}
              prefix={<SearchOutlined />}
              placeholder="搜索线路、客户、机房、运营商、设备或接口"
              style={{ width: 420 }}
            />
          </Space>
          <Table
            rowKey="id"
            size="middle"
            loading={loading}
            columns={columns}
            dataSource={filteredItems}
            scroll={{ x: 1200 }}
            pagination={{
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
              defaultPageSize: 20,
              showTotal: (total) => `共 ${total} 条`,
            }}
          />
        </Space>
      </Card>
    </Space>
  )
}

export default TrafficQuery
