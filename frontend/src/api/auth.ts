import request from './request'

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export interface User {
  id: number
  username: string
  email: string
  full_name: string
  phone?: string | null
  department?: string | null
  is_active: boolean
  is_superuser: boolean
  read_only: boolean
  allowed_menus: string[]
  roles: string[]
  last_login?: string | null
  online?: boolean
  last_offline_at?: string | null
}

export interface AuditLog {
  id: number
  request_id?: string
  user_id?: number | null
  username: string
  action: string
  menu?: string
  method: string
  path: string
  query_params?: Record<string, any>
  resource_type?: string | null
  resource_id?: string | null
  request_body?: any
  response_status?: number
  success: boolean
  client_ip?: string
  user_agent?: string
  error_message?: string | null
  created_at?: string
}

export const login = async (username: string, password: string): Promise<LoginResponse> => {
  const formData = new URLSearchParams()
  formData.append('username', username)
  formData.append('password', password)
  
  const response = await request.post('/auth/login', formData, {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  })
  return response as unknown as LoginResponse
}

export const getCurrentUser = async (): Promise<User> => {
  return await request.get('/auth/me') as User
}

export const initAuth = async () => {
  return await request.post('/auth/init')
}

export const getUsers = async (): Promise<{ total: number; items: User[] }> => {
  return await request.get('/auth/users') as { total: number; items: User[] }
}

export const createUser = async (data: Record<string, any>): Promise<User> => {
  return await request.post('/auth/users', data) as User
}

export const updateUser = async (id: number, data: Record<string, any>): Promise<User> => {
  return await request.put(`/auth/users/${id}`, data) as User
}

export const updateCurrentUser = async (data: Record<string, any>): Promise<User> => {
  return await request.put('/auth/me', data) as User
}

export const deleteUser = async (id: number): Promise<void> => {
  await request.delete(`/auth/users/${id}`)
}

export const getMenuOptions = async (): Promise<{ items: Array<{ label: string; value: string }> }> => {
  return await request.get('/auth/menu-options') as { items: Array<{ label: string; value: string }> }
}

export const getAuditLogs = async (params?: {
  skip?: number
  limit?: number
  username?: string
  menu?: string
  action?: string
  method?: string
  path?: string
  success?: boolean
}): Promise<{ total: number; items: AuditLog[] }> => {
  return await request.get('/auth/audit-logs', { params }) as { total: number; items: AuditLog[] }
}
