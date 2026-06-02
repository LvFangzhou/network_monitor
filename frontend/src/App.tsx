import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { useAuthStore } from './store/auth'
import request from './api/request'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import DeviceList from './pages/devices/DeviceList'
import DeviceDictionaryManager from './pages/devices/DeviceDictionaryManager'
import DeviceForm from './pages/devices/DeviceForm'
import DeviceDetail from './pages/devices/DeviceDetail'
import DatacenterList from './pages/datacenters/DatacenterList'
import CustomerList from './pages/resources/CustomerList'
import VendorList from './pages/resources/VendorList'
import PublicCircuitList from './pages/resources/PublicCircuitList'
import PrivateCircuitList from './pages/resources/PrivateCircuitList'
import IPDBList from './pages/resources/IPDBList'
import AlertRules from './pages/alerts/AlertRules'
import AlertHistory from './pages/alerts/AlertHistory'
import AlertSilences from './pages/alerts/AlertSilences'
import Metrics from './pages/metrics/Metrics'
import DeviceOverview from './pages/metrics/DeviceOverview'
import IPFlowQuery from './pages/metrics/IPFlowQuery'
import Settings from './pages/Settings'
import TacacsManager from './pages/TacacsManager'

const FALLBACK_MENU_PATHS = ['/dashboard', '/devices', '/device-overview', '/port-query', '/ip-flow-query']
const PUBLIC_MENU_PATHS = ['/alerts/history', '/port-query']
const ROUTE_ALIASES: Record<string, string[]> = {
  '/port-query': ['/metrics'],
  '/device-overview': ['/metrics'],
}

const getInitialRoute = (allowedMenus: string[] = []) => {
  const savedRoute = localStorage.getItem('lastVisitedRoute')
  const effectiveMenus = allowedMenus.includes('*')
    ? FALLBACK_MENU_PATHS
    : (allowedMenus.length ? allowedMenus : FALLBACK_MENU_PATHS)

  const normalizedSavedRoute = savedRoute === '/metrics' ? '/port-query' : savedRoute
  if (
    normalizedSavedRoute &&
    normalizedSavedRoute !== '/login' &&
    effectiveMenus.some((path) => normalizedSavedRoute === path || normalizedSavedRoute.startsWith(`${path}/`))
  ) {
    return normalizedSavedRoute
  }

  return effectiveMenus[0] || '/dashboard'
}

const canAccessRoute = (allowedMenus: string[], routePath: string) =>
  allowedMenus.includes('*') ||
  allowedMenus.some((path) => routePath === path || routePath.startsWith(`${path}/`)) ||
  (ROUTE_ALIASES[routePath] || []).some((path) => allowedMenus.includes(path))

const HomeRedirect = () => {
  const user = useAuthStore((state) => state.user)
  const allowedMenus = user?.is_superuser ? ['*'] : (user?.allowed_menus || FALLBACK_MENU_PATHS)
  return <Navigate to={getInitialRoute(allowedMenus)} replace />
}

// 路由守卫组件
const PrivateRoute = ({ children }: { children: React.ReactNode }) => {
  const { token, isInitialized } = useAuthStore()
  const location = useLocation()
  
  if (!isInitialized) {
    return <div style={{ padding: 50, textAlign: 'center' }}>加载中...</div>
  }
  
  const isPublicRoute = PUBLIC_MENU_PATHS.some((path) => location.pathname === path || location.pathname.startsWith(`${path}/`))
  if (!token && !isPublicRoute) {
    return <Navigate to="/login" replace />
  }
  
  return <>{children}</>
}

const MenuRoute = ({ menuPath, children }: { menuPath: string; children: React.ReactNode }) => {
  const token = useAuthStore((state) => state.token)
  const user = useAuthStore((state) => state.user)
  const allowedMenus = !token ? PUBLIC_MENU_PATHS : (user?.is_superuser ? ['*'] : (user?.allowed_menus || FALLBACK_MENU_PATHS))

  if (!canAccessRoute(allowedMenus, menuPath)) {
    return <Navigate to={getInitialRoute(allowedMenus)} replace />
  }

  return <>{children}</>
}

const RouteAuditTracker = () => {
  const location = useLocation()
  const token = useAuthStore((state) => state.token)

  useEffect(() => {
    if (!token || location.pathname === '/login') return
    const currentPath = `${location.pathname}${location.search}`
    const timer = window.setTimeout(() => {
      request.post('/auth/audit/menu-visit', {
        path: currentPath,
        title: document.title,
      }).catch(() => undefined)
    }, 200)
    return () => window.clearTimeout(timer)
  }, [location.pathname, location.search, token])

  return null
}

function App() {
  const { initAuth } = useAuthStore()

  useEffect(() => {
    initAuth()
  }, [initAuth])

  return (
    <BrowserRouter>
      <RouteAuditTracker />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <PrivateRoute>
              <Layout />
            </PrivateRoute>
          }
        >
          <Route index element={<HomeRedirect />} />
          <Route path="dashboard" element={<MenuRoute menuPath="/dashboard"><Dashboard /></MenuRoute>} />
          <Route path="devices" element={<MenuRoute menuPath="/devices"><DeviceList /></MenuRoute>} />
          <Route path="device-dictionaries" element={<MenuRoute menuPath="/device-dictionaries"><DeviceDictionaryManager /></MenuRoute>} />
          <Route path="customers" element={<MenuRoute menuPath="/customers"><CustomerList /></MenuRoute>} />
          <Route path="datacenters" element={<MenuRoute menuPath="/datacenters"><DatacenterList /></MenuRoute>} />
          <Route path="vendors" element={<MenuRoute menuPath="/vendors"><VendorList /></MenuRoute>} />
          <Route path="public-circuits" element={<MenuRoute menuPath="/public-circuits"><PublicCircuitList /></MenuRoute>} />
          <Route path="private-circuits" element={<MenuRoute menuPath="/private-circuits"><PrivateCircuitList /></MenuRoute>} />
          <Route path="circuits" element={<Navigate to="/public-circuits" replace />} />
          <Route path="ipdb" element={<MenuRoute menuPath="/ipdb"><IPDBList /></MenuRoute>} />
          <Route path="devices/add" element={<MenuRoute menuPath="/devices"><DeviceForm /></MenuRoute>} />
          <Route path="devices/edit/:id" element={<MenuRoute menuPath="/devices"><DeviceForm /></MenuRoute>} />
          <Route path="devices/:id" element={<MenuRoute menuPath="/devices"><DeviceDetail /></MenuRoute>} />
          <Route path="alerts/rules" element={<MenuRoute menuPath="/alerts/rules"><AlertRules /></MenuRoute>} />
          <Route path="alerts/history" element={<MenuRoute menuPath="/alerts/history"><AlertHistory /></MenuRoute>} />
          <Route path="alerts/silences" element={<MenuRoute menuPath="/alerts/silences"><AlertSilences /></MenuRoute>} />
          <Route path="port-query" element={<MenuRoute menuPath="/port-query"><Metrics /></MenuRoute>} />
          <Route path="ip-flow-query" element={<MenuRoute menuPath="/ip-flow-query"><IPFlowQuery /></MenuRoute>} />
          <Route path="device-overview" element={<MenuRoute menuPath="/device-overview"><DeviceOverview /></MenuRoute>} />
          <Route path="metrics" element={<Navigate to="/port-query" replace />} />
          <Route path="settings" element={<MenuRoute menuPath="/settings"><Settings /></MenuRoute>} />
          <Route path="tacacs" element={<Navigate to="/tacacs/config" replace />} />
          <Route path="tacacs/config" element={<MenuRoute menuPath="/tacacs"><TacacsManager activeTab="config" /></MenuRoute>} />
          <Route path="tacacs/logs" element={<MenuRoute menuPath="/tacacs"><TacacsManager activeTab="logs" /></MenuRoute>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
