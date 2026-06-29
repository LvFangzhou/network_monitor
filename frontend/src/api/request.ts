import axios, { AxiosError, AxiosInstance } from 'axios'
import { message } from 'antd'
import { useAuthStore } from '../store/auth'

const POST_LOGIN_REDIRECT_KEY = 'postLoginRedirect'

// 创建 axios 实例
const request: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    const retryConfig = config as typeof config & { metadata?: { retryCount?: number } }
    retryConfig.metadata = retryConfig.metadata || {}
    retryConfig.metadata.retryCount = retryConfig.metadata.retryCount || 0
    const token = useAuthStore.getState().token
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    return response.data
  },
  async (error: AxiosError) => {
    const config = error.config as (AxiosError['config'] & { metadata?: { retryCount?: number } }) | undefined
    const status = error.response?.status
    const shouldRetry = Boolean(
      config &&
      config.method?.toLowerCase() === 'get' &&
      !config.url?.includes('/auth/login') &&
      (status === 502 || status === 503 || status === 504 || !error.response) &&
      (config.metadata?.retryCount || 0) < 1,
    )

    if (shouldRetry && config) {
      config.metadata = config.metadata || {}
      config.metadata.retryCount = (config.metadata.retryCount || 0) + 1
      await new Promise((resolve) => window.setTimeout(resolve, 400))
      return request(config)
    }

    if (error.response) {
      const { status } = error.response
      const requestUrl = error.config?.url || ''
      const isLoginRequest = requestUrl.includes('/auth/login')
      
      if (status === 401 && !isLoginRequest) {
        message.error('登录已过期，请重新登录')
        useAuthStore.getState().logout()
        const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
        if (currentPath && currentPath !== '/login' && !currentPath.startsWith('/login?')) {
          sessionStorage.setItem(POST_LOGIN_REDIRECT_KEY, currentPath)
        }
        window.location.href = `/login${currentPath && currentPath !== '/login' ? `?redirect=${encodeURIComponent(currentPath)}` : ''}`
      }
      // 422 错误让调用方处理，不在这里显示
    } else {
      message.error('网络错误，请检查网络连接')
    }
    return Promise.reject(error)
  }
)

export default request
