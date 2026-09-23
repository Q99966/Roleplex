import { request, type Message } from './client'
export type WorkflowScalar = boolean | number | string | null
export type WorkflowCondition = { sources: string[]; key: string; operator: 'eq' | 'ne'; value: WorkflowScalar; aggregate: 'all' | 'any' }
export type WorkflowLoop = { id: string; entry: string; decision: string; exit: string; body: string[]; repeat_when: boolean; max_iterations: number | null; carry_inputs: string[] }
export type WorkflowNode = { id: string; kind: 'role' | 'approval' | 'join' | 'condition' | 'judge'; title: string; role_id: number | null; task: string; expected_output: string; inputs: string[];
  tools?: string[] | null; result_keys?: string[]; result_schema?: Record<string, 'boolean' | 'integer' | 'number' | 'string' | 'null'>; condition?: WorkflowCondition | null; position?: { x: number; y: number } | null; color?: string | null }
export type WorkflowPresentation = { groups: { id: string; title: string; node_ids: string[] }[]; edge_labels: { source: string; target: string; label: string }[] }
export type WorkflowGraph = { nodes: WorkflowNode[]; edges: [string, string][]; runtime_version?: 1 | 2; concurrency?: number | null;
  entries?: string[]; edge_rules?: { source: string; target: string; when: 'always' | 'true' | 'false' }[]; loops?: WorkflowLoop[]; presentation?: WorkflowPresentation | null }
export type WorkflowActivation = { current?: boolean; graph_revision?: number | null; id: string; node_id: string; iteration: number; loop_id: string | null; status: string; attempt_id: string | null; error_code: string | null }
export type WorkflowDefinition = { id: string; name: string; revision: number; graph: WorkflowGraph }
export type WorkflowAttempt = { graph_revision?: number | null; node_snapshot?: WorkflowNode | null; id: string; node_id: string; number: number; status: string; current: boolean; upstream_ids: string[]; retry_source_id: string | null; instruction: string; message_id: number | null; generation_id: number | null; execution_id: string | null; error_code: string | null; activation_id?: string | null; iteration?: number; loop_id?: string | null; phase?: string; selected_in_activation?: boolean; assigned_role_id?: number | null; assigned_tools?: string[]; waiting_resource?: string | null; result?: Record<string, unknown> | null; decision_count?: number; usage?: { input_tokens: number | null; output_tokens: number | null } }
export type FeedbackCategory = 'implementation' | 'contract' | 'capability' | 'unverified' | 'suggestion'
export type FeedbackAction = 'assign' | 'wait' | 'review' | 'resolve' | 'dismiss' | 'accept' | 'obsolete' | 'reopen' | 'coordinate'
export type FeedbackReport = { request_key: string; attempt_id: string; category: FeedbackCategory; summary: string; details: string; blocking: boolean; requested_tools?: string[]; suggested_role_id?: number }
export type FeedbackUpdate = { expected_revision: number; request_key: string; action: FeedbackAction; reason: string; handler_role_id?: number; handler_node_ids?: string[]; verification_attempt_id?: string; manual_verification?: boolean }
export type WorkflowFeedback = {
  id: string; run_id: string; attempt_id: string; node_id: string; graph_revision: number | null; result_revision: number | null; iteration: number;
  source_role_id: number | null; source_current: boolean; actor_execution_id: string | null; category: FeedbackCategory;
  summary: string; details: string; blocking: boolean; status: string; revision: number; handler_role_id: number | null;
  handler_node_ids: string[]; verification_attempt_id: string | null; coordination_session_id: string | null; coordination_requested: boolean;
  capability_check: { assigned_tools?: string[]; available_tools?: string[]; unavailable_tools?: string[]; unassigned_tools?: string[] };
  history: { id: string; revision: number; action: string; status: string; reason: string; actor_kind: 'owner' | 'role' | 'system'; actor_execution_id: string | null; data: Record<string, unknown>; created_at: string }[];
  graph_changes: Pick<GraphVersion, 'graph_revision' | 'status' | 'changes'>[];
}
export type WorkflowRun = { feedback?: WorkflowFeedback[]; feedback_mode?: 'manual' | 'automatic'; constraints?: { nodes?: Record<string, unknown> }; chain_id?: string; graph_revision?: number | null; latest_graph_revision?: number | null; pending_graph_revision?: number | null; graph_versions?: GraphVersion[]; id: string; definition_id: string; definition_revision: number; name: string; graph: WorkflowGraph; status: string; revision: number; cursor: number | null; runtime_version?: 1 | 2; mode?: 'manual' | 'coordinated'; coordinator_role_id?: number | null; phase?: string; loop_states?: Record<string, { iteration: number; exited: boolean; limited?: boolean }>; activations?: WorkflowActivation[]; error_code: string | null; workspace_binding_id: number | null; input_text: string; attempts: WorkflowAttempt[]; decision_limit: number | null; used_decisions: number | null }
export type WorkflowList = { coordinations?: Coordination[]; parallel_capacity?: number; member_capabilities?: { role_id: number; name: string; tools: string[] }[]; definitions: WorkflowDefinition[]; runs: WorkflowRun[] }
export type GraphVersion = { source_execution_id?: string | null; graph_revision: number; status: string; legacy: boolean; changes: { added?: string[]; removed?: string[]; updated?: string[]; configuration_changed?: string[]; reason?: string } }
export type GraphRead = { target: { kind: 'definition' | 'run'; id: string }; name: string; graph_revision: number; graph: WorkflowGraph; versions: GraphVersion[]; compile_issues: { code: string }[]; edit_scope?: { effective_graph_revision: number | null; frozen_nodes: string[]; future_loop_ids: string[] } }
export type GraphWrite = GraphVersion & { id: string; graph: WorkflowGraph; name: string; committed?: boolean; executable: boolean; compile_issues: { code: string }[] }
export type Coordination = { feedback_ids?: string[]; feedback_mode?: 'manual' | 'automatic'; constraints?: { nodes?: Record<string, unknown> }; chain_id: string; id: string; definition_id: string; run_id: string | null; started_run_id: string | null; mode: 'design' | 'execute' | 'replan'; goal: string; status: string; revision: number; role_id: number; execution_id: string | null; message_id: number | null; error_code: string | null; decision_limit: number | null; used_decisions: number | null }
export type CoordinateRequest = { feedback_ids?: string[]; feedback_mode?: 'manual' | 'automatic'; role_id: number; mode: Coordination['mode']; goal: string; request_key: string; definition_id?: string; run_id?: string; expected_graph_revision?: number; continue_session_id?: string; protected_nodes?: string[] }
const base = (cid: number) => `/api/conversations/${cid}/workflows`
export const workflows = {
  reportFeedback: (cid: number, rid: string, body: FeedbackReport) => request<WorkflowFeedback>(`${base(cid)}/runs/${rid}/feedback`, { method: 'POST', body: JSON.stringify(body) }),
  updateFeedback: (cid: number, rid: string, fid: string, body: FeedbackUpdate) => request<WorkflowFeedback>(`${base(cid)}/runs/${rid}/feedback/${fid}/actions`, { method: 'POST', body: JSON.stringify(body) }),
  draftScope: (cid: number) => request<{ scope: string }>(`${base(cid)}/draft-scope`),
  coordinationMessage: (cid: number, id: string) => request<Message | null>(`${base(cid)}/coordination/${id}/message`),
  coordinate: (cid: number, body: CoordinateRequest) => request<Coordination>(`${base(cid)}/coordination`, { method: 'POST', body: JSON.stringify(body) }),
  cancelCoordination: (cid: number, value: Coordination) => request<Coordination>(`${base(cid)}/coordination/${value.id}/cancel`, { method: 'POST', body: JSON.stringify({ expected_revision: value.revision }) }),
  readGraph: (cid: number, kind: 'definition' | 'run', id: string, revision?: number) => request<GraphRead>(`${base(cid)}/graphs/${kind}/${id}${revision == null ? '' : `?graph_revision=${revision}`}`),
  writeGraph: (cid: number, kind: 'definition' | 'run', id: string, graph: WorkflowGraph, revision: number, mutation_key: string, name?: string) => request<GraphWrite>(`${base(cid)}/graphs/${kind}/${id}/write`, { method: 'POST', body: JSON.stringify({ graph, expected_graph_revision: revision, mutation_key, name }) }),
  list: (cid: number) => request<WorkflowList>(base(cid)),
  save: (cid: number, definition: WorkflowDefinition) => request<WorkflowDefinition>(`${base(cid)}/definitions/${definition.id}`, { method: 'PUT', body: JSON.stringify({ name: definition.name, graph: definition.graph, expected_revision: definition.revision }) }),
  start: (cid: number, definition: WorkflowDefinition, request_key: string, input_text: string, mode: 'manual' | 'coordinated' = 'manual') => request<WorkflowRun>(`${base(cid)}/runs`, { method: 'POST', body: JSON.stringify({ definition_id: definition.id, expected_revision: definition.revision, request_key, input_text, mode }) }),
  control: (cid: number, run: WorkflowRun, body: { action: 'confirm' | 'stop' | 'retry' | 'resume'; attempt_id?: string; decision?: boolean; instruction?: string; acknowledge_facts?: boolean; rerun_downstream?: boolean }) => request<WorkflowRun>(`${base(cid)}/runs/${run.id}/control`, { method: 'POST', body: JSON.stringify({ ...body, expected_revision: run.revision }) }),
  facts: (cid: number, rid: string, aid: string) => request<{ attempt_id: string; text: string }>(`${base(cid)}/runs/${rid}/attempts/${aid}/facts`),
  input: (cid: number, rid: string, aid: string) => request<{ text: string | null; legacy: boolean }>(`${base(cid)}/runs/${rid}/attempts/${aid}/input`),
  message: (cid: number, rid: string, aid: string) => request<Message | null>(`${base(cid)}/runs/${rid}/attempts/${aid}/message`),
}
