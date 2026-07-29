import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Select, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getLosslessTelemetryDevices, getLosslessTelemetrySnapshot } from '../../api/metrics'

const { Text } = Typography

const TelemetryStatus = () => {
  const [devices, setDevices] = useState<any[]>([])
  const [deviceId, setDeviceId] = useState<number>()
  const [snapshot, setSnapshot] = useState<any>({ path_status: [] })
  const [loading, setLoading] = useState(false)

  const loadDevices = useCallback(async () => {
    const data = await getLosslessTelemetryDevices()
    setDevices(data.items || [])
    setDeviceId((current) => current || data.items?.[0]?.id)
  }, [])

  const loadSnapshot = useCallback(async () => {
    if (!deviceId) return
    setLoading(true)
    try {
      setSnapshot(await getLosslessTelemetrySnapshot(deviceId))
    } finally {
      setLoading(false)
    }
  }, [deviceId])

  useEffect(() => { void loadDevices() }, [loadDevices])
  useEffect(() => {
    void loadSnapshot()
    const timer = window.setInterval(() => void loadSnapshot(), 30_000)
    return () => window.clearInterval(timer)
  }, [loadSnapshot])

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="Telemetry路径接收状态">
        <Space wrap>
          <Select
            showSearch
            optionFilterProp="label"
            style={{ minWidth: 460 }}
            placeholder="选择Telemetry设备"
            value={deviceId}
            options={devices.map((item) => ({ value: item.id, label: `${item.name} / ${item.ip_address} / ${item.model || '-'}` }))}
            onChange={setDeviceId}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadSnapshot}>刷新</Button>
          <Tag color="green">已识别 {devices.length} 台</Tag>
          {snapshot.collected_at && <Text type="secondary">最新：{new Date(snapshot.collected_at).toLocaleString()}</Text>}
        </Space>
      </Card>
      <Card>
        <Table<any>
          rowKey="sensor_path"
          loading={loading}
          dataSource={snapshot.path_status || []}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
          columns={[
            { title: 'Sensor Path', dataIndex: 'sensor_path', sorter: (a, b) => String(a.sensor_path).localeCompare(String(b.sensor_path)) },
            { title: '实际状态', dataIndex: 'received', width: 110, filters: [{ text: '已收到', value: true }, { text: '暂无', value: false }], onFilter: (v, r) => r.received === v, render: (v) => <Tag color={v ? 'green' : 'default'}>{v ? '已收到' : '暂无'}</Tag> },
            { title: '有效行数', dataIndex: 'row_count', width: 110, sorter: (a, b) => a.row_count - b.row_count },
            { title: '最近接收', dataIndex: 'collected_at', width: 200, render: (v) => v ? new Date(v).toLocaleString() : '-' },
          ]}
        />
      </Card>
    </Space>
  )
}

export default TelemetryStatus
