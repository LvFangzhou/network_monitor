import { startTransition, useEffect, useState } from 'react'
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
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('layoutCollapsed') !== 'false')
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const token = useAuthStore((state) => state.token)
  const appTheme = useThemeStore((state) => state.mode)
  const toggleTheme = useThemeStore((state) => state.toggleMode)
  const {
    token: { colorBgLayout, colorText, colorTextSecondary },
  } = theme.useToken()

  const publicMenus = ['/alerts/history', '/alerts/audit', '/grafana']
  const allowedMenus = !token ? publicMenus : (user?.is_superuser ? ['*'] : (user?.allowed_menus || []))
  const canAccessMenu = (path: string) =>
    allowedMenus.includes('*') ||
    allowedMenus.includes(path) ||
    (path === '/device-compliance' && allowedMenus.includes('/devices')) ||
    (path.startsWith('/tacacs/') && allowedMenus.includes('/tacacs'))

  const selectedMenuKey = location.pathname

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
        { key: '/device-dictionaries', label: '字典管理' },
        { key: '/devices', label: '网络设备' },
        { key: '/device-compliance', label: '上线合规' },
        { key: '/customers', label: '客户管理' },
        { key: '/public-circuits', label: '公网管理' },
        { key: '/private-circuits', label: '专线管理' },
        { key: '/ipdb', label: 'IPDB' },
        { key: '/config-backups', label: '配置备份' },
      ],
    },
    {
      key: '/alerts',
      icon: <AlertOutlined />,
      label: '告警管理',
      children: [
        { key: '/alerts/rules', label: '告警规则' },
        { key: '/alerts/history', label: '告警历史' },
        { key: '/alerts/audit', label: '告警日志' },
        { key: '/alerts/silences', label: '告警屏蔽' },
      ],
    },
    {
      key: 'monitor-center',
      icon: <LineChartOutlined />,
      label: '监控中心',
      children: [
        { key: '/device-overview', label: '设备总览' },
        { key: '/collection-health', label: '采集健康' },
        { key: '/telemetry-status', label: 'Telemetry状态' },
        { key: '/grafana', label: '接口查询' },
        { key: '/ip-flow-query', label: 'IP查询' },
        { key: '/quality-query', label: '质量查询' },
        { key: '/traffic-query', label: '流量查询' },
        { key: '/module-info-query', label: '模块信息查询' },
        { key: '/lossless-info-query', label: '无损信息查询' },
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

  const addMenuTooltips = (items: ItemType[]): ItemType[] => items.map((item) => {
    if (!item || !('key' in item)) return item
    const label = 'label' in item ? item.label : undefined
    const children = 'children' in item && item.children ? addMenuTooltips(item.children as ItemType[]) : undefined
    return {
      ...item,
      title: typeof label === 'string' ? label : undefined,
      ...(children ? { children } : {}),
    }
  })

  const menuItems = addMenuTooltips(filterMenuItems(baseMenuItems))
  const activeMenuKey = selectedMenuKey
  const routeParentKey = (items: ItemType[], route: string): string | null => {
    for (const item of items) {
      if (!item || !('key' in item)) continue
      if ('children' in item && item.children?.length) {
        const children = item.children as ItemType[]
        const matched = children.some((child) => child && 'key' in child && (route === String(child.key) || route.startsWith(`${String(child.key)}/`)))
        if (matched) return String(item.key)
        const nested = routeParentKey(children, route)
        if (nested) return String(item.key)
      }
    }
    return null
  }
  const [openKeys, setOpenKeys] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('layoutOpenKeys') || '[]')
    } catch {
      return []
    }
  })

  useEffect(() => {
    localStorage.setItem('layoutOpenKeys', JSON.stringify(openKeys))
  }, [openKeys])

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
    const navigateSmoothly = (path: string) => {
      startTransition(() => {
        navigate(path)
      })
    }

    if (key === 'logout') {
      logout()
      navigateSmoothly('/login')
    } else if (key === 'login') {
      navigateSmoothly('/login')
    } else if (key === 'profile') {
      navigateSmoothly('/settings')
    } else if (key.startsWith('/')) {
      navigateSmoothly(key)
    }
  }

  return (
    <AntLayout data-theme={appTheme} style={{ minHeight: '100vh', background: colorBgLayout }}>
      <Sider
        className={`modern-sider${collapsed ? ' modern-sider-collapsed' : ''}`}
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={200}
        collapsedWidth={72}
        theme="dark"
        style={{
          overflow: collapsed ? 'visible' : 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          zIndex: 20,
          padding: collapsed ? '14px 6px' : '14px 12px',
          background: appTheme === 'dark'
            ? 'linear-gradient(180deg, #102a43 0%, #12395c 50%, #0f4c75 100%)'
            : 'linear-gradient(180deg, #2f66d8 0%, #3477e6 46%, #56b6f7 100%)',
          boxShadow: appTheme === 'dark'
            ? '14px 0 36px rgba(2, 8, 23, 0.24)'
            : '14px 0 36px rgba(47, 102, 216, 0.22)',
          borderRight: appTheme === 'dark'
            ? '1px solid rgba(255,255,255,0.10)'
            : '1px solid rgba(255,255,255,0.22)',
        }}
      >
        <div
          className="modern-sider-brand"
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: collapsed ? 14 : 18,
            fontWeight: 800,
            letterSpacing: collapsed ? 0 : 1,
            borderRadius: collapsed ? 18 : 20,
            marginBottom: 14,
            background: appTheme === 'dark'
              ? 'linear-gradient(135deg, rgba(255,255,255,0.18), rgba(125,211,252,0.14), rgba(16,185,129,0.12))'
              : 'linear-gradient(135deg, rgba(255,255,255,0.22), rgba(255,255,255,0.12), rgba(125,211,252,0.18))',
            border: appTheme === 'dark'
              ? '1px solid rgba(255,255,255,0.16)'
              : '1px solid rgba(255,255,255,0.26)',
            boxShadow: appTheme === 'dark'
              ? 'inset 0 1px 0 rgba(255,255,255,0.18), 0 12px 28px rgba(2, 8, 23, 0.16)'
              : 'inset 0 1px 0 rgba(255,255,255,0.28), 0 12px 28px rgba(30, 64, 175, 0.18)',
          }}
        >
          {collapsed ? '网络' : '网络运营平台'}
        </div>
        <Menu
          theme="dark"
          mode={collapsed ? 'vertical' : 'inline'}
          inlineCollapsed={false}
          selectedKeys={[activeMenuKey]}
          openKeys={collapsed ? undefined : openKeys}
          triggerSubMenuAction="hover"
          subMenuOpenDelay={0}
          subMenuCloseDelay={0.12}
          onOpenChange={(keys) => {
            if (!collapsed) setOpenKeys(keys.map(String))
          }}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0, background: 'transparent', fontWeight: 600 }}
        />
      </Sider>

      <AntLayout style={{ marginLeft: collapsed ? 72 : 200, transition: 'all 0.2s' }}>
        <Header
          style={{
            height: 68,
            lineHeight: '68px',
            padding: '0 26px',
            background: appTheme === 'dark' ? 'rgba(17, 24, 39, 0.78)' : 'rgba(255, 255, 255, 0.76)',
            backdropFilter: 'blur(18px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: appTheme === 'dark' ? '0 1px 0 rgba(148,163,184,0.10)' : '0 1px 0 rgba(15,23,42,0.06)',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
            />
            <div style={{ lineHeight: 1.2 }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: colorText }}>网络运营平台</div>
              <div style={{ fontSize: 12, color: colorTextSecondary }}>Network operations platform</div>
            </div>
          </div>

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
                <Avatar style={{ background: 'linear-gradient(135deg, #2f66d8, #38bdf8)' }} icon={<UserOutlined />} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{token ? (user?.full_name || user?.username) : '未登录'}</span>
              </div>
            </Dropdown>
          </div>
        </Header>

        <Content
          style={{
            margin: 0,
            padding: 24,
            background: 'transparent',
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
