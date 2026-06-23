import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Card, message, Typography, Space } from 'antd'
import {
  UserOutlined,
  LockOutlined,
  SafetyOutlined,
  ClusterOutlined,
  ThunderboltOutlined,
  RadarChartOutlined,
} from '@ant-design/icons'
import type { AxiosError } from 'axios'
import { useAuthStore } from '../store/auth'
import { initAuth } from '../api/auth'

const { Title, Text } = Typography

const Login = () => {
  const [loading, setLoading] = useState(false)
  const [initLoading, setInitLoading] = useState(false)
  const navigate = useNavigate()
  const { login } = useAuthStore()

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      await login(values.username, values.password)
      message.success('登录成功')
      navigate('/')
    } catch (error) {
      const axiosError = error as AxiosError<{ detail?: string }>
      const detail = axiosError.response?.data?.detail
      message.error(detail || '登录失败，请检查账号信息')
    } finally {
      setLoading(false)
    }
  }

  const handleInit = async () => {
    setInitLoading(true)
    try {
      const result = await initAuth() as any
      if (result.admin_user) {
        message.success(`初始化成功！管理员账号: ${result.admin_user.username} / ${result.admin_user.password}`)
      } else {
        message.info(result.message)
      }
    } catch (error) {
      // 错误已处理
    } finally {
      setInitLoading(false)
    }
  }

  return (
    <div className="login-shell">
      <div className="login-grid-overlay" />
      <div className="login-glow login-glow-a" />
      <div className="login-glow login-glow-b" />

      <div className="login-stage">
        <section className="login-hero-panel">
          <div className="login-brand-pill">
            <SafetyOutlined />
            Network Ops Console
          </div>
          <Title level={1} className="login-hero-title">
            面向现代网络工程师的可视化运维平台
          </Title>
          <Text className="login-hero-subtitle">
            汇聚网络设备、端口流量、告警审计与 Tacacs 操作记录，用更直观的方式掌控基础设施状态。
          </Text>

          <div className="login-signal-card">
            <div>
              <Text className="login-signal-label">Backbone health</Text>
              <div className="login-signal-value">99.98%</div>
            </div>
            <div className="login-signal-bars">
              {Array.from({ length: 18 }).map((_, index) => (
                <span key={index} style={{ height: 12 + ((index * 7) % 28) }} />
              ))}
            </div>
          </div>

          <div className="login-feature-row">
            <div className="login-feature">
              <ClusterOutlined />
              <span>拓扑感知</span>
            </div>
            <div className="login-feature">
              <RadarChartOutlined />
              <span>实时监控</span>
            </div>
            <div className="login-feature">
              <ThunderboltOutlined />
              <span>快速定位</span>
            </div>
          </div>
        </section>

        <Card className="login-card">
          <div className="login-card-header">
            <div className="login-card-icon">
              <SafetyOutlined />
            </div>
            <Title level={3} style={{ margin: 0 }}>
              网络监控平台
            </Title>
            <Text type="secondary">Visualization-driven operations console</Text>
          </div>

          <Form
            name="login"
            onFinish={handleSubmit}
            autoComplete="off"
            size="large"
            layout="vertical"
          >
            <Form.Item
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="用户名"
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="密码"
              />
            </Form.Item>

            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
                className="login-submit"
              >
                登录控制台
              </Button>
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="link"
                onClick={handleInit}
                loading={initLoading}
                block
              >
                初始化系统（首次使用）
              </Button>
            </Form.Item>
          </Form>

          <Space className="login-card-footer" size={8}>
            <span />
            <Text type="secondary">Secure access · Audit ready</Text>
          </Space>
        </Card>
      </div>
    </div>
  )
}

export default Login
