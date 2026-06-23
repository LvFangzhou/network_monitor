import React, { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from 'react-query'
import App from './App'
import { useThemeStore } from './store/theme'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

const AppProviders = () => {
  const mode = useThemeStore((state) => state.mode)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode)
  }, [mode])

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          borderRadius: 12,
          borderRadiusLG: 16,
          borderRadiusSM: 8,
          colorPrimary: '#2f66d8',
          colorInfo: '#2f66d8',
          colorSuccess: '#10b981',
          colorWarning: '#f59e0b',
          colorError: '#ef4444',
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
          boxShadow: mode === 'dark'
            ? '0 16px 40px rgba(0, 0, 0, 0.32)'
            : '0 16px 40px rgba(15, 23, 42, 0.08)',
          boxShadowSecondary: mode === 'dark'
            ? '0 10px 30px rgba(0, 0, 0, 0.28)'
            : '0 10px 30px rgba(15, 23, 42, 0.06)',
          colorBgLayout: mode === 'dark' ? '#0b1020' : '#f5f7fb',
          colorBgContainer: mode === 'dark' ? '#111827' : '#ffffff',
          colorBorder: mode === 'dark' ? 'rgba(148, 163, 184, 0.16)' : 'rgba(15, 23, 42, 0.08)',
        },
        components: {
          Card: {
            borderRadiusLG: 16,
            headerFontSize: 15,
            boxShadowTertiary: mode === 'dark'
              ? '0 16px 40px rgba(0, 0, 0, 0.32)'
              : '0 16px 40px rgba(15, 23, 42, 0.08)',
          },
          Button: {
            borderRadius: 10,
            controlHeight: 34,
          },
          Input: {
            borderRadius: 10,
          },
          Select: {
            borderRadius: 10,
          },
          Modal: {
            borderRadiusLG: 16,
          },
          Table: {
            borderColor: mode === 'dark' ? 'rgba(148, 163, 184, 0.12)' : 'rgba(15, 23, 42, 0.06)',
            headerBg: mode === 'dark' ? 'rgba(15, 23, 42, 0.84)' : '#f8fafc',
            headerColor: mode === 'dark' ? 'rgba(226, 232, 240, 0.88)' : '#475569',
            rowHoverBg: mode === 'dark' ? 'rgba(47, 102, 216, 0.12)' : 'rgba(47, 102, 216, 0.06)',
          },
          Menu: {
            borderRadius: 12,
            itemBorderRadius: 12,
            darkItemBg: 'transparent',
            darkSubMenuItemBg: 'transparent',
            darkItemSelectedBg: 'rgba(59, 130, 246, 0.22)',
            darkItemHoverBg: 'rgba(148, 163, 184, 0.12)',
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppProviders />
    </QueryClientProvider>
  </React.StrictMode>,
)
