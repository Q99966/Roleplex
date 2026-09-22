import { request } from './client'

export type ContextSource = { message_id: number; revision: number; status: string }
export type MaterialReceipt = { scope: string; revision: number | null; visible_through_message_id: number; current_message_id: number | null; current_message_revision: number | null; sources: ContextSource[] }
export type InputEstimate = { estimated_tokens: number; safety_margin_tokens: number; estimator_kind: string; estimator_version: number; is_provider_exact: false }
export type RequestEstimate = InputEstimate & { effective_context_window: number; output_reserved_tokens: number; input_budget_tokens: number; before_truncation_tokens: number; before_truncation_safety_margin_tokens: number; included_message_count: number; truncated_message_count: number; blocked: boolean; recovery_omitted: boolean; breakdown: Record<string, number> }
export type ContextSummary = { conversation_id: number; revision: number; projection_version: number; updated_at: string; counts: { included: number; pending: number; excluded: number }; excluded_reasons: Record<string, number>; text_bytes: number; through_message_id: number }
export type ContextEntry = ContextSource & { sender_type: string; sender_id: number | null; state: string; reason: string | null; text: string; text_truncated: boolean; text_bytes: number; created_at: string }
export type ContextPage = ContextSummary & { entries: ContextEntry[]; next_before: number | null }
export type ContextPreview = { role_id: number; role_name: string; model_name: string; shared: ContextSummary; material: MaterialReceipt; request: RequestEstimate;
  messages: { type: string; content: string }[] | null;
  latest_call: { execution_id: string; execution_kind: string; execution_status: string; call_index: number; status: string; recorded_at: string; model_name: string; provider_mode: string;
    input_estimate: (InputEstimate & { message_tokens: number; tool_schema_tokens: number; message_count: number; tool_message_count: number }) | null;
    provider_usage: { input_tokens: number | null; output_tokens: number | null; cache_hit_tokens: number | null; cache_write_tokens: number | null };
    snapshot: { request?: RequestEstimate; material?: MaterialReceipt } | null } | null }

export const conversationContext = {
  read: (id: number, signal?: AbortSignal, before?: number, revision?: number) => request<ContextPage>(`/api/conversations/${id}/context?${new URLSearchParams({
    ...(before === undefined ? {} : { before: String(before) }), ...(revision === undefined ? {} : { expected_revision: String(revision) }),
  })}`, { signal }),
  preview: (id: number, roleId: number, draft: string, includeContent: boolean, signal?: AbortSignal) => request<ContextPreview>(`/api/conversations/${id}/context/preview`, {
    method: 'POST', signal, body: JSON.stringify({ role_id: roleId, draft, include_content: includeContent }),
  }),
}
