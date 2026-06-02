import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { login as loginApi, getCurrentUser, type User } from '../api/auth'

interface AuthState {
  token: string | null
  user: User | null
  isInitialized: boolean
  setToken: (token: string) => void
  setUser: (user: User | null) => void
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  initAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isInitialized: false,
      
      setToken: (token) => set({ token }),
      setUser: (user) => set({ user }),
      
      login: async (username, password) => {
        const response = await loginApi(username, password)
        set({ token: response.access_token })
        // 获取用户信息
        const user = await getCurrentUser()
        set({ user })
      },
      
      logout: () => {
        set({ token: null, user: null })
      },
      
      initAuth: async () => {
        const token = useAuthStore.getState().token
        if (token) {
          try {
            const user = await getCurrentUser()
            set({ user, isInitialized: true })
            return
          } catch {
            set({ token: null, user: null, isInitialized: true })
            return
          }
        }
        set({ isInitialized: true })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({ token: state.token }),
    }
  )
)
