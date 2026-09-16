import { request, type Message } from './client'
export type WorkflowNode = { id: string; kind: 'role' | 'approval'; title: string; role_id: number | null; task: string; expected_output: string; inputs: string[]; position?: { x: number; y: number } | null; color?: string | null }
export type WorkflowGraph = { nodes: WorkflowNode[]; edges: [string, string][] }
export type WorkflowDefinition = { id: string; name: string; revision: number; graph: WorkflowGraph }
export type WorkflowAttempt = { id: string; node_id: string; number: number; status: string; current: boolean; upstream_ids: string[]; retry_source_id: string | null; instruction: string; message_id: number | null; generation_id: number | null; execution_id: string | null; error_code: string | null; decision_count?: number; usage?: { input_tokens: number | null; output_tokens: number | null } }
export type WorkflowRun = { id: string; definition_id: string; definition_revision: number; name: string; graph: WorkflowGraph; status: string; revision: number; cursor: number; error_code: string | null; workspace_binding_id: number | null; input_text: string; attempts: WorkflowAttempt[]; decision_limit: number | null; used_decisions: number | null }
export type WorkflowList = { definitions: WorkflowDefinition[]; runs: WorkflowRun[] }
const base = (cid: number) => `/api/conversations/${cid}/workflows`
export const workflows = {
  list: (cid: number) => request<WorkflowList>(base(cid)),
  save: (cid: number, definition: WorkflowDefinition) => request<WorkflowDefinition>(`${base(cid)}/definitions/${definition.id}`, { method: 'PUT', body: JSON.stringify({ name: definition.name, graph: definition.graph, expected_revision: definition.revision }) }),
  start: (cid: number, definition: WorkflowDefinition, request_key: string, input_text: string) => request<WorkflowRun>(`${base(cid)}/runs`, { method: 'POST', body: JSON.stringify({ definition_id: definition.id, expected_revision: definition.revision, request_key, input_text }) }),
  control: (cid: number, run: WorkflowRun, body: { action: 'confirm' | 'stop' | 'retry' | 'resume'; attempt_id?: string; instruction?: string; acknowledge_facts?: boolean; rerun_downstream?: boolean }) => request<WorkflowRun>(`${base(cid)}/runs/${run.id}/control`, { method: 'POST', body: JSON.stringify({ ...body, expected_revision: run.revision }) }),
  facts: (cid: number, rid: string, aid: string) => request<{ attempt_id: string; text: string }>(`${base(cid)}/runs/${rid}/attempts/${aid}/facts`),
  message: (cid: number, rid: string, aid: string) => request<Message | null>(`${base(cid)}/runs/${rid}/attempts/${aid}/message`),
}
