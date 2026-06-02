import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Card, message, Typography } from 'antd'
import { UserOutlined, LockOutlined, SafetyOutlined } from '@ant-design/icons'
import type { AxiosError } from 'axios'
import { useAuthStore } from '../store/auth'
import { initAuth } from '../api/auth'

const { Title } = Typography

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
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
      }}
    >
      <Card
        style={{
          width: 400,
          borderRadius: 8,
          boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ margin: 0 }}>
            <SafetyOutlined style={{ marginRight: 8 }} />
            网络设备监控系统
          </Title>
          <p style={{ color: '#666', marginTop: 8 }}>Network Monitor System</p>
        </div>

        <Form
          name="login"
          onFinish={handleSubmit}
          autoComplete="off"
          size="large"
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
            >
              登录
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
      </Card>
    </div>
  )
}

export default Login
