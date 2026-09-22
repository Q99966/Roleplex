import { lazy, Suspense } from 'react'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { useAppStore } from '../../store/app'
const WorkflowInspector = lazy(() => import('./WorkflowInspector').then(module => ({ default: module.WorkflowInspector })))

/** 入口只负责打开工作流；画布展开后，原右栏成为唯一的上下文面板。 */
export function WorkflowModule({ onExpand, drawer }: { onExpand: () => void; drawer: boolean }) {
  const w = useWorkflow(), user = useAppStore(state => state.user)
  if (!user?.is_owner) return <p className="text-xs text-slate-500">流程由 Owner 管理，可在对话中查看允许公开的执行摘要。</p>
  if (!w.localReady) return <div className="space-y-2 text-xs"><p role="status">{w.localNotice || '正在恢复工作流…'}</p>{w.localStatus === 'error' && <button onClick={w.retryLocal}>重试草稿恢复</button>}</div>
  if (w.open) return <Suspense fallback={<p role="status">正在读取上下文…</p>}><WorkflowInspector drawer={drawer} onClose={drawer ? onExpand : () => window.dispatchEvent(new CustomEvent('roleplex:workflow-inspector-close', { detail: w.conversation.id }))} /></Suspense>
  return <section aria-label="工作流入口" className="workflow-entry space-y-4 text-xs">
    <h3 className="text-sm font-semibold">会话工作流</h3>
    <p className="text-slate-500">打开画布后，顶部管理模板与运行，右侧查看选中对象。</p>
    <div className="flex flex-wrap gap-2"><button className="rounded-lg bg-indigo-600 px-3 py-2 text-white" onClick={() => { w.setOpen(true); onExpand() }}>打开工作流</button>
      <button className="rounded-lg border border-slate-700 px-3 py-2" onClick={() => void w.choose().then(ok => { if (ok) onExpand() })}>新建工作流</button></div>
    <p className="text-slate-500">{w.draft.definition.name} · {w.draft.dirty ? '有未提交编辑' : '草稿已保留'}</p>
    <div className="space-y-2"><h4>流程模板</h4>{w.data.definitions.map(item => <button type="button" key={item.id} aria-label={`打开模板：${item.name}`} className="workflow-entry-item" onClick={() => void w.choose(item).then(ok => { if (ok) onExpand() })}>{item.name}<span>版本 {item.revision}</span></button>)}</div>
    {!!w.data.runs.length && <div className="space-y-2"><h4>运行记录</h4>{w.data.runs.map(item => <button type="button" key={item.id} aria-label={`打开运行：${item.name} ${item.id.slice(0, 6)}`} className="workflow-entry-item" onClick={() => void w.selectRun(item.id).then(ok => { if (ok) onExpand() })}>{item.name}<span>{statusLabel(item.status)}</span></button>)}</div>}
  </section>
}
