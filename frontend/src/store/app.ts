import { create } from 'zustand'
import { api, Conversation, getAuthEpoch, getPasswordResetRequired, Role, setPasswordResetRequired, setToken, User, ModelConfig, WorldSummary, WorkspaceBinding, WorkspaceCapabilities } from '../api/client'
import { navigateToConversation } from '../router'

type AppState = {
  user: User | null
  passwordResetRequired: boolean
  worldName: string
  worlds: WorldSummary[]
  worldSwitchingSupported: boolean
  switchingWorld: string | null
  /** 仅存活角色：侧边栏、成员选择器和统计都只应看到这些。 */
  roles: Role[]
  /** 按 id 索引的全部角色（含墓碑），供历史消息按 sender_id 查出原名称与头像。 */
  roleDirectory: Record<number, Role>
  conversations: Conversation[]
  /** 回收站中的会话，仅在打开回收站时按需加载。 */
  deletedConversations: Conversation[]
  modelConfigs: ModelConfig[]
  workspaceBindings: WorkspaceBinding[]
  workspaceCapabilities: WorkspaceCapabilities | null
  activeConversationId: number | null
  loading: boolean
  error: string | null
  setActiveConversation: (id: number | null) => void
  bootstrap: () => Promise<void>
  authenticate: (mode: 'login' | 'register', form: { username: string; password: string; nickname: string }) => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  logout: () => void
  loadWorkspace: () => Promise<void>
  loadWorlds: () => Promise<void>
  switchWorld: (name: string) => Promise<void>
  loadWorkspaceBindings: () => Promise<void>
  createWorkspaceBinding: (body: Parameters<typeof api.createWorkspace>[0]) => Promise<void>
  updateWorkspaceBinding: (id: number, body: Parameters<typeof api.updateWorkspace>[1]) => Promise<void>
  validateWorkspaceBinding: (id: number) => Promise<void>
  deleteWorkspaceBinding: (id: number) => Promise<void>

  // 模型配置操作
  createModelConfig: (body: Parameters<typeof api.createModelConfig>[0]) => Promise<void>
  deleteModelConfig: (id: number) => Promise<void>

  // 角色操作
  createRole: (body: Parameters<typeof api.createRole>[0]) => Promise<void>
  updateRole: (id: number, body: Parameters<typeof api.updateRole>[1]) => Promise<void>
  deleteRole: (id: number) => Promise<void>

  // 会话操作
  createConversation: (body: Parameters<typeof api.createConversation>[0]) => Promise<void>
  updateConversationMembers: (id: number, roleIds: number[], expectedRevision: number) => Promise<void>
  deleteConversation: (id: number) => Promise<void>
  loadDeletedConversations: () => Promise<void>
  restoreConversation: (id: number) => Promise<void>
  updateConversationPreferences: (id: number, pinned?: boolean, archived?: boolean) => Promise<void>
}

export const useAppStore = create<AppState>((set, get) => ({
  user: null,
  passwordResetRequired: false,
  worldName: 'default',
  worlds: [],
  worldSwitchingSupported: false,
  switchingWorld: null,
  roles: [],
  roleDirectory: {},
  conversations: [],
  deletedConversations: [],
  modelConfigs: [],
  workspaceBindings: [],
  workspaceCapabilities: null,
  activeConversationId: null,
  loading: true,
  error: null,
  setActiveConversation: (activeConversationId) => set({ activeConversationId }),

  /** 仅恢复当前 Token 的登录身份；旧认证请求不能覆盖新的登录操作。 */
  bootstrap: async () => {
    const epoch = getAuthEpoch()
    const health = await api.health().catch(() => null)
    if (epoch !== getAuthEpoch()) return
    if (health) set({ worldName: health.world_name })
    if (!localStorage.getItem('roleplex_token')) {
      set({ loading: false, user: null, passwordResetRequired: false })
      return
    }
    try {
      const user = await api.me()
      if (epoch !== getAuthEpoch()) return
      // 刷新页面后本地标记决定是否直接进入重置流程；标记丢失时 loadWorkspace
      // 会收到 403 PASSWORD_RESET_REQUIRED 并自行切回重置流程。
      const pending = getPasswordResetRequired()
      set({ user, passwordResetRequired: pending })
      if (!pending) {
        await get().loadWorkspace()
        if (user.is_owner) await get().loadWorlds()
      }
    } catch {
      if (epoch !== getAuthEpoch()) return
      get().logout()
      set({ loading: false })
    } finally {
      if (epoch === getAuthEpoch()) set({ loading: false })
    }
  },

  // 将请求的加载和错误状态集中管理，保证所有认证视图行为一致。
  authenticate: async (mode, form) => {
    set({ loading: true, error: null })
    try {
      const result = mode === 'login' ? await api.login(form) : await api.register(form)
      setToken(result.access_token)
      setPasswordResetRequired(result.password_reset_required)
      set({ user: result.user, passwordResetRequired: result.password_reset_required })
      // 待改密时不加载工作台：除改密和查看本人资料外的接口都会被服务端拒绝。
      if (!result.password_reset_required) {
        await get().loadWorkspace()
        if (result.user.is_owner) await get().loadWorlds()
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '请求失败' })
      throw error
    } finally {
      set({ loading: false })
    }
  },

  /**
   * 提交改密，成功后用新 Token 接管会话并进入工作台。
   *
   * 服务端在改密时递增 Token 版本，旧 Token（含待改密标记的那一个）立即失效，
   * 因此必须用响应里的新 Token 替换本地 Token，否则后续请求会被判为已撤销。
   */
  changePassword: async (currentPassword, newPassword) => {
    set({ loading: true, error: null })
    try {
      const result = await api.changePassword({ current_password: currentPassword, new_password: newPassword })
      setToken(result.access_token)
      setPasswordResetRequired(false)
      set({ user: result.user, passwordResetRequired: false })
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
    setPasswordResetRequired(false)
    set({ user: null, passwordResetRequired: false, roles: [], roleDirectory: {}, conversations: [], deletedConversations: [], modelConfigs: [], workspaceBindings: [], workspaceCapabilities: null, activeConversationId: null })
  },

  /** 加载世界列表；兼容数据库模式仍显示当前世界，但切换控件保持禁用。 */
  loadWorlds: async () => {
    const epoch = getAuthEpoch()
    try {
      const result = await api.worlds()
      if (epoch !== getAuthEpoch() || get().switchingWorld) return
      set({
        worldName: result.current,
        worlds: result.items,
        worldSwitchingSupported: result.switching_supported,
      })
    } catch {
      if (epoch !== getAuthEpoch() || get().switchingWorld) return
      set({ worlds: [], worldSwitchingSupported: false })
    }
  },

  /** 请求包装器切换世界，等待新健康检查后清除跨世界 Token 并重新加载。 */
  switchWorld: async (name) => {
    if (!name || name === get().worldName || get().switchingWorld) return
    set({ switchingWorld: name, error: null })
    try {
      await api.switchWorld(name)
      const deadline = Date.now() + 60_000
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        const health = await api.health().catch(() => null)
        if (health?.world_name !== name) continue
        setToken(null)
        setPasswordResetRequired(false)
        window.location.hash = '#/auth'
        window.location.reload()
        return
      }
      throw new Error('世界切换超时，请检查后端包装器日志')
    } catch (error) {
      const message = error instanceof Error ? error.message : '世界切换失败'
      set({ switchingWorld: null, error: message })
      window.alert(message)
    }
  },

  /** 重新读取当前 World 的能力与 Workspace Binding；切换 World 后不得复用旧列表。 */
  loadWorkspaceBindings: async () => {
    const epoch = getAuthEpoch()
    const world = get().worldName
    if (!get().user?.is_owner) {
      set({ workspaceBindings: [], workspaceCapabilities: null })
      return
    }
    const [capabilities, bindings] = await Promise.all([
      api.workspaceCapabilities(),
      api.workspaces(),
    ])
    if (epoch !== getAuthEpoch() || world !== get().worldName || get().switchingWorld) return
    set({ workspaceCapabilities: capabilities, workspaceBindings: bindings })
  },
  createWorkspaceBinding: async (body) => {
    await api.createWorkspace(body)
    await get().loadWorkspaceBindings()
  },
  updateWorkspaceBinding: async (id, body) => {
    const epoch = getAuthEpoch()
    if (body.active === false) {
      const preview = await api.cleanupPreview('workspace', id)
      if (epoch !== getAuthEpoch()) return
      if (preview.items.length && !confirm(`停用工作区会逐项回收所有关联进程：\n${preview.items.map((item) => `会话 ${item.conversation_id} · ${item.id.slice(0, 8)}`).join('\n')}\n继续吗？`)) return
      body = { ...body, confirm_cleanup: true }
    }
    if (epoch !== getAuthEpoch()) return
    await api.updateWorkspace(id, body)
    if (epoch !== getAuthEpoch()) return
    await get().loadWorkspaceBindings()
  },
  validateWorkspaceBinding: async (id) => {
    await api.validateWorkspace(id)
    await get().loadWorkspaceBindings()
  },
  deleteWorkspaceBinding: async (id) => {
    const epoch = getAuthEpoch()
    const preview = await api.cleanupPreview('workspace', id)
    if (epoch !== getAuthEpoch()) return
    if (preview.items.length && !confirm(`删除此工作区登记前将逐项回收所有关联会话进程：\n${preview.items.map((row) => `会话 ${row.conversation_id} · ${row.tool_name} · ${row.id.slice(0, 8)}`).join('\n')}\n物理目录不会删除，继续吗？`)) return
    if (epoch !== getAuthEpoch()) return
    await api.deleteWorkspace(id, true)
    if (epoch !== getAuthEpoch()) return
    await Promise.all([get().loadWorkspaceBindings(), get().loadWorkspace()])
  },
  
  /** 按当前认证与 World 读取工作台，丢弃迟到的旧身份配置。 */
  loadWorkspace: async () => {
    const epoch = getAuthEpoch()
    const world = get().worldName
    try {
      const user = get().user
      const [roles, conversations, modelConfigs, workspaceData] = await Promise.all([
        api.roles(),
        api.conversations(),
        user?.is_owner 
          ? api.modelConfigs().catch(() => []) 
          : Promise.resolve([]),
        user?.is_owner
          ? Promise.all([api.workspaceCapabilities(), api.workspaces()]).catch(() => null)
          : Promise.resolve(null),
      ])
      // 登录或 World 已更换时，旧配置请求也必须失效，不能把 Owner 配置写回 Guest 工作台。
      if (epoch !== getAuthEpoch() || world !== get().worldName || get().switchingWorld) return
      // 服务端会连墓碑一起返回：查找表保留全部角色用于历史消息展示，
      // 列表只保留存活角色，避免墓碑出现在侧边栏和成员选择器里。
      set({
        roles: roles.filter((role) => !role.deleted_at),
        roleDirectory: Object.fromEntries(roles.map((role) => [role.id, role])),
        conversations,
        modelConfigs,
        workspaceCapabilities: workspaceData?.[0] ?? null,
        workspaceBindings: workspaceData?.[1] ?? [],
      })
      
      // 如果 activeConversationId 不存在或在会话列表中找不到，则回到工作台空态
      const currentActiveId = get().activeConversationId
      if (currentActiveId !== null && !conversations.some(c => c.id === currentActiveId)) {
        set({ activeConversationId: null })
        navigateToConversation(null)
      }
    } catch (err) {
      if (epoch !== getAuthEpoch() || world !== get().worldName || get().switchingWorld) return
      // 待改密的 Token 会让业务接口返回 403：这是预期内的状态而不是故障，
      // 直接切到强制重置流程，避免本地标记丢失后停在空白工作台。
      if ((err as { code?: string }).code === 'PASSWORD_RESET_REQUIRED') {
        setPasswordResetRequired(true)
        set({ passwordResetRequired: true })
        return
      }
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
    // 201 已返回完整会话对象，直接写入本地状态，避免创建后立刻全量 GET。
    set({
      conversations: [conv, ...get().conversations.filter((item) => item.id !== conv.id)],
      activeConversationId: conv.id,
    })
    navigateToConversation(conv.id)
  },
  updateConversationMembers: async (id, roleIds, expectedRevision) => {
    const updated = await api.updateConversationMembers(id, {
      role_ids: roleIds,
      expected_revision: expectedRevision,
    })
    set({
      conversations: get().conversations.map((conversation) => (
        conversation.id === id ? updated : conversation
      )),
    })
  },
  deleteConversation: async (id) => {
    const epoch = getAuthEpoch()
    const preview = await api.cleanupPreview('conversation', id)
    if (epoch !== getAuthEpoch()) return
    if (preview.items.length && !confirm(`删除会话前将逐项回收以下进程：\n${preview.items.map((row) => `${row.tool_name} · ${row.id.slice(0, 8)}`).join('\n')}\n继续吗？`)) return
    if (epoch !== getAuthEpoch()) return
    await api.deleteConversation(id, true)
    if (epoch !== getAuthEpoch()) return
    if (get().activeConversationId === id) {
      set({ activeConversationId: null })
      navigateToConversation(null)
    }
    await get().loadWorkspace()
  },

  /** 加载回收站列表；打开回收站时调用，不随工作台常驻刷新。 */
  loadDeletedConversations: async () => {
    set({ deletedConversations: await api.deletedConversations() })
  },

  /** 从回收站恢复会话，并同步刷新工作台与回收站两份列表。 */
  restoreConversation: async (id) => {
    await api.restoreConversation(id)
    await Promise.all([get().loadWorkspace(), get().loadDeletedConversations()])
  },
  updateConversationPreferences: async (id, pinned, archived) => {
    await api.updateConversationPreferences(id, pinned, archived)
    await get().loadWorkspace()
  }
}))
