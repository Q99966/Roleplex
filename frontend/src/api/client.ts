export type AgentBudgetConfig = { decision_limit: number; effective_limit: number; ceiling: number; revision: number }
export type User = { id: number; username: string; nickname: string; avatar: string | null; is_owner: boolean }
export type RuntimeScope = 'world' | 'workspace' | 'conversation'
export type RuntimeConfig = { scope: RuntimeScope; scope_id: number; limit: number; revision: number; used: number; services_enabled?: boolean; services_supported?: boolean }
export type RuntimeProcess = { id: string; kind: string; state: string; tool_name: string; conversation_id: number; workspace_id: number;
  execution_id: string; role_id: number; pid: number | null; port: number | null; health_code: number | null; error_code: string | null;
  exit_code: number | null; created_at: string; started_at: string | null; expires_at: string | null; ended_at: string | null }
export type RuntimeLogPage = { availability: string; items: Array<{ seq: number; stream: string; text: string; bytes: number }>; next_seq: number; gap: boolean }

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
  context_window_tokens: number
  params: Record<string, unknown>
  skills: Record<string, unknown>[]
  builtin_tools: string[]
  mcp_servers: Record<string, unknown>[]
}
/** Agent 角色定义；`deleted_at` 非空表示墓碑，配置已清空，仅保留身份供历史消息展示。 */
export type Role = { id: number; name: string; avatar: string | null; description: string | null; tags: string[]; system_prompt: string; model_config_id: number | null; model_name: string; context_window_tokens: number; context_window_ceiling_tokens: number; effective_context_window_tokens: number; params: Record<string, unknown>; skills: Record<string, unknown>[]; builtin_tools: string[]; mcp_servers: Record<string, unknown>[]; active: boolean; deleted_at: string | null; created_at: string; updated_at: string }
/** 会话；`deleted_at` 非空表示在回收站中，保留期内可恢复。 */
export type Conversation = { id: number; type: 'single' | 'group'; title: string; orchestrator_enabled: boolean; orchestrator_role_id: number | null; workspace_binding_id: number | null; role_ids: number[]; revision: number; last_message_at: string | null; pinned: boolean; archived: boolean; deleted_at: string | null }
export type Part = { type: string; text?: string; language?: string; code?: string; title?: string; artifact_id?: number; version?: number; call_id?: string; tool_name?: string; status?: string; duration_ms?: number; command?: string; command_status?: 'exited' | 'timed_out' | 'cancelled'; exit_code?: number | null; truncated?: boolean; error_code?: string; [key: string]: unknown }
export type Message = { id: number; conversation_id: number; sender_type: string; sender_id: number | null; reply_to_id: number | null; mentions: Array<number | 'all'>; parts_json: Part[]; status: string; revision: number; chain_id: string | null; created_at: string; timeline_version?: number; stop_reason?: StopReason | null }
/** 服务器保存的停止原因，不包含回合统计或私有工具内容。 */
export type StopReason = 'user_cancelled' | 'graph_budget' | 'decision_budget' | 'provider_failed' | 'protocol_error' | 'interrupted' | 'context_rejected'
export type ToolCapture = { text: string; bytes: number; truncated: boolean }
export type FileDiffLine = {
  kind: 'context' | 'insert' | 'delete'; old_line: number | null; new_line: number | null;
  text: string; ending: 'lf' | 'crlf' | 'none'
}
export type FileDiffHunk = { old_start: number; old_lines: number; new_start: number; new_lines: number; lines: FileDiffLine[] }
export type FileChange = {
  id: string; path: string; operation: 'created' | 'modified' | 'unchanged'; applied: boolean | null;
  before_sha256: string | null; after_sha256: string; before_bytes: number; after_bytes: number;
  added: number | null; removed: number | null; hunks: FileDiffHunk[]
}
export type WriteDetails = {
  version: number; availability: 'recorded' | 'partial' | 'unavailable' | 'not_executed' | 'result_unconfirmed' | 'pending' | 'not_recorded';
  reason: string | null; files: FileChange[]; created_parent_count?: number | null
}
export type ShellApproval = {
  id: number; execution_id: string; tool_call_id: string; tool_name: string; workspace_binding_id: number;
  workspace_name: string; world_name: string; root_path: string; script: string; shell_kind: string;
  timeout_seconds: number; output_bytes: number; request_digest: string; status: string;
  requested_at: string; expires_at: string
  active_service_count?: number
  runtime_id?: string; port?: number; health_path?: string; lifetime_seconds?: number; ready_timeout_seconds?: number
}
export type LineReadResult = {
  mode: 'lines'; text: string; bytes: number; eof: boolean; start_line: number; start_offset?: number | null; end_line: number | null;
  next_line: number; sha256: string; scanned_bytes: number; limited_reason: 'line_too_long' | 'content_budget' | 'json_budget' | null
}
export type SearchSnippet = { line_number: number; text: string; truncated: boolean }
export type SearchResult = {
  version: number; status: 'complete' | 'partial'; truncated: boolean;
  matches: Array<{ path: string; line_number: number | null; text: string | null; truncated: boolean;
    context_before: SearchSnippet[]; context_after: SearchSnippet[]; sha256: string | null; version_confirmed: boolean; matched_queries?: number[] }>;
  issues: Array<{ reason: string; path: string | null }>; scanned_files: number; scanned_bytes: number; visited_entries: number
}
export type ReadBatchDetails = {
  version: number; status: 'running' | 'success' | 'partial' | 'failed' | 'cancelled' | 'rejected'; error_code: string | null
  items: Array<{ id: string; operation: 'read'; path: string;
    status: 'pending' | 'running' | 'success' | 'failed' | 'rejected' | 'cancelled' | 'not_executed' | 'budget_exhausted';
    error_code: string | null; output_limited: boolean;
    result: { text: string; bytes: number; eof: boolean; next_offset: number; sha256: string } | LineReadResult | null }>
}
export type WriteDiagnostic = {
  version: number; reason: string; scope: 'world' | 'workspace' | 'conversation' | 'tool'; executed: false;
  message: string; next_steps: string[]; recommended_tool: 'workspace_service_status' | null;
  services: Array<{ runtime_id: string; state: string }> | null; services_truncated: boolean;
  other_sessions_blocking: boolean | null
}
export type WriteWait = { phase: 'queue' | 'lock'; reason: 'queue_full' | 'queue_bytes' | 'queue_timeout' | 'lock_timeout' | 'closed' }
export type EditError = { replacement_index: number; conflicting_replacement_index?: number | null; recovery: 'reread_and_adjust' | 'split_non_overlapping' }
export type BatchMutationDetails = {
  wait_diagnostic?: WriteWait | null
  version: number; status: 'running' | 'success' | 'partial' | 'failed' | 'rejected' | 'cancelled' | 'result_unconfirmed'; error_code: string | null
  items: Array<{ id: string; path: string; operation: 'write' | 'edit';
    status: 'not_executed' | 'running' | 'success' | 'failed' | 'result_unconfirmed'; applied: boolean | null; created_parent_count?: number | null;
    error_code: string | null; result: { created: boolean; bytes: number; sha256: string } | null; write: WriteDetails | null;
    diagnostic?: WriteDiagnostic | null; edit_error?: EditError | null }>
}
export type ToolDetails = {
  edit_error?: EditError | null
  not_dispatched?: { reason: 'graph_budget' | 'arguments_invalid' | 'tool_unavailable' } | null
  argument_error?: { error_code: string; issues: Array<{ path: Array<string | number>; reason: string }> } | null
  wait_diagnostic?: WriteWait | null
  availability: 'available' | 'not_recorded' | 'expired' | 'unavailable'
  tool_name?: string; status?: string; started_at?: string; ended_at?: string | null; expires_at?: string
  input: ToolCapture | null; output: ToolCapture | null
  write?: WriteDetails | null
  read_batch?: ReadBatchDetails | null
  read_range?: LineReadResult | null
  search?: SearchResult | null
  budget_error?: { phase: string; actual: number; limit: number; unit: string }
  write_batch?: BatchMutationDetails | null
  diagnostic?: WriteDiagnostic | null
  shell?: {
    script: ToolCapture | null; approval_status: 'pending' | 'approved' | 'rejected' | 'expired' | null;
    approval_wait_ms: number | null; execution_duration_ms: number | null;
    stdout: ToolCapture | null; stderr: ToolCapture | null;
    output_availability: 'recorded' | 'pending' | 'not_executed' | 'not_recorded';
    execution_status: string | null; exit_code: number | null
  } | null
}
export type MessageCreate = { parts: Part[]; mentions?: Array<number | 'all'>; reply_to_id?: number | null; client_message_id?: string }
export type HistoryWindow = { has_more: boolean; next_cursor: string | null; oversized: boolean; page_bytes: number }
export type MessageHistory = HistoryWindow & { items: Message[]; event_seq: number; stream_epoch: string; active_generation_id: number | null; active_generation_ids: number[] }
export type SendMessageResult = { message: Message; generation_id: number | null; generation_ids: number[]; duplicate: boolean }
export type HealthStatus = { status: string; stream_epoch: string; world_name: string; world_managed: boolean }
export type WorldSummary = { name: string; current: boolean; created_at: string }
export type WorldList = { current: string; switching_supported: boolean; items: WorldSummary[] }
export type WorkspaceAvailability = 'available' | 'unavailable' | 'busy' | 'disabled'
export type WorkspaceBinding = {
  id: number
  display_name: string
  root_path: string
  workspace_kind: 'managed_directory'
  file_tools_enabled: boolean
  basic_commands_enabled: boolean
  shell_enabled: boolean
  active: boolean
  availability: WorkspaceAvailability
  last_validated_at: string | null
  bound_conversation_count: number
  created_at: string
  updated_at: string
}
export type WorkspaceCapabilities = {
  world_name: string
  workspace_kinds: Array<'managed_directory'>
  file_tools: Array<'workspace_list' | 'workspace_read' | 'workspace_search' | 'workspace_write' | 'workspace_edit'>
  basic_commands_available: boolean
  shell_available: boolean
  shell_kind: 'bash' | 'powershell' | null
  shell_approval_mode: 'per_call'
  shell_timeout_seconds: number
  shell_output_bytes: number
}

/** 服务端事件信封；未知事件类型必须被客户端安全忽略。 */
export type StreamEvent = {
  subscription_id?: string
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
 * 后端地址：
 * 1. 优先使用显式环境变量（如 E2E 测试指定不同端口）。
 * 2. 未指定时默认为空字符串，由当前 Web 宿主（如 Vite 开发代理或生产同源网关）统一反向代理 /api 与 WebSocket，
 *    彻底避免 Windows / WSL2 下浏览器直连 8000 端口引发的 ERR_CONNECTION_REFUSED。
 */
const API_URL = import.meta.env.VITE_API_URL ?? ''
let token = localStorage.getItem('roleplex_token')
let authEpoch = 0
const tokenListeners = new Set<() => void>()

/** 返回本地认证代次，不把 Token 原文作为状态键。 */
export function getAuthEpoch() { return authEpoch }

/** 监听认证上下文变化。
 * @param listener 登录、退出或替换 Token 后执行的回调。
 */
export function onTokenChange(listener: () => void) {
  tokenListeners.add(listener)
  return () => { tokenListeners.delete(listener) }
}

/** 保存或清除浏览器会话 Token，不将其暴露给业务状态。 */
export function setToken(next: string | null) {
  const changed = token !== next
  token = next
  if (next) localStorage.setItem('roleplex_token', next)
  else localStorage.removeItem('roleplex_token')
  if (changed) {
    authEpoch += 1
    for (const listener of tokenListeners) listener()
  }
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
 * @param format 成功响应格式；敏感备份只在调用方内存中短暂持有 Blob。
 */
export async function request<T>(path: string, init: RequestInit = {}, format: 'json' | 'blob' = 'json'): Promise<T> {
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
  if (format === 'blob') return response.blob() as Promise<T>
  return response.json() as Promise<T>
}

export const api = {
  agentBudget: (signal?: AbortSignal) => request<AgentBudgetConfig>('/api/agent-budget/config', { signal, cache: 'no-store' }),
  setAgentBudget: (decision_limit: number, expected_revision: number) => request<AgentBudgetConfig>('/api/agent-budget/config', { method: 'PUT', body: JSON.stringify({ decision_limit, expected_revision }) }),
  toolDetails: (conversationId: number, messageId: number, callId: string, signal?: AbortSignal) => request<ToolDetails>(
    `/api/conversations/${conversationId}/messages/${messageId}/tools/${encodeURIComponent(callId)}`, { signal, cache: 'no-store' },
  ),
  health: () => request<HealthStatus>('/api/health'),
  register: (body: { username: string; password: string; nickname: string }) => request<AuthResult>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (body: { username: string; password: string }) => request<AuthResult>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  me: () => request<User>('/api/auth/me'),
  changePassword: (body: { current_password: string; new_password: string }) => request<AuthResult>('/api/auth/password', { method: 'POST', body: JSON.stringify(body) }),

  // 世界存档：列表仅 Owner 可读，切换必须由包装器托管后端。
  worlds: () => request<WorldList>('/api/worlds'),
  backupWorld: (signal?: AbortSignal) => request<Blob>('/api/worlds/backup', {
    method: 'POST', body: JSON.stringify({ confirm_cleanup: true }), cache: 'no-store', signal,
  }, 'blob'),
  switchWorld: (name: string) => request<{ target: string; restarting: boolean }>('/api/worlds/switch', {
    method: 'POST', body: JSON.stringify({ name }),
  }),

  // 当前 World 工作区：绝对根只通过 Owner-only 接口读写，Guest 无法枚举。
  workspaceCapabilities: () => request<WorkspaceCapabilities>('/api/workspaces/capabilities'),
  workspaces: () => request<WorkspaceBinding[]>('/api/workspaces'),
  createWorkspace: (body: {
    display_name: string
    root_path: string
    create_directory: boolean
    acknowledge_existing_content: boolean
  }) => request<WorkspaceBinding>('/api/workspaces', { method: 'POST', body: JSON.stringify(body) }),
  updateWorkspace: (id: number, body: { active?: boolean; file_tools_enabled?: boolean; basic_commands_enabled?: boolean; shell_enabled?: boolean; confirm_cleanup?: boolean }) => (
    request<WorkspaceBinding>(`/api/workspaces/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
  ),
  validateWorkspace: (id: number) => request<WorkspaceBinding>(`/api/workspaces/${id}/validate`, { method: 'POST' }),
  deleteWorkspace: (id: number, confirmed = false) => request<void>(`/api/workspaces/${id}?confirm_cleanup=${confirmed}`, { method: 'DELETE' }),

  // 角色管理 API
  roles: () => request<Role[]>('/api/roles'),
  createRole: (body: RoleInput) => request<Role>('/api/roles', { method: 'POST', body: JSON.stringify(body) }),
  updateRole: (id: number, body: RoleInput) => request<Role>(`/api/roles/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRole: (id: number) => request<void>(`/api/roles/${id}`, { method: 'DELETE' }),

  // 会话管理 API
  conversations: () => request<Conversation[]>('/api/conversations'),
  deletedConversations: () => request<Conversation[]>('/api/conversations/deleted'),
  restoreConversation: (id: number) => request<Conversation>(`/api/conversations/${id}/restore`, { method: 'POST' }),
  createConversation: (body: { type: 'single' | 'group'; title: string; role_ids: number[]; orchestrator_enabled?: boolean; orchestrator_role_id?: number | null; workspace_binding_id?: number | null }) => request<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify(body) }),
  updateConversationMembers: (id: number, body: { role_ids: number[]; expected_revision: number }) => request<Conversation>(`/api/conversations/${id}/members`, { method: 'PUT', body: JSON.stringify(body) }),
  updateConversationWorkspace: (id: number, body: { workspace_binding_id: number | null; expected_revision: number; confirm_cleanup?: boolean }) => request<Conversation>(`/api/conversations/${id}/workspace`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteConversation: (id: number, confirmed = false) => request<void>(`/api/conversations/${id}?confirm_cleanup=${confirmed}`, { method: 'DELETE' }),
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
  messages: (conversationId: number, signal?: AbortSignal, before?: string) => request<MessageHistory>(`/api/conversations/${conversationId}/messages?window=recent${before ? `&before=${encodeURIComponent(before)}` : ''}`, { signal, cache: 'no-store' }),
  shellApprovals: (conversationId: number, signal?: AbortSignal) => request<ShellApproval[]>(`/api/conversations/${conversationId}/tool-approvals`, { signal, cache: 'no-store' }),
  runtimeConfig: (scope: RuntimeScope, id: number, signal?: AbortSignal) => request<RuntimeConfig>(`/api/runtime/config?scope=${scope}&scope_id=${id}`, { signal, cache: 'no-store' }),
  setRuntimeConfig: (body: { scope: RuntimeScope; scope_id: number; limit: number; expected_revision: number; services_enabled?: boolean; confirm_cleanup?: boolean }) => request<RuntimeConfig>('/api/runtime/config', { method: 'PUT', body: JSON.stringify(body) }),
  runtimeProcesses: (id: number, signal?: AbortSignal) => request<{ items: RuntimeProcess[]; services_supported: boolean }>(`/api/conversations/${id}/processes`, { signal, cache: 'no-store' }),
  runtimeDetail: (id: number, runtimeId: string, signal?: AbortSignal) => request<RuntimeProcess & { request: { script: string; root_path: string; shell_kind: string } | null; command_detail: { message_id: number; part: Part } | null }>(`/api/conversations/${id}/processes/${runtimeId}`, { signal, cache: 'no-store' }),
  runtimeLogs: (id: number, runtimeId: string, after: number, signal?: AbortSignal) => request<RuntimeLogPage>(`/api/conversations/${id}/processes/${runtimeId}/logs?after=${after}`, { signal, cache: 'no-store' }),
  stopRuntime: (id: number, runtimeId: string) => request<RuntimeProcess>(`/api/conversations/${id}/processes/${runtimeId}/stop`, { method: 'POST' }),
  cleanupPreview: (scope: RuntimeScope, id: number) => request<{ items: RuntimeProcess[] }>(`/api/runtime/cleanup-preview?scope=${scope}&scope_id=${id}`, { cache: 'no-store' }),
  decideShell: (conversationId: number, id: number, decision: 'approve' | 'reject', request_digest: string) => request<{ id: number; status: string }>(
    `/api/conversations/${conversationId}/tool-approvals/${id}/decision`, { method: 'POST', body: JSON.stringify({ decision, request_digest }), cache: 'no-store' }),
  sendMessage: (conversationId: number, body: MessageCreate) => request<SendMessageResult>(`/api/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  stopGeneration: (conversationId: number) => request<{ stopped: boolean; generation_id: number | null }>(`/api/conversations/${conversationId}/stop`, { method: 'POST' }),
}

/** 返回 REST 和后续 WebSocket 客户端使用的后端地址。 */
export function getApiUrl() { return API_URL }
