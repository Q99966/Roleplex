import { useEffect, useRef, useState } from 'react'
import { getAuthEpoch, type Message } from '../../api/client'
import { workflows, type Coordination, type GraphRead } from '../../api/workflows'
import { MessageParts } from '../MessageParts'
import { useWorkflow, statusLabel } from './WorkflowContext'

const button = 'rounded-lg border border-slate-700 px-3 py-2 disabled:opacity-40'

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
  const local = new Map(w.draft.definition.graph.nodes.map(n => [n.id, n]))
  const remote = w.remoteDefinition?.graph ?? history?.graph
  return <div className="space-y-3 text-xs">
    {w.conflict && <section aria-label="流程编辑冲突" className="space-y-2 rounded-xl border border-amber-500 p-3">
      <p role="alert">服务端图已更新，本地未保存草稿仍保留。请核对后选择处理方式。</p>
      {remote && <ul className="list-inside list-disc text-slate-500">
        {remote.nodes.filter(n => !local.has(n.id) || JSON.stringify(local.get(n.id)) !== JSON.stringify(n)).map(n => <li key={n.id}>{local.has(n.id) ? '内容有差异' : '服务端新增'}：{n.title}</li>)}
        {w.draft.definition.graph.nodes.filter(n => !remote.nodes.some(r => r.id === n.id)).map(n => <li key={n.id}>仅本地保留：{n.title}</li>)}
        {JSON.stringify(remote.edges) !== JSON.stringify(w.draft.definition.graph.edges) && <li>连线有差异</li>}
        {JSON.stringify(remote.presentation ?? null) !== JSON.stringify(w.draft.definition.graph.presentation ?? null) && <li>展示阶段或分支名称有差异</li>}
      </ul>}
      <button type="button" className={button} onClick={w.forkDraft}>另存本地草稿</button>
      <button type="button" className={`${button} ml-2`} disabled={w.busy} onClick={() => { if (confirm('采用服务端版本并放弃本地未保存改动？')) void w.acceptRemote() }}>采用服务端版本</button>
    </section>}
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
    {!!w.data.coordinations?.length && <section aria-label="协调请求记录" className="space-y-2 rounded-xl border border-slate-700 p-3">
      <h4 className="font-medium">协调请求</h4>
      {w.data.coordinations.map(c => <div key={c.id} className="border-t border-slate-700 pt-2 first:border-0">
        <p>{({ design: '规划草稿', execute: '协调执行', replan: '调整运行' })[c.mode]} · {statusLabel(c.status)}</p>
        <p className="line-clamp-2 text-slate-500">{c.goal}</p>
        <p className="text-slate-500">共享决策 {c.used_decisions ?? '未知'} / {c.decision_limit ?? '不限'}</p>
        {c.error_code && <p className="text-amber-600">{c.error_code}</p>}
        <div className="mt-1 flex flex-wrap gap-3">
          <button type="button" className="text-indigo-500" onClick={() => setRecord(c)}>查看协调工具记录</button>
          {['queued', 'running', 'stopping'].includes(c.status) && <button type="button" disabled={w.busy || c.status === 'stopping'} className="text-indigo-500 disabled:opacity-40" onClick={() => void w.cancelCoordination(c)}>停止协调</button>}
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
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let live = true
    const epoch = getAuthEpoch(), previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    void workflows.coordinationMessage(w.conversation.id, value.id).then(m => { if (live && epoch === getAuthEpoch()) setMessage(m) })
      .catch(() => { if (live) setError('协调记录读取失败，请关闭后重新打开。') })
    return () => { live = false; if (previous?.isConnected) previous.focus() }
  }, [value.id, w.conversation.id])
  return <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4">
    <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="协调执行记录" className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl border border-slate-700 bg-panel p-4 outline-none" onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); onClose() }
      if (event.key === 'Tab') {
        const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,a[href],textarea,select') ?? [])
        const first = items[0], last = items.at(-1)
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus() }
      }
    }}>
      <div className="mb-3 flex items-center justify-between"><h4>协调执行记录</h4><button type="button" onClick={onClose}>关闭协调记录</button></div>
      {error ? <p role="alert">{error}</p> : message ? <MessageParts message={message} isOwner /> : <p className="text-slate-500">尚无可显示的模型消息。</p>}
    </div>
  </div>
}
