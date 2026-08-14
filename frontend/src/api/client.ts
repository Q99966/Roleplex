export type User = { id: number; username: string; nickname: string; avatar: string | null; is_owner: boolean }
export type Role = { id: number; name: string; avatar: string | null; description: string | null; tags: string[]; system_prompt: string; model_config_id: number; model_name: string; params: Record<string, unknown>; skills: Record<string, unknown>[]; builtin_tools: string[]; mcp_servers: Record<string, unknown>[]; active: boolean; created_at: string; updated_at: string }
export type Conversation = { id: number; type: 'single' | 'group'; title: string; orchestrator_enabled: boolean; orchestrator_role_id: number | null; last_message_at: string | null; pinned: boolean; archived: boolean }
export type Part = { type: string; text?: string; language?: string; code?: string; title?: string; artifact_id?: number; version?: number; [key: string]: unknown }
export type Message = { id: number; conversation_id: number; sender_type: string; sender_id: number | null; parts_json: Part[]; status: string; revision: number; chain_id: string | null; created_at: string }

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

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
let token = localStorage.getItem('roleplex_token')

/** 保存或清除浏览器会话 Token，不将其暴露给业务状态。 */
export function setToken(next: string | null) {
  token = next
  if (next) localStorage.setItem('roleplex_token', next)
  else localStorage.removeItem('roleplex_token')
}

/** 返回当前浏览器会话 Token 的内存副本。 */
export function getToken() { return token }

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
  register: (body: { username: string; password: string; nickname: string }) => request<{ access_token: string; user: User }>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (body: { username: string; password: string }) => request<{ access_token: string; user: User }>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  me: () => request<User>('/api/auth/me'),
  
  // 角色管理 API
  roles: () => request<Role[]>('/api/roles'),
  createRole: (body: Omit<Role, 'id' | 'created_at' | 'updated_at' | 'active'>) => request<Role>('/api/roles', { method: 'POST', body: JSON.stringify(body) }),
  updateRole: (id: number, body: Omit<Role, 'id' | 'created_at' | 'updated_at' | 'active'>) => request<Role>(`/api/roles/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRole: (id: number) => request<void>(`/api/roles/${id}`, { method: 'DELETE' }),

  // 会话管理 API
  conversations: () => request<Conversation[]>('/api/conversations'),
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
}

/** 返回 REST 和后续 WebSocket 客户端使用的后端地址。 */
export function getApiUrl() { return API_URL }
