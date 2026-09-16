import { Hand, Play, Plus, Square } from 'lucide-react'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { useAppStore } from '../../store/app'
import { WorkflowLinks } from './WorkflowLinks'

const button = 'rounded-lg border border-slate-700 bg-panel px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
const field = 'mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'

/** 侧栏统一承载流程编辑与运行操作；窄屏打开画布/添加节点后关闭抽屉。 */
export function WorkflowModule({ onExpand, drawer }: { onExpand: () => void; drawer: boolean }) {
  const w = useWorkflow()
  const { user, workspaceBindings } = useAppStore()
  if (!user?.is_owner) return <p className="text-xs text-slate-500">流程由 Owner 管理，可在对话中查看允许公开的执行摘要。</p>
  const terminal = w.run && !['queued', 'running', 'waiting', 'stopping'].includes(w.run.status)
  const workspaceId = w.mode === 'run' && w.run ? w.run.workspace_binding_id : w.conversation.workspace_binding_id
  const workspaceName = workspaceId === null ? '未绑定工作区' : workspaceBindings.find(b => b.id === workspaceId)?.display_name ?? '工作区信息不可用'
  return <div className="space-y-4 text-xs">
    <div className="space-y-3 rounded-2xl border border-slate-800 bg-panel p-4">
      <div className="flex flex-wrap gap-3">
        <button type="button" onClick={() => { if (w.choose()) onExpand() }} className={button}>新建工作流</button>
        {(!w.open || drawer) && <button type="button" onClick={() => { w.setOpen(true); onExpand() }} className="text-indigo-500">{w.open ? '返回画布' : '展开画布'}</button>}
      </div>
      <details open={!w.open}>
        <summary className="cursor-pointer text-slate-500">已保存流程（{w.data.definitions.length}）</summary>
        <div className="mt-2 space-y-2">{w.data.definitions.map(d => <button type="button" key={d.id} onClick={() => { if (w.choose(d)) onExpand() }}
          className="block w-full rounded-lg border border-slate-700 p-2 text-left">{d.name} <span className="text-slate-500">v{d.revision}</span></button>)}</div>
      </details>
    </div>

    {w.mode === 'edit' ? <section aria-label="工作流编辑操作" className="space-y-3 rounded-2xl border border-slate-800 bg-panel p-4">
      <p className="text-slate-500">{w.draft.dirty ? '有未保存编辑 · 关闭页面前请保存' : `已保存版本 ${w.draft.definition.revision || '尚无'}`}</p>
      <label className="block text-slate-500">流程名称<input aria-label="流程名称" value={w.draft.definition.name} maxLength={128} onChange={e => w.update({ ...w.draft.definition, name: e.target.value })} className={field} /></label>
      <label className="block text-slate-500">本次运行补充要求<textarea rows={2} value={w.draft.input} onChange={e => w.input(e.target.value)} className={field} /></label>
      <p className="text-slate-500">工作区：{workspaceName}</p>
      <div className="grid grid-cols-1 gap-2">
        <button type="button" onClick={() => { w.addNode('role'); onExpand() }} className={button}><Plus size={12} className="mr-1 inline" />添加角色任务</button>
        <button type="button" onClick={() => { w.addNode('approval'); onExpand() }} className={button}><Hand size={12} className="mr-1 inline" />添加人工确认</button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" disabled={w.busy || !w.graph.nodes.length} onClick={() => void w.save()} className={button}>保存流程</button>
        <button type="button" disabled={w.busy || w.draft.dirty || !w.draft.definition.revision} onClick={() => void (async () => {
          if (await w.start()) { w.setOpen(true); onExpand() }
        })()} className="rounded-lg bg-indigo-600 px-3 py-2 text-white disabled:opacity-40"><Play size={12} className="mr-1 inline" />启动流程</button>
      </div>
      {w.run && <button type="button" onClick={() => w.setMode('run')} className="text-indigo-500">查看运行</button>}
    </section> : w.run && <section aria-label="工作流运行操作" className="space-y-3 rounded-2xl border border-slate-800 bg-panel p-4">
      <p role="status">{statusLabel(w.run.status)} · 定义快照 v{w.run.definition_revision}</p>
      <p className="text-slate-500">步骤 {Math.min(w.run.cursor + 1, w.run.graph.nodes.length)} / {w.run.graph.nodes.length} · 决策 {w.run.used_decisions ?? '未知'} / {w.run.decision_limit ?? '不限'}</p>
      <p className="text-slate-500">工作区：{workspaceName}</p>
      {w.run.error_code && <p className="text-amber-600">{w.run.error_code} · 已提交事实保留，请选择节点查看。</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={w.busy || Boolean(terminal)} onClick={() => void w.control({ action: 'stop' })} className={button}><Square size={12} className="mr-1 inline" />停止此运行</button>
        {terminal && w.run.cursor < w.run.graph.nodes.length && !w.run.attempts.some(a => a.current && a.node_id === w.run!.graph.nodes[w.run!.cursor].id) &&
          <button type="button" disabled={w.busy} onClick={() => void w.control({ action: 'resume' })} className={button}>继续执行剩余步骤</button>}
        {terminal && <button type="button" onClick={() => { if (w.newRun()) onExpand() }} className={button}>准备新运行</button>}
      </div>
      <button type="button" onClick={() => { if (w.editDefinition()) onExpand() }} className="text-indigo-500">编辑流程定义</button>
      <p className="text-slate-500">修改定义只影响后续新运行，不改变本次快照。</p>
      {!w.open && <button type="button" onClick={() => { w.setOpen(true); onExpand() }} className="text-indigo-500">查看运行画布</button>}
    </section>}

    {w.graphNotice && <p role="status" className="text-slate-500">{w.graphNotice}</p>}
    {w.error && <p role="alert" className="text-red-500">{w.error}</p>}
    <WorkflowLinks />
    <details className="rounded-xl border border-slate-800 p-3 text-slate-500">
      <summary className="cursor-pointer">操作帮助</summary>
      <p className="mt-2">拖动仅改变节点位置。连线可表达分支和环路，当前仅运行完整串行路径。</p>
      <p className="mt-2">选中并聚焦画布节点时，Tab 在它后面插入角色任务；Shift+Tab 和输入框内 Tab 保留焦点导航。选中画布连线后按 Delete 删除；节点需先断开所有连线。编辑文字和查看运行时不会触发删除。</p>
    </details>
    <label className="block text-slate-500">选择运行
      <select aria-label="选择工作流运行" value={w.run?.id ?? ''} onChange={e => w.selectRun(e.target.value)} className={field}>
        <option value="">选择一次运行</option>{w.data.runs.map(r => <option key={r.id} value={r.id}>{r.name} · {statusLabel(r.status)} · {r.id.slice(0, 6)}</option>)}
      </select>
    </label>
    <button type="button" onClick={() => void w.refresh()} className="text-slate-500">刷新工作流</button>
  </div>
}
