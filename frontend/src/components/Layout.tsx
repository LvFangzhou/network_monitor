import { useEffect, useState } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import {
  Layout as AntLayout,
  Menu,
  Button,
  Avatar,
  Dropdown,
  Tooltip,
  theme,
} from 'antd'
import {
  DashboardOutlined,
  DesktopOutlined,
  AlertOutlined,
  LineChartOutlined,
  SettingOutlined,
  SafetyCertificateOutlined,
  LogoutOutlined,
  UserOutlined,
  MoonOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SunOutlined,
} from '@ant-design/icons'
import type { ItemType } from 'antd/es/menu/interface'
import { useAuthStore } from '../store/auth'
import { useThemeStore } from '../store/theme'

const { Header, Sider, Content } = AntLayout

const Layout = () => {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('layoutCollapsed') === 'true')
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const token = useAuthStore((state) => state.token)
  const appTheme = useThemeStore((state) => state.mode)
  const toggleTheme = useThemeStore((state) => state.toggleMode)
  const {
    token: { colorBgContainer, colorBgLayout, borderRadiusLG },
  } = theme.useToken()

  const publicMenus = ['/alerts/history', '/port-query']
  const allowedMenus = !token ? publicMenus : (user?.is_superuser ? ['*'] : (user?.allowed_menus || []))
  const canAccessMenu = (path: string) =>
    allowedMenus.includes('*') ||
    allowedMenus.includes(path) ||
    (path.startsWith('/tacacs/') && allowedMenus.includes('/tacacs')) ||
    ((path === '/port-query' || path === '/device-overview') && allowedMenus.includes('/metrics'))

  const selectedMenuKey = location.pathname === '/metrics' ? '/port-query' : location.pathname

  const filterMenuItems = (items: ItemType[]): ItemType[] => {
    return items.reduce<ItemType[]>((result, item) => {
      if (!item || !('key' in item)) {
        return result
      }

      if (!('children' in item) || !item.children) {
        return canAccessMenu(String(item.key)) ? [...result, item] : result
      }

      const filteredChildren = filterMenuItems(item.children as ItemType[])
      if (!filteredChildren.length) {
        return result
      }

      return [...result, { ...item, children: filteredChildren }]
    }, [])
  }

  useEffect(() => {
    if (location.pathname !== '/login') {
      localStorage.setItem('lastVisitedRoute', location.pathname)
    }
  }, [location.pathname])

  useEffect(() => {
    localStorage.setItem('layoutCollapsed', String(collapsed))
  }, [collapsed])

  const baseMenuItems: ItemType[] = [
    {
      key: '/dashboard',
      icon: <DashboardOutlined />,
      label: '仪表盘',
    },
    {
      key: 'resource-management',
      icon: <DesktopOutlined />,
      label: '资源管理',
      children: [
        { key: '/devices', label: '网络设备' },
        { key: '/device-dictionaries', label: '字典管理' },
        { key: '/customers', label: '客户管理' },
        { key: '/vendors', label: '供应商管理' },
        { key: '/datacenters', label: '机房管理' },
        { key: '/public-circuits', label: '公网管理' },
        { key: '/private-circuits', label: '专线管理' },
        { key: '/ipdb', label: 'IPDB' },
      ],
    },
    {
      key: '/alerts',
      icon: <AlertOutlined />,
      label: '告警管理',
      children: [
        { key: '/alerts/rules', label: '告警规则' },
        { key: '/alerts/history', label: '告警历史' },
        { key: '/alerts/silences', label: '告警屏蔽' },
      ],
    },
    {
      key: 'monitor-center',
      icon: <LineChartOutlined />,
      label: '监控中心',
      children: [
        { key: '/device-overview', label: '设备总览' },
        { key: '/port-query', label: '端口查询' },
        { key: '/ip-flow-query', label: 'IP流量查询' },
      ],
    },
    {
      key: 'tacacs-management',
      icon: <SafetyCertificateOutlined />,
      label: 'Tacacs管理',
      children: [
        { key: '/tacacs/config', label: '配置管理' },
        { key: '/tacacs/logs', label: '操作日志' },
      ],
    },
    {
      key: '/settings',
      icon: <SettingOutlined />,
      label: '系统设置',
    },
  ]

  const menuItems = filterMenuItems(baseMenuItems)

  const userMenuItems = token ? [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人中心',
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
    },
  ] : [
    {
      key: 'login',
      icon: <UserOutlined />,
      label: '登录',
    },
  ]

  const handleMenuClick = ({ key }: { key: string }) => {
    if (key === 'logout') {
      logout()
      navigate('/login')
    } else if (key === 'login') {
      navigate('/login')
    } else if (key === 'profile') {
      navigate('/settings')
    } else if (key.startsWith('/')) {
      navigate(key)
    }
  }

  return (
    <AntLayout style={{ minHeight: '100vh', background: colorBgLayout }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        theme="dark"
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
        }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: collapsed ? 14 : 18,
            fontWeight: 'bold',
            borderBottom: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          {collapsed ? 'NM' : '网络监控'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedMenuKey]}
          defaultOpenKeys={['resource-management', '/alerts', 'monitor-center', 'tacacs-management']}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0 }}
        />
      </Sider>

      <AntLayout style={{ marginLeft: collapsed ? 80 : 200, transition: 'all 0.2s' }}>
        <Header
          style={{
            height: 64,
            lineHeight: '64px',
            padding: '0 24px',
            background: colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
            position: 'sticky',
            top: 0,
            zIndex: 30,
            flexShrink: 0,
          }}
        >
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
          />

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: '100%', flexShrink: 0, whiteSpace: 'nowrap' }}>
            <Tooltip title={appTheme === 'dark' ? '切换白天模式' : '切换黑暗模式'}>
              <Button
                type="text"
                icon={appTheme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
                onClick={toggleTheme}
              />
            </Tooltip>
            <Dropdown
              menu={{ items: userMenuItems, onClick: handleMenuClick }}
              placement="bottomRight"
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', maxWidth: 180 }}>
                <Avatar icon={<UserOutlined />} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{token ? (user?.full_name || user?.username) : '未登录'}</span>
              </div>
            </Dropdown>
          </div>
        </Header>

        <Content
          style={{
            margin: 24,
            padding: 24,
            background: colorBgContainer,
            borderRadius: borderRadiusLG,
            minHeight: 280,
          }}
        >
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  )
}

export default Layout
