import { useEffect, useMemo, useState } from 'react'
import { Row, Col, Card, Statistic, Spin, Progress, Space, Typography, theme } from 'antd'
import {
  DesktopOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  CloseCircleOutlined,
  BankOutlined,
  GlobalOutlined,
  ApartmentOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { getDashboardStats, getServerResources, ServerResourceStats } from '../api/metrics'
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
const CHART_COLORS = ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4']

interface ResourceSample {
  timestamp: number
  time: string
  cpu: number
  memory: number
  disk: number
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

const Dashboard = () => {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [serverResources, setServerResources] = useState<ServerResourceStats | null>(null)
  const [resourceSamples, setResourceSamples] = useState<ResourceSample[]>([])
  const [stats, setStats] = useState({
    total: 0,
    datacenters: 0,
    online: 0,
    offline: 0,
    warning: 0,
    publicCircuits: 0,
    privateCircuits: 0,
  })

  useEffect(() => {
    fetchStats()
    fetchServerResources()
    const timer = window.setInterval(fetchServerResources, 10000)
    return () => window.clearInterval(timer)
  }, [])

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
      const timestamp = new Date(result.timestamp).getTime()
      setResourceSamples((prev) => [
        ...prev.slice(-35),
        {
          timestamp,
          time: new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false }),
          cpu: result.cpu.percent,
          memory: result.memory.percent,
          disk: result.disk.percent,
        },
      ])
    } catch (error) {
      console.error('获取服务器资源失败:', error)
    }
  }

  const deviceHealthData = useMemo(() => [
    { name: '在线', value: stats.online, color: '#10b981' },
    { name: '离线', value: stats.offline, color: '#ef4444' },
    { name: '告警', value: stats.warning, color: '#f59e0b' },
  ].filter((item) => item.value > 0), [stats.offline, stats.online, stats.warning])

  const resourceMixData = useMemo(() => [
    { name: '网络设备', value: stats.total },
    { name: '公网链路', value: stats.publicCircuits },
    { name: '专线链路', value: stats.privateCircuits },
    { name: '机房', value: stats.datacenters },
  ], [stats.datacenters, stats.privateCircuits, stats.publicCircuits, stats.total])

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
          <Card title="设备健康分布" loading={loading}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={deviceHealthData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={92} paddingAngle={4}>
                    {deviceHealthData.map((item) => <Cell key={item.name} fill={item.color} />)}
                  </Pie>
                  <Tooltip contentStyle={tooltipStyle} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="资源资产结构" loading={loading}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer>
                <BarChart data={resourceMixData} margin={{ top: 18, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={gridColor} vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: axisColor, fontSize: 12 }} />
                  <YAxis tick={{ fill: axisColor, fontSize: 12 }} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="value" radius={[10, 10, 0, 0]}>
                    {resourceMixData.map((_, index) => <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
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
                <Space size="middle">
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
                        title={`CPU 使用率 (${serverResources.cpu.cores}核)`}
                        value={serverResources.cpu.percent}
                        suffix="%"
                        valueStyle={{ color: serverResources.cpu.percent >= 80 ? '#cf1322' : '#1677ff' }}
                      />
                      <Progress percent={serverResources.cpu.percent} showInfo={false} strokeColor="#1677ff" />
                      <Text type="secondary">
                        {serverResources.cpu.load_avg
                          ? `负载 ${serverResources.cpu.load_avg.join(' / ')}`
                          : '暂无负载信息'}
                      </Text>
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
              </Space>
            ) : (
              <div style={{ textAlign: 'center', padding: 48 }}>
                <Spin />
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default Dashboard
