import { create } from 'zustand'
import { api, Conversation, Role, setToken, User, ModelConfig } from '../api/client'

type AppState = {
  user: User | null
  roles: Role[]
  conversations: Conversation[]
  modelConfigs: ModelConfig[]
  activeConversationId: number | null
  loading: boolean
  error: string | null
  setActiveConversation: (id: number | null) => void
  bootstrap: () => Promise<void>
  authenticate: (mode: 'login' | 'register', form: { username: string; password: string; nickname: string }) => Promise<void>
  logout: () => void
  loadWorkspace: () => Promise<void>
  
  // 模型配置操作
  createModelConfig: (body: Parameters<typeof api.createModelConfig>[0]) => Promise<void>
  deleteModelConfig: (id: number) => Promise<void>
  
  // 角色操作
  createRole: (body: Parameters<typeof api.createRole>[0]) => Promise<void>
  updateRole: (id: number, body: Parameters<typeof api.updateRole>[1]) => Promise<void>
  deleteRole: (id: number) => Promise<void>
  
  // 会话操作
  createConversation: (body: Parameters<typeof api.createConversation>[0]) => Promise<void>
  deleteConversation: (id: number) => Promise<void>
  updateConversationPreferences: (id: number, pinned?: boolean, archived?: boolean) => Promise<void>
}

export const useAppStore = create<AppState>((set, get) => ({
  user: null,
  roles: [],
  conversations: [],
  modelConfigs: [],
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
    set({ user: null, roles: [], conversations: [], modelConfigs: [], activeConversationId: null })
  },
  
  // 保留已有的当前会话，否则将最新会话设为当前会话。
  loadWorkspace: async () => {
    try {
      const user = get().user
      const [roles, conversations, modelConfigs] = await Promise.all([
        api.roles(),
        api.conversations(),
        user?.is_owner 
          ? api.modelConfigs().catch(() => []) 
          : Promise.resolve([])
      ])
      set({ roles, conversations, modelConfigs })
      
      // 如果 activeConversationId 不存在或在会话列表中找不到，则尝试默认选中第一个
      const currentActiveId = get().activeConversationId
      if (currentActiveId !== null && !conversations.some(c => c.id === currentActiveId)) {
        set({ activeConversationId: null })
      }
    } catch (err) {
      console.error('Failed to load workspace data:', err)
    }
  },

  // 模型配置操作实现
  createModelConfig: async (body) => {
    await api.createModelConfig(body)
    await get().loadWorkspace()
  },
  deleteModelConfig: async (id) => {
    await api.deleteModelConfig(id)
    await get().loadWorkspace()
  },

  // 角色操作实现
  createRole: async (body) => {
    await api.createRole(body)
    await get().loadWorkspace()
  },
  updateRole: async (id, body) => {
    await api.updateRole(id, body)
    await get().loadWorkspace()
  },
  deleteRole: async (id) => {
    await api.deleteRole(id)
    await get().loadWorkspace()
  },

  // 会话操作实现
  createConversation: async (body) => {
    const conv = await api.createConversation(body)
    set({ activeConversationId: conv.id })
    await get().loadWorkspace()
  },
  deleteConversation: async (id) => {
    await api.deleteConversation(id)
    if (get().activeConversationId === id) {
      set({ activeConversationId: null })
    }
    await get().loadWorkspace()
  },
  updateConversationPreferences: async (id, pinned, archived) => {
    await api.updateConversationPreferences(id, pinned, archived)
    await get().loadWorkspace()
  }
}))
