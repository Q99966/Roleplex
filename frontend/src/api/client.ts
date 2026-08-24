export type User = { id: number; username: string; nickname: string; avatar: string | null; is_owner: boolean }

/**
 * 认证响应。
 *
 * `password_reset_required` 由服务端显式回传，前端据此进入强制重置流程，
 * 不解析 JWT。为 true 时该 Token 只能调用改密和查看本人资料两个接口。
 */
export type AuthResult = { access_token: string; user: User; password_reset_required: boolean }
/**
 * 角色写入请求体。
 *
 * 不用 `Omit<Role, …>` 推导：`deleted_at`、`active` 等状态由服务端管理，
 * 客户端提交的字段集合与响应字段集合是两件事，混用会让新增只读字段意外
 * 变成必填的请求字段。
 */
export type RoleInput = {
  name: string
  avatar: string | null
  description: string | null
  tags: string[]
  system_prompt: string
  model_config_id: number
  model_name: string
  params: Record<string, unknown>
  skills: Record<string, unknown>[]
  builtin_tools: string[]
  mcp_servers: Record<string, unknown>[]
}
/** Agent 角色定义；`deleted_at` 非空表示墓碑，配置已清空，仅保留身份供历史消息展示。 */
export type Role = { id: number; name: string; avatar: string | null; description: string | null; tags: string[]; system_prompt: string; model_config_id: number | null; model_name: string; params: Record<string, unknown>; skills: Record<string, unknown>[]; builtin_tools: string[]; mcp_servers: Record<string, unknown>[]; active: boolean; deleted_at: string | null; created_at: string; updated_at: string }
/** 会话；`deleted_at` 非空表示在回收站中，保留期内可恢复。 */
export type Conversation = { id: number; type: 'single' | 'group'; title: string; orchestrator_enabled: boolean; orchestrator_role_id: number | null; role_ids: number[]; last_message_at: string | null; pinned: boolean; archived: boolean; deleted_at: string | null }
export type Part = { type: string; text?: string; language?: string; code?: string; title?: string; artifact_id?: number; version?: number; [key: string]: unknown }
export type Message = { id: number; conversation_id: number; sender_type: string; sender_id: number | null; parts_json: Part[]; status: string; revision: number; chain_id: string | null; created_at: string }
export type MessageCreate = { parts: Part[]; mentions?: Array<number | 'all'>; reply_to_id?: number | null; client_message_id?: string }
export type MessageHistory = { items: Message[]; event_seq: number; stream_epoch: string; active_generation_id: number | null }
export type SendMessageResult = { message: Message; generation_id: number | null; duplicate: boolean }

/** 服务端事件信封；未知事件类型必须被客户端安全忽略。 */
export type StreamEvent = {
  stream_epoch: string
  event_seq: number
  conversation_id: number
  type: string
  revision: number
  delta_seq: number | null
  generation_id: number | null
  payload: Record<string, any>
}

export type ModelConfig = {
  id: number
  name: string
  provider_type: 'anthropic' | 'openai_compatible'
  base_url: string | null
  api_key_hint: string
  capability_overrides: Record<string, any>
  created_at: string
}

type ApiError = Error & { status?: number; code?: string }

/**
 * 后端地址：优先使用构建时配置，否则回退到当前页面主机的 8000 端口。
 *
 * 使用页面主机而不是写死 localhost，可避免 Windows 上 localhost 解析到 IPv6
 * 而后端只监听 IPv4 的连接失败，同时让局域网访问自动指向同一台主机。
 */
const API_URL = import.meta.env.VITE_API_URL
  ?? (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8000` : 'http://127.0.0.1:8000')
let token = localStorage.getItem('roleplex_token')

/** 保存或清除浏览器会话 Token，不将其暴露给业务状态。 */
export function setToken(next: string | null) {
  token = next
  if (next) localStorage.setItem('roleplex_token', next)
  else localStorage.removeItem('roleplex_token')
}

/** 返回当前浏览器会话 Token 的内存副本。 */
export function getToken() { return token }

const PASSWORD_RESET_KEY = 'roleplex_password_reset'

/**
 * 记录或清除"当前 Token 必须先改密"的本地标记。
 *
 * 该标记只是刷新页面后免去一次失败请求的 UI 提示，不是安全边界：
 * 真正的拦截在服务端，标记丢失或被篡改时，业务接口仍会返回
 * `PASSWORD_RESET_REQUIRED`，前端据此回到重置流程。
 */
export function setPasswordResetRequired(next: boolean) {
  if (next) localStorage.setItem(PASSWORD_RESET_KEY, '1')
  else localStorage.removeItem(PASSWORD_RESET_KEY)
}

/** 读取本地的待改密标记。 */
export function getPasswordResetRequired() {
  return localStorage.getItem(PASSWORD_RESET_KEY) === '1'
}

/**
 * 执行 JSON API 请求，并将公开错误信封映射为 ApiError。
 * @param path 相对于后端地址的 API 路径。
 * @param init Fetch 配置，包括请求方法和可选请求体。
 */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_URL}${path}`, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string; error?: { code?: string } }
    const error = new Error(body.error?.code ?? body.detail ?? `HTTP_${response.status}`) as ApiError
    error.status = response.status
    error.code = body.error?.code ?? body.detail
    throw error
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  register: (body: { username: string; password: string; nickname: string }) => request<AuthResult>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (body: { username: string; password: string }) => request<AuthResult>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  me: () => request<User>('/api/auth/me'),
  changePassword: (body: { current_password: string; new_password: string }) => request<AuthResult>('/api/auth/password', { method: 'POST', body: JSON.stringify(body) }),

  // 角色管理 API
  roles: () => request<Role[]>('/api/roles'),
  createRole: (body: RoleInput) => request<Role>('/api/roles', { method: 'POST', body: JSON.stringify(body) }),
  updateRole: (id: number, body: RoleInput) => request<Role>(`/api/roles/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRole: (id: number) => request<void>(`/api/roles/${id}`, { method: 'DELETE' }),

  // 会话管理 API
  conversations: () => request<Conversation[]>('/api/conversations'),
  deletedConversations: () => request<Conversation[]>('/api/conversations/deleted'),
  restoreConversation: (id: number) => request<Conversation>(`/api/conversations/${id}/restore`, { method: 'POST' }),
  createConversation: (body: { type: 'single' | 'group'; title: string; role_ids: number[]; orchestrator_enabled?: boolean; orchestrator_role_id?: number | null }) => request<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify(body) }),
  deleteConversation: (id: number) => request<void>(`/api/conversations/${id}`, { method: 'DELETE' }),
  updateConversationPreferences: (id: number, pinned?: boolean, archived?: boolean) => {
    const params = new URLSearchParams()
    if (pinned !== undefined) params.append('pinned', String(pinned))
    if (archived !== undefined) params.append('archived', String(archived))
    return request<Conversation>(`/api/conversations/${id}/preferences?${params.toString()}`, { method: 'PATCH' })
  },

  // 模型厂商配置 API
  modelConfigs: () => request<ModelConfig[]>('/api/model-configs'),
  createModelConfig: (body: { name: string; provider_type: 'anthropic' | 'openai_compatible'; base_url?: string | null; api_key: string; capability_overrides?: Record<string, any> }) => request<ModelConfig>('/api/model-configs', { method: 'POST', body: JSON.stringify(body) }),
  deleteModelConfig: (id: number) => request<void>(`/api/model-configs/${id}`, { method: 'DELETE' }),

  // 消息与生成 API
  messages: (conversationId: number) => request<MessageHistory>(`/api/conversations/${conversationId}/messages`),
  sendMessage: (conversationId: number, body: MessageCreate) => request<SendMessageResult>(`/api/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  stopGeneration: (conversationId: number) => request<{ stopped: boolean; generation_id: number | null }>(`/api/conversations/${conversationId}/stop`, { method: 'POST' }),
}

/** 返回 REST 和后续 WebSocket 客户端使用的后端地址。 */
export function getApiUrl() { return API_URL }
