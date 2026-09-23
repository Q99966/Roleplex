import { request, type Conversation, type Role, type RoleInput, type UsageSummary } from './client'

export type WorldOrchestrator = { world_name: string; role_id: number | null; role_name: string | null; model_name: string | null;
  revision: number; available: boolean; active_task_count: number; conversation: Conversation | null;
  fixed_identity: boolean; enabled: boolean; status: 'needs_owner' | 'needs_configuration' | 'disabled' | 'ready'; profile: Role | null }
export type WorldChild = { id: string; kind: string; conversation_id: number | null; coordination_id: string | null; run_id: string | null;
  chain_id: string | null; status: string; revision: number; graph_revision: number | null; available: boolean; error_code: string | null; reference: Record<string, unknown>;
  results: { attempt_id: string; node_id: string; status: string; graph_revision: number; result: unknown }[];
  feedback: { id: string; category: string; summary: string; status: string; revision: number; blocking: boolean; attempt_id: string }[] }
export type WorldTask = { id: string; title: string; status: string; revision: number; role_id: number; conversation_id: number;
  chain_id: string; root_execution_id: string; summary: string | null; error_code: string | null; decision_limit: number | null;
  used_decisions: number; children: WorldChild[]; usage: UsageSummary; created_at: string; updated_at: string }
export type WorldMemory = { id: string; category: string; revision: number; status: string; origin: string; source_message_id: number | null;
  source_revision: number | null; source_role_id: number | null; available: boolean; text: string | null; updated_at: string; notice: string }
export const worldOrchestrator = {
  read: (signal?: AbortSignal) => request<WorldOrchestrator>('/api/world-orchestrator', { signal, cache: 'no-store' }),
  appoint: (role_id: number | null, expected_revision: number) => request<WorldOrchestrator>('/api/world-orchestrator', {
    method: 'PUT', body: JSON.stringify({ role_id, expected_revision }),
  }),
  importRole: (role_id: number, expected_revision: number) => request<WorldOrchestrator>('/api/world-orchestrator/import-role', { method: 'POST', body: JSON.stringify({ role_id, expected_revision }) }),
  configure: (body: RoleInput) => request<WorldOrchestrator>('/api/world-orchestrator/config', { method: 'PUT', body: JSON.stringify(body) }),
  enable: (enabled: boolean, expected_revision: number) => request<WorldOrchestrator>('/api/world-orchestrator/enabled', { method: 'POST', body: JSON.stringify({ enabled, expected_revision }) }),
  tasks: (signal?: AbortSignal) => request<{ items: WorldTask[] }>('/api/world-orchestrator/tasks', { signal, cache: 'no-store' }),
  stopTask: (id: string, revision: number) => request<WorldTask>(`/api/world-orchestrator/tasks/${id}/stop`, { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }),
  stopChild: (task: string, child: string, revision: number) => request<WorldChild>(`/api/world-orchestrator/tasks/${task}/children/${child}/stop`, { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }),
  memories: (query = '', signal?: AbortSignal) => request<{ items: WorldMemory[] }>(`/api/world-orchestrator/memories?query=${encodeURIComponent(query)}`, { signal, cache: 'no-store' }),
  readMemory: (id: string, revision: number) => request<WorldMemory>(`/api/world-orchestrator/memories/${id}?revision=${revision}`),
  memoryVersions: (id: string) => request<{ items: WorldMemory[] }>(`/api/world-orchestrator/memories/${id}/versions`),
  saveMemory: (text: string, category: string, request_key: string) => request<WorldMemory>('/api/world-orchestrator/memories', { method: 'POST', body: JSON.stringify({ text, category, request_key }) }),
  editMemory: (id: string, expected_revision: number, text: string, status: string) => request<WorldMemory>(`/api/world-orchestrator/memories/${id}`, { method: 'PUT', body: JSON.stringify({ expected_revision, text, status }) }),
}
