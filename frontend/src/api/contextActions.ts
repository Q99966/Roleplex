import { request, type UsageSummary } from './client'
import type { ContextSummary } from './context'

export type CompressionInput = { request_key: string; expected_revision: number; role_id: number; keep_recent: number; target_tokens: number; instructions: string }
export type CompressionJob = { outcome?: { target_tokens: number; material_tokens: number; target_reached: boolean } | null; trigger?: 'manual' | 'automatic'; scope?: 'conversation' | 'execution'; id: string; request_key: string; role_id: number | null; execution_id: string; source_revision: number; base_summary_id: string | null; through_message_id: number; keep_recent: number; target_tokens: number; instructions: string; source_count: number; input_tokens_estimate: number; output_tokens_estimate: number | null; completed_calls: number; phase: string; status: string; error_code: string | null; cancel_requested: boolean; model_name: string; created_at: string; updated_at: string; usage?: UsageSummary }
export type SummaryVersion = { id: string; active: boolean; valid: boolean; text: string | null; source_count: number; through_message_id: number; input_tokens_estimate: number; output_tokens_estimate: number }
export type Compressions = { jobs: CompressionJob[]; versions: SummaryVersion[] }
export type MemorySource = { kind: 'message' | 'summary'; source_id: string; source_revision: number; conversation_id: number; conversation_title: string; created_at: string; message_id: number | null; summary_id?: string; sender_type: string; sender_id: number | null; status: string; reference: string; execution_facts?: unknown; workflow?: Record<string, string | number> }
export type MemoryHit = MemorySource & { snippet: string; snippet_offset: number; matched_terms: string[]; score: number; match_kind: string }
export type SearchResult = { results: MemoryHit[]; next_cursor: string | null; notice: string; scope: string }
export type MemoryRead = MemorySource & { text: string; offset: number; total_characters: number; truncated: boolean; next_offset: number | null; neighbors: (MemorySource & { text: string; truncated: boolean })[]; notice: string }
export type MemoryAccess = { id: string; execution_id: string; tool_call_id: string; action: string; created_at: string; available: boolean; source?: MemorySource;
  world_source?: { kind: 'world_note' | 'world_child'; source_id: string; source_revision: number; title: string } }

export const contextActions = {
  list: (cid: number, signal?: AbortSignal) => request<Compressions>(`/api/conversations/${cid}/context/compressions`, { signal }),
  find: (cid: number, key: string, signal?: AbortSignal) => request<Compressions>(`/api/conversations/${cid}/context/compressions?request_key=${encodeURIComponent(key)}`, { signal }),
  start: (cid: number, input: CompressionInput) => request<CompressionJob>(`/api/conversations/${cid}/context/compressions`, { method: 'POST', body: JSON.stringify(input) }),
  cancel: (cid: number, id: string) => request<CompressionJob>(`/api/conversations/${cid}/context/compressions/${id}/cancel`, { method: 'POST' }),
  restore: (cid: number, revision: number, id: string | null) => request<ContextSummary>(`/api/conversations/${cid}/context/restore`, { method: 'POST', body: JSON.stringify({ expected_revision: revision, summary_id: id }) }),
  search: (cid: number, roleId: number, query: string, scope: string, kinds: string[], cursor?: string, signal?: AbortSignal) => request<SearchResult>(`/api/conversations/${cid}/memory/search`, { method: 'POST', signal, body: JSON.stringify({ role_id: roleId, query, scope, kinds, ...(cursor ? { cursor } : {}) }) }),
  read: (cid: number, roleId: number, reference: string, offset = 0, signal?: AbortSignal) => request<MemoryRead>(`/api/conversations/${cid}/memory/read`, { method: 'POST', signal, body: JSON.stringify({ role_id: roleId, reference, offset, max_characters: 3000, context_messages: offset ? 0 : 1 }) }),
  references: (cid: number, roleId: number, signal?: AbortSignal) => request<{ items: MemoryAccess[] }>(`/api/conversations/${cid}/memory/references?role_id=${roleId}`, { signal }),
}

const errors: Record<string, string> = {
  CONTEXT_POLICY_CHANGED: '自动压缩策略已变化，本次维护未采用，原任务继续检查容量。',
  CONTEXT_SOURCE_CHANGED: '会话材料版本已变化，请刷新后重新操作。',
  CONTEXT_COMPRESSION_SOURCE_CHANGED: '来源、授权或摘要版本已变化，结果没有采用。',
  CONTEXT_COMPRESSION_BUSY: '本会话已有压缩任务，先查看其进度。',
  CONTEXT_COMPRESSION_REQUEST_CONFLICT: '这个请求已经提交过不同内容，请先核对原任务。',
  CONTEXT_NOTHING_TO_COMPRESS: '没有可压缩的历史，可以减少最近保留条数。',
  CONTEXT_COMPRESSION_MODEL_UNAVAILABLE: '所选角色模型不可用，请检查角色、成员及模型配置。',
  CONTEXT_COMPRESSION_MODEL_CHANGED: '模型配置已变化，结果没有采用。',
  CONTEXT_COMPRESSION_BUDGET_EXCEEDED: '压缩所需窗口或决策额度不足，原材料保留。',
  CONTEXT_COMPRESSION_SOURCE_TOO_LARGE: '一条完整来源无法装入所选模型窗口，请选择更大窗口的模型。',
  CONTEXT_COMPRESSION_MERGE_TOO_LARGE: '压缩段无法完整合并，请选择更大的模型窗口或缩短本次要求。',
  CONTEXT_COMPRESSION_OUTPUT_TOO_LARGE: '模型返回的摘要超过目标长度，原材料保留。',
  CONTEXT_COMPRESSION_NO_GAIN: '摘要没有缩短材料，保留当前版本。',
  CONTEXT_COMPRESSION_REQUIRED_FACTS_TOO_LARGE: '必须保留的事实超过目标长度，请提高目标或缩小范围。',
  CONTEXT_COMPRESSION_RESULT_INVALID: '模型返回格式或引用不正确，结果没有采用。',
  CONTEXT_COMPRESSION_RANGE_PENDING: '所选范围还有生成中的消息，请收口后重试。',
  CONTEXT_COMPRESSION_RANGE_INVALID: '压缩范围不适用，请刷新材料后重选。',
  EXECUTION_INTERRUPTED: '执行中断，保留原材料；不会自动重新调用模型。',
  MEMORY_SOURCE_CHANGED: '原文版本已变化，请重新搜索。', MEMORY_SOURCE_NOT_FOUND: '这个来源目前不可读取，请重新搜索或检查共享范围。',
  MEMORY_NOT_AVAILABLE: '角色或会话权限已变化，请重新选择。', MEMORY_CURSOR_CHANGED: '搜索范围或内容已变化，请重新搜索。',
  MEMORY_QUERY_INVALID: '请输入中文、英文或代码关键词。', OWNER_REQUIRED: '此操作由 Owner 管理。',
  MEMORY_READ_RANGE_INVALID: '读取位置超出原文范围，请从搜索结果重新读取。',
}
export function contextError(error: unknown) {
  const code = typeof error === 'string' ? error : (error as { code?: string })?.code
  return code && errors[code] ? errors[code] : '操作暂时未完成，请刷新状态后重试。'
}
