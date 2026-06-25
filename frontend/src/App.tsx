import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import React, { Suspense, lazy, useEffect } from 'react'
import { Spin } from 'antd'
import { useAuthStore } from './store/auth'
import request from './api/request'
import Layout from './components/Layout'

const loadLogin = () => import('./pages/Login')
const loadDashboard = () => import('./pages/Dashboard')
const loadDeviceList = () => import('./pages/devices/DeviceList')
const loadDeviceDictionaryManager = () => import('./pages/devices/DeviceDictionaryManager')
const loadDeviceForm = () => import('./pages/devices/DeviceForm')
const loadDeviceDetail = () => import('./pages/devices/DeviceDetail')
const loadDatacenterList = () => import('./pages/datacenters/DatacenterList')
const loadCustomerList = () => import('./pages/resources/CustomerList')
const loadVendorList = () => import('./pages/resources/VendorList')
const loadPublicCircuitList = () => import('./pages/resources/PublicCircuitList')
const loadPrivateCircuitList = () => import('./pages/resources/PrivateCircuitList')
const loadIPDBList = () => import('./pages/resources/IPDBList')
const loadAlertRules = () => import('./pages/alerts/AlertRules')
const loadAlertHistory = () => import('./pages/alerts/AlertHistory')
const loadAlertAudit = () => import('./pages/alerts/AlertAudit')
const loadAlertSilences = () => import('./pages/alerts/AlertSilences')
const loadMetrics = () => import('./pages/metrics/Metrics')
const loadDeviceOverview = () => import('./pages/metrics/DeviceOverview')
const loadIPFlowQuery = () => import('./pages/metrics/IPFlowQuery')
const loadModuleInfoQuery = () => import('./pages/metrics/ModuleInfoQuery')
const loadLosslessInfoQuery = () => import('./pages/metrics/LosslessInfoQuery')
const loadSettings = () => import('./pages/Settings')
const loadTacacsManager = () => import('./pages/TacacsManager')
const loadConfigBackups = () => import('./pages/ConfigBackups')

const Login = lazy(loadLogin)
const Dashboard = lazy(loadDashboard)
const DeviceList = lazy(loadDeviceList)
const DeviceDictionaryManager = lazy(loadDeviceDictionaryManager)
const DeviceForm = lazy(loadDeviceForm)
const DeviceDetail = lazy(loadDeviceDetail)
const DatacenterList = lazy(loadDatacenterList)
const CustomerList = lazy(loadCustomerList)
const VendorList = lazy(loadVendorList)
const PublicCircuitList = lazy(loadPublicCircuitList)
const PrivateCircuitList = lazy(loadPrivateCircuitList)
const IPDBList = lazy(loadIPDBList)
const AlertRules = lazy(loadAlertRules)
const AlertHistory = lazy(loadAlertHistory)
const AlertAudit = lazy(loadAlertAudit)
const AlertSilences = lazy(loadAlertSilences)
const Metrics = lazy(loadMetrics)
const DeviceOverview = lazy(loadDeviceOverview)
const IPFlowQuery = lazy(loadIPFlowQuery)
const ModuleInfoQuery = lazy(loadModuleInfoQuery)
const LosslessInfoQuery = lazy(loadLosslessInfoQuery)
const Settings = lazy(loadSettings)
const TacacsManager = lazy(loadTacacsManager)
const ConfigBackups = lazy(loadConfigBackups)

const preloadRouteModules = () => {
  void Promise.allSettled([
    loadDashboard(),
    loadDeviceList(),
    loadDeviceDictionaryManager(),
    loadDatacenterList(),
    loadCustomerList(),
    loadVendorList(),
    loadPublicCircuitList(),
    loadPrivateCircuitList(),
    loadIPDBList(),
    loadAlertRules(),
    loadAlertHistory(),
    loadAlertAudit(),
    loadAlertSilences(),
    loadMetrics(),
    loadDeviceOverview(),
    loadIPFlowQuery(),
    loadModuleInfoQuery(),
    loadLosslessInfoQuery(),
    loadSettings(),
    loadTacacsManager(),
    loadConfigBackups(),
  ])
}

const FALLBACK_MENU_PATHS = ['/dashboard', '/devices', '/device-overview', '/port-query', '/ip-flow-query', '/module-info-query', '/lossless-info-query', '/config-backups']
const PUBLIC_MENU_PATHS = ['/alerts/history', '/alerts/audit', '/port-query']
const ROUTE_ALIASES: Record<string, string[]> = {
  '/port-query': ['/metrics'],
  '/device-overview': ['/metrics'],
  '/module-info-query': ['/metrics'],
  '/lossless-info-query': ['/metrics'],
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

const RouteFallback = () => (
  <div className="route-spin-fallback">
    <div className="route-spin-card">
      <Spin size="large" />
    </div>
  </div>
)

function App() {
  const { initAuth } = useAuthStore()

  useEffect(() => {
    initAuth()
    const timer = window.setTimeout(() => {
      preloadRouteModules()
    }, 300)
    return () => window.clearTimeout(timer)
  }, [initAuth])

  return (
    <BrowserRouter>
      <RouteAuditTracker />
      <Suspense fallback={<RouteFallback />}>
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
            <Route path="alerts/audit" element={<MenuRoute menuPath="/alerts/audit"><AlertAudit /></MenuRoute>} />
            <Route path="alerts/silences" element={<MenuRoute menuPath="/alerts/silences"><AlertSilences /></MenuRoute>} />
            <Route path="port-query" element={<MenuRoute menuPath="/port-query"><Metrics /></MenuRoute>} />
            <Route path="ip-flow-query" element={<MenuRoute menuPath="/ip-flow-query"><IPFlowQuery /></MenuRoute>} />
            <Route path="device-overview" element={<MenuRoute menuPath="/device-overview"><DeviceOverview /></MenuRoute>} />
            <Route path="module-info-query" element={<MenuRoute menuPath="/module-info-query"><ModuleInfoQuery /></MenuRoute>} />
            <Route path="lossless-info-query" element={<MenuRoute menuPath="/lossless-info-query"><LosslessInfoQuery /></MenuRoute>} />
            <Route path="config-backups" element={<MenuRoute menuPath="/config-backups"><ConfigBackups /></MenuRoute>} />
            <Route path="metrics" element={<Navigate to="/port-query" replace />} />
            <Route path="settings" element={<MenuRoute menuPath="/settings"><Settings /></MenuRoute>} />
            <Route path="tacacs" element={<Navigate to="/tacacs/config" replace />} />
            <Route path="tacacs/config" element={<MenuRoute menuPath="/tacacs"><TacacsManager activeTab="config" /></MenuRoute>} />
            <Route path="tacacs/logs" element={<MenuRoute menuPath="/tacacs"><TacacsManager activeTab="logs" /></MenuRoute>} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

export default App
