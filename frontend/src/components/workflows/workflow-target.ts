import type { WorkflowRun } from '../../api/workflows'
import type { Draft } from './local-drafts'

export type InspectorPage = 'context' | 'feedback' | 'coordination' | 'records' | 'advanced' | 'drafts'
export type WorkflowTarget = { kind: 'template' | 'run-edit' | 'run' | 'history'; id: string; templateId: string; name: string; version: number | null; label: string; editable: boolean }

/** 当前操作对象由视图与草稿目标共同确定，不能把同一个 definition_id 当成模板/运行可互换。 */
export function workflowTarget(mode: 'edit' | 'run', draft: Draft, run: WorkflowRun | null, runId: string | null,
  history: { runId: string; revision: number } | null): WorkflowTarget {
  if (mode === 'edit') return {
    kind: draft.runTarget ? 'run-edit' : 'template', id: draft.runTarget ?? draft.definition.id,
    templateId: draft.definition.id, name: draft.definition.name, version: draft.definition.revision,
    label: draft.runTarget ? '本次运行调整' : '流程模板', editable: true,
  }
  return { kind: history ? 'history' : 'run', id: history?.runId ?? run?.id ?? runId ?? '',
    templateId: run?.definition_id ?? '', name: run?.name ?? '读取运行…', version: history?.revision ?? run?.graph_revision ?? null,
    label: history ? '历史只读' : '本次运行', editable: false }
}

/** 人工确认与问题等待是不同原因，旧串行运行没有 activations 也不能误标为反馈等待。 */
export function workflowRunStatus(run: WorkflowRun | null, fallback: (status: string) => string) {
  if (!run) return '正在读取'
  const feedback = run.runtime_version === 2 && !run.activations?.some(a => a.status === 'waiting')
    && (run.activations?.some(a => a.status === 'waiting_feedback') || run.feedback?.some(item => item.source_current && item.blocking && ['open', 'in_progress', 'waiting', 'review'].includes(item.status)))
  return run.status === 'waiting' && feedback ? '等待反馈处置' : fallback(run.status)
}

export const unfinished = (run: WorkflowRun) => run.runtime_version === 2
  ? Boolean(run.pending_graph_revision || run.activations?.some(a => a.current && ['pending', 'waiting', 'waiting_feedback', 'active'].includes(a.status)))
  : (run.cursor ?? 0) < run.graph.nodes.length
