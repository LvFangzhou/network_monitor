import { useEffect, useMemo, useState } from 'react'
import { Row, Col, Card, Statistic, Spin, Progress, Select, Space, Typography, theme, Segmented, Tooltip as AntTooltip, Alert, Table, Tag } from 'antd'
import {
  DesktopOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  CloseCircleOutlined,
  BankOutlined,
  GlobalOutlined,
  ApartmentOutlined,
  QuestionCircleOutlined,
  DeploymentUnitOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { getDashboardStats, getServerQueueHealth, getServerResourceHistory, getServerResources, QueueHealth, QueueHealthItem, ServerResourceStats } from '../api/metrics'
import { getDatacenters } from '../api/devices'
import {
  CartesianGrid,
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const { Text } = Typography
const CHART_COLORS = ['#2f66d8', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4']

interface ResourceSample {
  timestamp: number
  time: string
  cpu: number
  memory: number
  disk: number
  [key: string]: number | string
}

type ResourceRange = '1h' | '6h' | '24h' | '3d' | '7d'

type ChartMode = 'bar' | 'pie' | 'horizontal'
type AssetMetricKey = 'devices' | 'public_circuits' | 'private_circuits'

interface NamedCount {
  name: string
  value: number
}

const formatBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[unitIndex]}`
}

const formatUptime = (seconds: number) => {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分钟`
  return `${minutes}分钟`
}

const formatLoadAverage = (values?: number[] | null) => {
  if (!values || values.length < 3) return '暂无负载信息'
  return `平均负载（1/5/15分钟）：${values.map((value) => Number(value).toFixed(2)).join(' / ')}`
}

const getLoadStatus = (loadAvg?: number[] | null, cores?: number) => {
  if (!loadAvg || loadAvg.length < 1 || !cores || cores <= 0) {
    return { label: '状态未知', color: '#8c8c8c' }
  }
  const latestLoad = Number(loadAvg[0] || 0)
  const ratio = latestLoad / cores
  if (ratio >= 1) {
    return { label: '高负载', color: '#cf1322' }
  }
  if (ratio >= 0.7) {
    return { label: '偏高', color: '#d48806' }
  }
  return { label: '正常', color: '#389e0d' }
}

const getPercentStatus = (percent: number, warning: number, critical: number) => {
  if (percent >= critical) return { label: '高负载', color: '#cf1322' }
  if (percent >= warning) return { label: '偏高', color: '#d48806' }
  return { label: '正常', color: '#389e0d' }
}

const formatRate = (bps: number) => {
  if (!Number.isFinite(bps) || bps <= 0) return '0 bps'
  const units = ['bps', 'Kbps', 'Mbps', 'Gbps', 'Tbps']
  let value = bps
  let unit = 0
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }
  return `${value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${units[unit]}`
}

const Dashboard = () => {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [assetMetric, setAssetMetric] = useState<AssetMetricKey>('devices')
  const [assetChartMode, setAssetChartMode] = useState<ChartMode>('horizontal')
  const [serverResources, setServerResources] = useState<ServerResourceStats | null>(null)
  const [resourceSamples, setResourceSamples] = useState<ResourceSample[]>([])
  const [resourceRange, setResourceRange] = useState<ResourceRange>('1h')
  const [queueHealth, setQueueHealth] = useState<QueueHealth | null>(null)
  const [stats, setStats] = useState({
    total: 0,
    datacenters: 0,
    online: 0,
    offline: 0,
    warning: 0,
    publicCircuits: 0,
    privateCircuits: 0,
    deviceStatusDistribution: [] as NamedCount[],
    assetByDatacenter: {
      devices: [] as NamedCount[],
      public_circuits: [] as NamedCount[],
      private_circuits: [] as NamedCount[],
    },
  })
  const loadStatus = useMemo(
    () => getLoadStatus(serverResources?.cpu.load_avg, serverResources?.cpu.cores),
    [serverResources?.cpu.load_avg, serverResources?.cpu.cores]
  )
  const memoryStatus = useMemo(
    () => getPercentStatus(serverResources?.memory.percent || 0, 70, 85),
    [serverResources?.memory.percent]
  )
  const diskStatus = useMemo(
    () => getPercentStatus(serverResources?.disk.percent || 0, 75, 90),
    [serverResources?.disk.percent]
  )

  useEffect(() => {
    fetchStats()
    fetchServerResources()
    fetchQueueHealth()
    const timer = window.setInterval(fetchServerResources, 10000)
    const queueTimer = window.setInterval(fetchQueueHealth, 10000)
    return () => {
      window.clearInterval(timer)
      window.clearInterval(queueTimer)
    }
  }, [])

  useEffect(() => {
    fetchServerResourceHistory(resourceRange)
    const timer = window.setInterval(() => fetchServerResourceHistory(resourceRange), 30000)
    return () => window.clearInterval(timer)
  }, [resourceRange])

  const fetchStats = async () => {
    try {
      const [result, datacenters] = await Promise.all([
        getDashboardStats(),
        getDatacenters(),
      ])
      setStats({
        total: result.total_devices,
        datacenters: datacenters.length,
        online: result.online_devices,
        offline: result.offline_devices,
        warning: result.total_alerts_firing,
        publicCircuits: result.public_circuits,
        privateCircuits: result.private_circuits,
        deviceStatusDistribution: result.device_status_distribution || [],
        assetByDatacenter: result.asset_by_datacenter || {
          devices: [],
          public_circuits: [],
          private_circuits: [],
        },
      })
    } catch (error) {
      console.error('获取统计失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const fetchServerResources = async () => {
    try {
      const result = await getServerResources()
      setServerResources(result)
    } catch (error) {
      console.error('获取服务器资源失败:', error)
    }
  }

  const fetchQueueHealth = async () => {
    try {
      setQueueHealth(await getServerQueueHealth())
    } catch (error) {
      console.error('获取消息队列健康状态失败:', error)
      setQueueHealth({
        status: 'unavailable',
        checked_at: Date.now() / 1000,
        message: '队列监控接口暂不可用',
        broker: { reachable: false, total_messages: 0, total_ready: 0, total_unacked: 0, consumers: 0 },
        queues: [],
      })
    }
  }

  const fetchServerResourceHistory = async (range: ResourceRange) => {
    try {
      const result = await getServerResourceHistory(range)
      const includeDate = range === '24h' || range === '3d' || range === '7d'
      setResourceSamples((result.samples || []).map((sample) => {
        const timestamp = new Date(sample.timestamp).getTime()
        const network: Record<string, number> = {}
        for (const item of sample.network || []) {
          const key = item.name.replace(/[^a-zA-Z0-9_-]/g, '_')
          network[`network_${key}_rx`] = Number(item.rx_bps || 0)
          network[`network_${key}_tx`] = Number(item.tx_bps || 0)
        }
        return {
          timestamp,
          time: new Date(timestamp).toLocaleString('zh-CN', {
            month: includeDate ? '2-digit' : undefined,
            day: includeDate ? '2-digit' : undefined,
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
          }),
          cpu: sample.cpu.percent,
          memory: sample.memory.percent,
          disk: sample.disk.percent,
          ...network,
        }
      }))
    } catch (error) {
      console.error('获取服务器资源历史失败:', error)
    }
  }

  const networkInterfaces = useMemo(() => (
    (serverResources?.network || [])
      .filter((item) => item.operstate === 'up' || item.rx_bps > 0 || item.tx_bps > 0)
      .slice(0, 8)
  ), [serverResources?.network])

  const deviceStatusData = useMemo(() => {
    const fallback = [
      { name: '上线', value: stats.online },
      { name: '离线', value: stats.offline },
    ].filter((item) => item.value > 0)
    const source = stats.deviceStatusDistribution.length ? stats.deviceStatusDistribution : fallback
    const colorMap: Record<string, string> = {
      上线: '#10b981',
      离线: '#ef4444',
      库存: '#8b5cf6',
      上架: '#2f66d8',
      其他: '#94a3b8',
    }
    return source.map((item, index) => ({
      ...item,
      color: colorMap[item.name] || CHART_COLORS[index % CHART_COLORS.length],
    }))
  }, [stats.deviceStatusDistribution, stats.offline, stats.online])

  const assetMetricMeta = useMemo(() => ({
    devices: { title: '网络设备', unit: '台', color: '#2f66d8' },
    public_circuits: { title: '公网链路', unit: '条', color: '#06b6d4' },
    private_circuits: { title: '专线链路', unit: '条', color: '#8b5cf6' },
  }), [])

  const assetByDatacenterData = useMemo(() => (
    (stats.assetByDatacenter[assetMetric] || [])
      .filter((item) => item.value > 0)
      .slice(0, 12)
  ), [assetMetric, stats.assetByDatacenter])

  const resourceLatestData = useMemo(() => serverResources ? [
    { name: 'CPU', value: serverResources.cpu.percent },
    { name: '内存', value: serverResources.memory.percent },
    { name: '磁盘', value: serverResources.disk.percent },
  ] : [], [serverResources])

  const gridColor = token.colorBorderSecondary
  const axisColor = token.colorTextSecondary
  const tooltipStyle = {
    background: token.colorBgElevated,
    border: `1px solid ${token.colorBorderSecondary}`,
    borderRadius: 12,
    color: token.colorText,
    boxShadow: token.boxShadowSecondary,
  }

  const renderAssetChart = () => {
    const meta = assetMetricMeta[assetMetric]
    if (!assetByDatacenterData.length) {
      return (
        <div style={{ height: 260, display: 'grid', placeItems: 'center' }}>
          <Text type="secondary">暂无机房维度数据</Text>
        </div>
      )
    }

    if (assetChartMode === 'pie') {
      return (
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={assetByDatacenterData}
              dataKey="value"
              nameKey="name"
              innerRadius={54}
              outerRadius={88}
              paddingAngle={3}
            >
              {assetByDatacenterData.map((_, index) => (
                <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${value}${meta.unit}`} />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      )
    }

    if (assetChartMode === 'horizontal') {
      return (
        <ResponsiveContainer>
          <BarChart
            data={assetByDatacenterData}
            layout="vertical"
            margin={{ top: 10, right: 24, left: 24, bottom: 0 }}
          >
            <CartesianGrid stroke={gridColor} horizontal={false} />
            <XAxis type="number" tick={{ fill: axisColor, fontSize: 12 }} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="name"
              width={96}
              tick={{ fill: axisColor, fontSize: 12 }}
            />
            <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${value}${meta.unit}`} />
            <Bar dataKey="value" name={meta.title} fill={meta.color} radius={[0, 10, 10, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )
    }

    return (
      <ResponsiveContainer>
        <BarChart data={assetByDatacenterData} margin={{ top: 18, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={gridColor} vertical={false} />
          <XAxis dataKey="name" tick={{ fill: axisColor, fontSize: 12 }} interval={0} angle={-18} textAnchor="end" height={58} />
          <YAxis tick={{ fill: axisColor, fontSize: 12 }} allowDecimals={false} />
          <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${value}${meta.unit}`} />
          <Bar dataKey="value" name={meta.title} fill={meta.color} radius={[10, 10, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }

  return (
    <div className="modern-page">
      <div className="modern-hero">
        <Row gutter={[20, 20]} align="middle">
          <Col xs={24} lg={15}>
            <Space direction="vertical" size={8}>
              <Typography.Title level={2} style={{ margin: 0 }}>
                网络运行态势
              </Typography.Title>
              <Text type="secondary">
                以设备、链路、告警与服务器资源为核心，实时呈现当前运维健康度。
              </Text>
            </Space>
          </Col>
          <Col xs={24} lg={9}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              {resourceLatestData.map((item, index) => (
                <div key={item.name} style={{ padding: 14, borderRadius: 16, background: token.colorBgContainer }}>
                  <Text type="secondary">{item.name}</Text>
                  <div style={{ fontSize: 24, fontWeight: 800, color: CHART_COLORS[index] }}>{item.value.toFixed(1)}%</div>
                  <Progress percent={Math.round(item.value)} showInfo={false} strokeColor={CHART_COLORS[index]} />
                </div>
              ))}
            </div>
          </Col>
        </Row>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/devices?reset=1')}>
            <Statistic
              title="网络设备总数"
              value={stats.total}
              prefix={<DesktopOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/datacenters')}>
            <Statistic
              title="机房总数"
              value={stats.datacenters}
              prefix={<BankOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/devices?reset=1&status=active')}>
            <Statistic
              title="在线设备"
              value={stats.online}
              valueStyle={{ color: '#3f8600' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/devices?reset=1&status=inactive')}>
            <Statistic
              title="离线设备"
              value={stats.offline}
              valueStyle={{ color: '#cf1322' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/alerts/history?status=firing')}>
            <Statistic
              title="告警设备"
              value={stats.warning}
              valueStyle={{ color: '#faad14' }}
              prefix={<ExclamationCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/public-circuits')}>
            <Statistic
              title="公网链路"
              value={stats.publicCircuits}
              valueStyle={{ color: '#1677ff' }}
              prefix={<GlobalOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card" hoverable onClick={() => navigate('/private-circuits')}>
            <Statistic
              title="专线链路"
              value={stats.privateCircuits}
              valueStyle={{ color: '#722ed1' }}
              prefix={<ApartmentOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="设备运行状态分布" loading={loading}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={deviceStatusData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={92} paddingAngle={4}>
                    {deviceStatusData.map((item) => <Cell key={item.name} fill={item.color} />)}
                  </Pie>
                  <Tooltip contentStyle={tooltipStyle} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title={`${assetMetricMeta[assetMetric].title}按机房统计`}
            loading={loading}
            extra={
              <Space wrap>
                <Segmented
                  size="small"
                  value={assetMetric}
                  onChange={(value) => setAssetMetric(value as AssetMetricKey)}
                  options={[
                    { label: '网络设备', value: 'devices' },
                    { label: '公网链路', value: 'public_circuits' },
                    { label: '专线链路', value: 'private_circuits' },
                  ]}
                />
                <Segmented
                  size="small"
                  value={assetChartMode}
                  onChange={(value) => setAssetChartMode(value as ChartMode)}
                  options={[
                    { label: '横向柱状', value: 'horizontal' },
                    { label: '柱状图', value: 'bar' },
                    { label: '饼图', value: 'pie' },
                  ]}
                />
              </Space>
            }
          >
            <div style={{ height: 260 }}>
              {renderAssetChart()}
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card
            title="服务器资源使用率"
            extra={
              serverResources ? (
                <Space size="middle" wrap>
                  <Select<ResourceRange>
                    size="small"
                    value={resourceRange}
                    onChange={setResourceRange}
                    style={{ width: 112 }}
                    options={[
                      { label: '过去1小时', value: '1h' },
                      { label: '过去6小时', value: '6h' },
                      { label: '过去24小时', value: '24h' },
                      { label: '过去3天', value: '3d' },
                      { label: '过去7天', value: '7d' },
                    ]}
                  />
                  <Text strong>{serverResources.hostname}</Text>
                  <Text type="secondary">{`运行 ${formatUptime(serverResources.uptime_seconds)}`}</Text>
                </Space>
              ) : null
            }
          >
            {serverResources ? (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Row gutter={[16, 16]}>
                  <Col xs={24} md={8}>
                    <Card size="small">
                      <Statistic
                        title={
                          <Space size={6}>
                            <span>{`CPU 使用率 (${serverResources.cpu.cores}核)`}</span>
                            <AntTooltip
                              title={`平均负载表示最近 1 分钟、5 分钟、15 分钟内，系统中处于运行或等待 CPU 的任务数。当前是 ${serverResources.cpu.cores} 核服务器，负载长期接近或超过 ${serverResources.cpu.cores} 才说明 CPU 压力较大。`}
                            >
                              <QuestionCircleOutlined style={{ color: '#8c8c8c', cursor: 'help' }} />
                            </AntTooltip>
                          </Space>
                        }
                        value={serverResources.cpu.percent}
                        suffix="%"
                        valueStyle={{ color: serverResources.cpu.percent >= 80 ? '#cf1322' : '#1677ff' }}
                      />
                      <Progress percent={serverResources.cpu.percent} showInfo={false} strokeColor="#1677ff" />
                      <Space direction="vertical" size={2}>
                        <Text type="secondary">
                          {formatLoadAverage(serverResources.cpu.load_avg)}
                        </Text>
                        <Text style={{ color: loadStatus.color }}>
                          {`负载状态：${loadStatus.label}`}
                        </Text>
                      </Space>
                    </Card>
                  </Col>
                  <Col xs={24} md={8}>
                    <Card size="small">
                      <Statistic
                        title="内存使用率"
                        value={serverResources.memory.percent}
                        suffix="%"
                        valueStyle={{ color: serverResources.memory.percent >= 80 ? '#cf1322' : '#52c41a' }}
                      />
                      <Progress percent={serverResources.memory.percent} showInfo={false} strokeColor="#52c41a" />
                      <Text type="secondary">
                        {`${formatBytes(serverResources.memory.used)} / ${formatBytes(serverResources.memory.total)}`}
                      </Text>
                      <div><Text style={{ color: memoryStatus.color }}>{`负载状态：${memoryStatus.label}`}</Text></div>
                    </Card>
                  </Col>
                  <Col xs={24} md={8}>
                    <Card size="small">
                      <Statistic
                        title={`磁盘使用率 (${serverResources.disk.path})`}
                        value={serverResources.disk.percent}
                        suffix="%"
                        valueStyle={{ color: serverResources.disk.percent >= 80 ? '#cf1322' : '#fa8c16' }}
                      />
                      <Progress percent={serverResources.disk.percent} showInfo={false} strokeColor="#fa8c16" />
                      <Text type="secondary">
                        {`${formatBytes(serverResources.disk.used)} / ${formatBytes(serverResources.disk.total)}`}
                      </Text>
                      <div><Text style={{ color: diskStatus.color }}>{`负载状态：${diskStatus.label}`}</Text></div>
                    </Card>
                  </Col>
                </Row>

                <div style={{ width: '100%', height: 300 }}>
                  <ResponsiveContainer>
                    <AreaChart data={resourceSamples} margin={{ top: 16, right: 24, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="cpuGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#2563eb" stopOpacity={0.26} />
                          <stop offset="95%" stopColor="#2563eb" stopOpacity={0.02} />
                        </linearGradient>
                        <linearGradient id="memoryGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.24} />
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                      <XAxis dataKey="time" minTickGap={24} tick={{ fill: axisColor, fontSize: 12 }} />
                      <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fill: axisColor, fontSize: 12 }} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${Number(value).toFixed(2)}%`} />
                      <Legend />
                      <Area type="monotone" dataKey="cpu" name="CPU" stroke="#2563eb" strokeWidth={2.4} fill="url(#cpuGradient)" dot={false} />
                      <Area type="monotone" dataKey="memory" name="内存" stroke="#10b981" strokeWidth={2.4} fill="url(#memoryGradient)" dot={false} />
                      <Line type="monotone" dataKey="disk" name="磁盘" stroke="#f59e0b" strokeWidth={2.4} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>

                <div>
                  <Space direction="vertical" size={2} style={{ marginBottom: 8 }}>
                    <Text strong>服务器网卡流量</Text>
                    <Text type="secondary">展示宿主机已启用网卡的实时收发速率，历史数据由后台每 30 秒统一采集。</Text>
                  </Space>
                  <div style={{ width: '100%', height: 320 }}>
                    <ResponsiveContainer>
                      <AreaChart data={resourceSamples} margin={{ top: 16, right: 28, left: 12, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                        <XAxis dataKey="time" minTickGap={32} tick={{ fill: axisColor, fontSize: 12 }} />
                        <YAxis tickFormatter={(value) => formatRate(Number(value))} tick={{ fill: axisColor, fontSize: 12 }} width={76} />
                        <Tooltip contentStyle={tooltipStyle} formatter={(value: number, name: string) => [formatRate(Number(value)), name]} />
                        <Legend />
                        {networkInterfaces.flatMap((item, index) => {
                          const key = item.name.replace(/[^a-zA-Z0-9_-]/g, '_')
                          const rxColor = CHART_COLORS[(index * 2) % CHART_COLORS.length]
                          const txColor = CHART_COLORS[(index * 2 + 1) % CHART_COLORS.length]
                          return [
                            <Area key={`${item.name}-rx`} type="monotone" dataKey={`network_${key}_rx`} name={`${item.name} 入向`} stroke={rxColor} strokeWidth={2} fill={rxColor} fillOpacity={0.08} dot={false} connectNulls />,
                            <Line key={`${item.name}-tx`} type="monotone" dataKey={`network_${key}_tx`} name={`${item.name} 出向`} stroke={txColor} strokeWidth={2} dot={false} connectNulls />,
                          ]
                        })}
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </Space>
            ) : (
              <div style={{ textAlign: 'center', padding: 48 }}>
                <Spin />
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card
            title={<Space><DeploymentUnitOutlined /><span>任务队列健康</span></Space>}
            extra={queueHealth ? (
              <Tag color={queueHealth.status === 'healthy' ? 'success' : queueHealth.status === 'warning' ? 'warning' : 'error'}>
                {queueHealth.status === 'healthy' ? '运行正常' : queueHealth.status === 'warning' ? '存在积压' : queueHealth.status === 'critical' ? '需要处理' : '监控不可用'}
              </Tag>
            ) : null}
          >
            {!queueHealth ? (
              <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div>
            ) : !queueHealth.broker.reachable ? (
              <Alert type="warning" showIcon message="RabbitMQ 队列监控暂不可用" description={queueHealth.message || '不会影响仪表盘其他功能，请检查 RabbitMQ Management API。'} />
            ) : (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Row gutter={[16, 16]}>
                  <Col xs={12} md={6}><Statistic title="总待处理" value={queueHealth.broker.total_ready} suffix="条" /></Col>
                  <Col xs={12} md={6}><Statistic title="处理中" value={queueHealth.broker.total_unacked} suffix="条" /></Col>
                  <Col xs={12} md={6}><Statistic title="消费者" value={queueHealth.broker.consumers} suffix="个" /></Col>
                  <Col xs={12} md={6}><Statistic title="监控队列" value={queueHealth.queues.length} suffix="个" /></Col>
                </Row>
                <Table<QueueHealthItem>
                  size="small"
                  rowKey="name"
                  pagination={false}
                  scroll={{ x: 920 }}
                  dataSource={queueHealth.queues}
                  columns={[
                    { title: '队列', dataIndex: 'display_name', width: 190, render: (value, row) => <Space direction="vertical" size={0}><Text>{value}</Text><Text type="secondary" style={{ fontSize: 12 }}>{row.name}</Text></Space> },
                    { title: '待处理', dataIndex: 'ready', width: 90, sorter: (a, b) => a.ready - b.ready },
                    { title: '处理中', dataIndex: 'unacked', width: 90 },
                    { title: '消费者', dataIndex: 'consumers', width: 90 },
                    { title: '发布/秒', dataIndex: 'publish_rate', width: 100, render: (value) => Number(value || 0).toFixed(2) },
                    { title: '消费/秒', dataIndex: 'deliver_rate', width: 100, render: (value) => Number(value || 0).toFixed(2) },
                    { title: '处理延迟', dataIndex: 'processing_lag_seconds', width: 105, render: (value) => value == null ? '-' : `${value}s` },
                    { title: '最近耗时', dataIndex: 'last_latency_ms', width: 105, render: (value) => value == null ? '-' : `${value}ms` },
                    { title: '失败数', dataIndex: 'failed', width: 85, render: (value) => Number(value || 0) },
                    { title: '状态', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'healthy' ? 'success' : value === 'warning' ? 'warning' : 'error'}>{value === 'healthy' ? '正常' : value === 'warning' ? '积压' : '异常'}</Tag> },
                  ]}
                />
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default Dashboard
