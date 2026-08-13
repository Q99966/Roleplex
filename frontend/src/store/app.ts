import { create } from 'zustand'
import { api, Conversation, Role, setToken, User } from '../api/client'

type AppState = {
  user: User | null
  roles: Role[]
  conversations: Conversation[]
  activeConversationId: number | null
  loading: boolean
  error: string | null
  setActiveConversation: (id: number) => void
  bootstrap: () => Promise<void>
  authenticate: (mode: 'login' | 'register', form: { username: string; password: string; nickname: string }) => Promise<void>
  logout: () => void
  loadWorkspace: () => Promise<void>
}

export const useAppStore = create<AppState>((set, get) => ({
  user: null,
  roles: [],
  conversations: [],
  activeConversationId: null,
  loading: true,
  error: null,
  setActiveConversation: (activeConversationId) => set({ activeConversationId }),
  // 仅在存在 Token 时恢复会话；匿名页面避免发起无意义的 401 请求。
  bootstrap: async () => {
    if (!localStorage.getItem('roleplex_token')) {
      set({ loading: false, user: null })
      return
    }
    try {
      const user = await api.me()
      set({ user })
      await get().loadWorkspace()
    } catch {
      setToken(null)
      set({ user: null })
    } finally {
      set({ loading: false })
    }
  },
  // 将请求的加载和错误状态集中管理，保证所有认证视图行为一致。
  authenticate: async (mode, form) => {
    set({ loading: true, error: null })
    try {
      const result = mode === 'login' ? await api.login(form) : await api.register(form)
      setToken(result.access_token)
      set({ user: result.user })
      await get().loadWorkspace()
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '请求失败' })
      throw error
    } finally {
      set({ loading: false })
    }
  },
  logout: () => {
    setToken(null)
    set({ user: null, roles: [], conversations: [], activeConversationId: null })
  },
  // 保留已有的当前会话，否则将最新会话设为当前会话。
  loadWorkspace: async () => {
    const [roles, conversations] = await Promise.all([api.roles(), api.conversations()])
    set({ roles, conversations, activeConversationId: get().activeConversationId ?? conversations[0]?.id ?? null })
  },
}))
