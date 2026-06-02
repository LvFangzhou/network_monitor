import { useEffect, useState } from 'react'
import { Row, Col, Card, Statistic, List, Spin, Progress, Space, Typography } from 'antd'
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
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const { Text } = Typography

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
  const [recentAlerts, setRecentAlerts] = useState<any[]>([])

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
      setRecentAlerts(result.recent_alerts)
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

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/devices?reset=1')}>
            <Statistic
              title="网络设备总数"
              value={stats.total}
              prefix={<DesktopOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/datacenters')}>
            <Statistic
              title="机房总数"
              value={stats.datacenters}
              prefix={<BankOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/devices?reset=1&status=active')}>
            <Statistic
              title="在线设备"
              value={stats.online}
              valueStyle={{ color: '#3f8600' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/devices?reset=1&status=inactive')}>
            <Statistic
              title="离线设备"
              value={stats.offline}
              valueStyle={{ color: '#cf1322' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/alerts/history?status=firing')}>
            <Statistic
              title="告警设备"
              value={stats.warning}
              valueStyle={{ color: '#faad14' }}
              prefix={<ExclamationCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/public-circuits')}>
            <Statistic
              title="公网链路"
              value={stats.publicCircuits}
              valueStyle={{ color: '#1677ff' }}
              prefix={<GlobalOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card hoverable onClick={() => navigate('/private-circuits')}>
            <Statistic
              title="专线链路"
              value={stats.privateCircuits}
              valueStyle={{ color: '#722ed1' }}
              prefix={<ApartmentOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 24 }}>
        <Col span={12}>
          <Card title="资源概览">
            <p>系统运行正常</p>
            <p>当前共维护 {stats.total} 台网络设备</p>
            <p>当前共维护 {stats.datacenters} 个机房</p>
            <p>公网链路 {stats.publicCircuits} 条，专线链路 {stats.privateCircuits} 条</p>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="最近告警">
            <List
              dataSource={recentAlerts}
              renderItem={(item) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/alerts/history?alert_id=${item.id}`)}
                >
                  <List.Item.Meta
                    title={`${item.device_name} (${item.device_ip})`}
                    description={item.message}
                  />
                </List.Item>
              )}
              locale={{ emptyText: '暂无告警' }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 24 }}>
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

                <div style={{ width: '100%', height: 260 }}>
                  <ResponsiveContainer>
                    <LineChart data={resourceSamples} margin={{ top: 16, right: 24, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                      <XAxis dataKey="time" minTickGap={24} />
                      <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} />
                      <Tooltip formatter={(value: number) => `${Number(value).toFixed(2)}%`} />
                      <Legend />
                      <Line type="linear" dataKey="cpu" name="CPU" stroke="#1677ff" strokeWidth={2} dot={false} />
                      <Line type="linear" dataKey="memory" name="内存" stroke="#52c41a" strokeWidth={2} dot={false} />
                      <Line type="linear" dataKey="disk" name="磁盘" stroke="#fa8c16" strokeWidth={2} dot={false} />
                    </LineChart>
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
