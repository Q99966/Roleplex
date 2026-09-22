import { request } from './client'

export type WorldPromptValues = { platform_override: string | null; world_prompt: string }
export type WorldPromptSettings = WorldPromptValues & { world_name: string; revision: number; platform_default: string; template_version: number; runtime_rules: string; updated_at: string | null }
export type ConversationPromptValues = { prompt: string }
export type ConversationPromptSettings = ConversationPromptValues & { conversation_id: number; revision: number; updated_at: string | null }
export type PromptRevisions = { template: number; world: number; role: number; conversation: number }
export type PromptLayer = { key: string; title: string; source: string; revision: number; text: string; characters: number; fingerprint: string }
export type PromptPreview = { world_name: string; conversation_id: number; role_id: number; role_name: string; configured_tools: string[]; revisions: PromptRevisions; layers: PromptLayer[];
  capabilities: { fingerprint: string; tools: { name: string; source: string; danger: string; description: string; parameters: Record<string, unknown> }[] };
  latest_execution: { execution_id: string; status: string; snapshot: { context_schema_version: number; revisions: PromptRevisions;
    layers: Omit<PromptLayer, 'text' | 'title'>[]; capabilities: { fingerprint: string; tools: { name: string; fingerprint: string }[] } } } | null }

export const promptSettings = {
  world: (signal?: AbortSignal) => request<WorldPromptSettings>('/api/prompt-settings', { signal }),
  saveWorld: (values: WorldPromptValues, revision: number) => request<WorldPromptSettings>('/api/prompt-settings', {
    method: 'PUT', body: JSON.stringify({ ...values, expected_revision: revision }),
  }),
  conversation: (id: number, signal?: AbortSignal) => request<ConversationPromptSettings>(`/api/conversations/${id}/prompt-settings`, { signal }),
  saveConversation: (id: number, values: ConversationPromptValues, revision: number) => request<ConversationPromptSettings>(`/api/conversations/${id}/prompt-settings`, {
    method: 'PUT', body: JSON.stringify({ ...values, expected_revision: revision }),
  }),
  preview: (id: number, roleId: number, signal?: AbortSignal) => request<PromptPreview>(`/api/conversations/${id}/prompt-preview?role_id=${roleId}`, { signal }),
}
