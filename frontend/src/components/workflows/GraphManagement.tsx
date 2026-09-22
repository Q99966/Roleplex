import { useEffect, useState } from 'react'
import { getAuthEpoch, type Message } from '../../api/client'
import { workflows, type Coordination, type GraphRead } from '../../api/workflows'
import { MessageParts } from '../MessageParts'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { WorkflowDialog } from './WorkflowDialog'

/** 记录真实提交与版本冲突；模型草稿和人工草稿保持可核对。 */
export function GraphManagement() {
  const w = useWorkflow()
  const [history, setHistory] = useState<GraphRead | null>(null)
  const [record, setRecord] = useState<Coordination | null>(null)
  const kind = w.mode === 'run' || w.draft.runTarget ? 'run' : 'definition'
  const id = kind === 'run' ? w.draft.runTarget && w.mode === 'edit' ? w.draft.runTarget : w.run?.id : w.draft.definition.id
  const revision = kind === 'run' ? w.run?.latest_graph_revision : w.data.definitions.find(d => d.id === id)?.revision
  const versionStates = w.run?.graph_versions?.map(v => `${v.graph_revision}:${v.status}`).join(',')
  useEffect(() => {
    let live = true
    const epoch = getAuthEpoch()
    setHistory(null)
    if (id && revision != null) void workflows.readGraph(w.conversation.id, kind, id).then(value => {
      if (live && epoch === getAuthEpoch()) setHistory(value)
    }).catch(() => { /* 主快照仍可操作；手动刷新可恢复历史。 */ })
    return () => { live = false }
  }, [w.conversation.id, kind, id, revision, versionStates])
  const coordinations = (w.data.coordinations ?? []).filter(c => kind === 'run'
    ? c.run_id === id || c.started_run_id === id || c.chain_id === w.run?.chain_id : c.definition_id === id)
  return <div className="space-y-3 text-xs">
    {history && history.compile_issues.length > 0 && <p role="status" className="rounded-lg border border-slate-700 p-2 text-slate-500">草稿已保存，可继续规划；启动前还需补齐配置。{history.compile_issues.map(issue => ({ WORKFLOW_GRAPH_NOT_EXECUTABLE: '任务或判断配置尚未完整。', WORKFLOW_LOOP_CONFIG_REQUIRED: '需要声明循环回边。', WORKFLOW_LOOP_CONFIG_INVALID: '请补齐循环入口、范围和出口。', WORKFLOW_ENTRY_REQUIRED: '请指定流程入口。', WORKFLOW_EDGE_CONFIG_INVALID: '请补齐条件出边。', WORKFLOW_INPUT_UNAVAILABLE: '请修正上游结果引用。' } as Record<string, string>)[issue.code] ?? issue.code).join(' ')}</p>}
    {history && history.versions.length > 0 && <details className="rounded-xl border border-slate-700 p-3">
      <summary className="cursor-pointer">图修改记录 · {kind === 'run' ? '运行图' : '流程定义'}</summary>
      <ul className="mt-2 space-y-2">{history.versions.map(v => <li key={v.graph_revision}>
        <span>图 v{v.graph_revision} · {v.legacy ? '旧记录基线' : v.source_execution_id ? '协调者提交' : 'Owner 提交'} · {({ applied: '已采用', pending: '等待循环边界', superseded: '已被后续修订替代', not_applied: '未采用' } as Record<string, string>)[v.status] ?? v.status}</span>
        <p className="text-slate-500">新增 {v.changes.added?.length ?? 0} · 修改 {v.changes.updated?.length ?? 0} · 移除 {v.changes.removed?.length ?? 0}</p>
        {v.changes.configuration_changed?.includes('presentation') && <p className="text-slate-500">展示阶段或分支名称已更新</p>}
        {v.changes.reason && <p className="text-amber-600">{v.changes.reason}</p>}
      </li>)}</ul>
    </details>}
    {!!coordinations.length && <section aria-label="协调请求记录" className="space-y-2 rounded-xl border border-slate-700 p-3">
      <h4 className="font-medium">{kind === 'run' ? '本次运行的协调请求' : '当前模板的协调请求'}</h4>
      {coordinations.map(c => <div key={c.id} className="border-t border-slate-700 pt-2 first:border-0">
        <p>{({ design: '规划草稿', execute: '协调执行', replan: '调整运行' })[c.mode]} · {statusLabel(c.status)}</p>
        <p className="line-clamp-2 text-slate-500">{c.goal}</p>
        <p className="text-slate-500">共享决策 {c.used_decisions ?? '未知'} / {c.decision_limit ?? '不限'}</p>
        {c.error_code && <p className="text-amber-600">{c.error_code}</p>}
        <div className="mt-1 flex flex-wrap gap-3">
          <button type="button" className="text-indigo-500" onClick={() => setRecord(c)}>查看协调工具记录</button>
          {['queued', 'running', 'stopping'].includes(c.status) && <button type="button" disabled={w.busy || Boolean(w.historyGraph) || c.status === 'stopping'} className="text-indigo-500 disabled:opacity-40" onClick={() => void w.cancelCoordination(c)}>停止协调</button>}
          <button type="button" disabled={!w.data.definitions.some(d => d.id === c.definition_id)} className="text-indigo-500 disabled:opacity-40" onClick={() => {
            const definition = w.data.definitions.find(d => d.id === c.definition_id)
            if (definition) w.choose(definition)
          }}>查看流程草稿</button>
        </div>
      </div>)}
    </section>}
    {record && <CoordinationRecord value={record} onClose={() => setRecord(null)} />}
  </div>
}

function CoordinationRecord({ value, onClose }: { value: Coordination; onClose: () => void }) {
  const w = useWorkflow()
  const [message, setMessage] = useState<Message | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    const epoch = getAuthEpoch()
    void workflows.coordinationMessage(w.conversation.id, value.id).then(m => { if (live && epoch === getAuthEpoch()) setMessage(m) })
      .catch(() => { if (live) setError('协调记录读取失败，请关闭后重新打开。') })
    return () => { live = false }
  }, [value.id, w.conversation.id])
  return <WorkflowDialog title="协调执行记录" onClose={onClose}>
      <div className="mb-3 flex items-center justify-between"><h4>协调执行记录</h4><button type="button" onClick={onClose}>关闭协调记录</button></div>
      {error ? <p role="alert">{error}</p> : message ? <MessageParts message={message} isOwner /> : <p className="text-slate-500">尚无可显示的模型消息。</p>}
  </WorkflowDialog>
}
