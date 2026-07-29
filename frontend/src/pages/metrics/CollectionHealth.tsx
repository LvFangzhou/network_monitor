import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Input, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getCollectionHealth } from '../../api/metrics'

const { Text } = Typography

const healthMeta: Record<string, { label: string; color: string }> = {
  healthy: { label: '正常', color: 'green' },
  delayed: { label: '延迟', color: 'orange' },
  stale: { label: '陈旧', color: 'red' },
  unreachable: { label: '不可达', color: 'volcano' },
}

const formatAge = (seconds?: number | null) => {
  if (seconds == null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}分钟`
  return `${(seconds / 3600).toFixed(1)}小时`
}

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString() : '-'

const CollectionHealth = () => {
  const [payload, setPayload] = useState<any>({ summary: {}, items: [], alert_tasks: [], profiles: {} })
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [health, setHealth] = useState<string>()
  const [source, setSource] = useState<string>()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setPayload(await getCollectionHealth())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    return () => window.clearInterval(timer)
  }, [load])

  const rows = useMemo<any[]>(() => (payload.items || []).filter((item: any) => {
    const text = `${item.device_name} ${item.ip_address} ${item.vendor} ${item.model} ${item.datacenter}`.toLowerCase()
    return (!keyword || text.includes(keyword.toLowerCase()))
      && (!health || item.health === health)
      && (!source || item.source === source || item.configured_source === source)
  }), [payload.items, keyword, health, source])

  const summary = payload.summary || {}
  const healthyPercent = summary.total ? Math.round((summary.healthy || 0) * 1000 / summary.total) / 10 : 0

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="采集健康中心"
        extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}
      >
        <Alert
          type="info"
          showIcon
          message="这里展示实际数据新鲜度，不以“已经配置”代替“已经收到”。事件类秒级处理，接口与慢指标分层采集。"
          style={{ marginBottom: 16 }}
        />
        <Row gutter={[12, 12]}>
          <Col xs={12} lg={4}><Statistic title="监控设备" value={summary.total || 0} /></Col>
          <Col xs={12} lg={4}><Statistic title="采集正常" value={summary.healthy || 0} valueStyle={{ color: '#389e0d' }} /></Col>
          <Col xs={12} lg={4}><Statistic title="采集延迟" value={summary.delayed || 0} valueStyle={{ color: '#d48806' }} /></Col>
          <Col xs={12} lg={4}><Statistic title="数据陈旧" value={summary.stale || 0} valueStyle={{ color: '#cf1322' }} /></Col>
          <Col xs={12} lg={4}><Statistic title="采集不可达" value={summary.unreachable || 0} valueStyle={{ color: '#cf1322' }} /></Col>
          <Col xs={12} lg={4}>
            <Text type="secondary">总体新鲜度</Text>
            <Progress percent={healthyPercent} size="small" status={healthyPercent < 95 ? 'exception' : 'success'} />
          </Col>
        </Row>
      </Card>

      <Card title="当前颗粒度">
        <Row gutter={[12, 12]}>
          {Object.entries(payload.profiles || {}).map(([key, value]) => (
            <Col xs={24} md={12} xl={6} key={key}>
              <Card size="small"><Text>{String(value)}</Text></Card>
            </Col>
          ))}
        </Row>
      </Card>

      <Card
        title="设备采集明细"
        extra={payload.generated_at && <Text type="secondary">更新：{formatTime(payload.generated_at)}</Text>}
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Input.Search allowClear placeholder="设备、IP、厂商、型号、机房" style={{ width: 300 }} onSearch={setKeyword} onChange={(e) => !e.target.value && setKeyword('')} />
          <Select allowClear placeholder="采集状态" style={{ width: 130 }} value={health} onChange={setHealth}
            options={Object.entries(healthMeta).map(([value, meta]) => ({ value, label: meta.label }))} />
          <Select allowClear placeholder="采集来源" style={{ width: 150 }} value={source} onChange={setSource}
            options={[
              { value: 'telemetry', label: 'Telemetry' },
              { value: 'snmp', label: 'SNMP' },
              { value: 'asternos_exporter', label: 'Exporter' },
            ]} />
        </Space>
        <Table<any>
          rowKey="device_id"
          loading={loading}
          dataSource={rows}
          scroll={{ x: 1500 }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '设备', dataIndex: 'device_name', width: 250, sorter: (a, b) => String(a.device_name).localeCompare(String(b.device_name)) },
            { title: '管理IP', dataIndex: 'ip_address', width: 135, sorter: (a, b) => String(a.ip_address).localeCompare(String(b.ip_address)) },
            { title: '机房', dataIndex: 'datacenter', width: 150, filters: [...new Set<string>(rows.map((i: any) => String(i.datacenter || '')).filter(Boolean))].map((v) => ({ text: v, value: v })), onFilter: (v, r) => r.datacenter === v },
            { title: '来源', dataIndex: 'source', width: 115, render: (v) => <Tag color={v === 'telemetry' ? 'blue' : v === 'asternos_exporter' ? 'purple' : 'cyan'}>{v || '-'}</Tag> },
            { title: '健康', dataIndex: 'health', width: 90, render: (v) => <Tag color={(healthMeta[v] || {}).color}>{(healthMeta[v] || {}).label || v}</Tag> },
            { title: '数据年龄', dataIndex: 'age_seconds', width: 105, sorter: (a, b) => (a.age_seconds ?? 1e12) - (b.age_seconds ?? 1e12), render: formatAge },
            { title: '目标周期', dataIndex: 'expected_interval_seconds', width: 95, render: (v) => `${v}秒` },
            { title: '最近采集', dataIndex: 'collected_at', width: 180, render: formatTime },
            { title: 'SNMP', dataIndex: 'snmp_status', width: 100, render: (v, row) => <Tag color={v === 'reachable' ? 'green' : v === 'unreachable' ? 'red' : 'default'}>{v} / {row.snmp_failures}</Tag> },
            { title: '最近Syslog', dataIndex: 'syslog_last_at', width: 180, render: formatTime },
            { title: 'BMP', dataIndex: 'bmp_status', width: 120, render: (v, row) => v ? <Tag color={v === 'connected' ? 'green' : 'red'}>{v} / {row.bmp_message_count}</Tag> : '-' },
          ]}
        />
      </Card>

      <Card title="告警评估耗时">
        <Table<any>
          rowKey="task"
          size="small"
          dataSource={payload.alert_tasks || []}
          pagination={false}
          locale={{ emptyText: '部署后完成一轮告警评估即可看到耗时' }}
          columns={[
            { title: '任务', dataIndex: 'task' },
            { title: '规则数', dataIndex: 'total_rules', width: 90 },
            { title: '触发数', dataIndex: 'triggered', width: 90 },
            { title: '耗时', dataIndex: 'elapsed_seconds', width: 100, render: (v) => `${v}秒`, sorter: (a, b) => a.elapsed_seconds - b.elapsed_seconds },
            { title: '检查时间', dataIndex: 'checked_at', width: 190, render: formatTime },
          ]}
        />
      </Card>
    </Space>
  )
}

export default CollectionHealth
