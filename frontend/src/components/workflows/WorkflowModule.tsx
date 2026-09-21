import { Hand, Play, Plus, Square } from 'lucide-react'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { useAppStore } from '../../store/app'
import { WorkflowSettings } from './WorkflowSettings'
import { lazy, Suspense } from 'react'
const GraphManagement = lazy(() => import('./GraphManagement').then(module => ({ default: module.GraphManagement })))
import { WorkflowLinks } from './WorkflowLinks'
import { WorkflowFeedbackPanel } from './WorkflowFeedback'
import { WorkflowPresentationEditor } from './WorkflowPresentationEditor'

const button = 'rounded-lg border border-slate-700 bg-panel px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
const field = 'mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'

/** 侧栏统一承载流程编辑与运行操作；窄屏打开画布/添加节点后关闭抽屉。 */
export function WorkflowModule({ onExpand, drawer }: { onExpand: () => void; drawer: boolean }) {
  const w = useWorkflow()
  const { user, workspaceBindings } = useAppStore()
  if (!user?.is_owner) return <p className="text-xs text-slate-500">流程由 Owner 管理，可在对话中查看允许公开的执行摘要。</p>
  if (!w.localReady) return <div className="space-y-2 text-xs"><p role="status">{w.localNotice || '正在恢复本地草稿…'}</p>{w.localStatus === 'error' && <button className={button} onClick={w.retryLocal}>重试草稿恢复</button>}</div>
  const coordinator = user?.is_owner ? useAppStore.getState().roles.find(role => role.id === w.conversation.orchestrator_role_id) : null
  const appointed = w.conversation.type === 'group' && w.conversation.orchestrator_enabled && (w.conversation.orchestrator_revision ?? 0) > 0
  const terminal = w.run && !['queued', 'running', 'waiting', 'stopping'].includes(w.run.status)
  const waitingFeedback = w.run?.runtime_version === 2 && !w.run.activations?.some(a => a.status === 'waiting')
    && (w.run.activations?.some(a => a.status === 'waiting_feedback') || w.run.feedback?.some(item => item.source_current && item.blocking && ['open', 'in_progress', 'waiting', 'review'].includes(item.status)))
  const workspaceId = w.mode === 'run' && w.run ? w.run.workspace_binding_id : w.conversation.workspace_binding_id
  const workspaceName = workspaceId === null ? '未绑定工作区' : workspaceBindings.find(b => b.id === workspaceId)?.display_name ?? '工作区信息不可用'
  return <div className="space-y-4 text-xs">
    <section aria-label="本地草稿" className="space-y-2 rounded-xl border border-slate-800 p-3">
      <p role="status">{w.localStatus === 'saved' ? '草稿已保存到此浏览器' : w.localStatus === 'saving' ? '正在保存本地草稿…' : '本地草稿保存失败，请下载备份或重试'}</p>
      <p className="text-slate-500">本地保存不提交服务端；清除浏览器站点数据会删除草稿。</p>
      {w.localNotice && <p>{w.localNotice}</p>}
      <div className="flex gap-3"><button onClick={w.exportLocal} className="text-indigo-500">下载草稿备份</button>{w.localStatus === 'error' && <button onClick={w.retryLocal} className="text-indigo-500">重试本地保存</button>}</div>
      {w.localCopies.length > 0 && <details><summary className="cursor-pointer">恢复副本（{w.localCopies.length}）</summary>{w.localCopies.map(record => <div key={record.id} className="mt-2 space-x-2">
        <span>{record.draft.definition.name} · {new Date(record.updatedAt).toLocaleString()}</span>
        <button className="text-indigo-500" onClick={() => { if (!w.draft.dirty || confirm('切换前会保留当前编辑为本地副本，恢复所选副本？')) void w.restoreLocal(record) }}>恢复</button>
        <button className="text-slate-500" onClick={() => { if (confirm('删除这个恢复副本？当前编辑不受影响。')) void w.removeLocal(record) }}>删除副本</button>
      </div>)}</details>}
    </section>
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
      {w.draft.runTarget && <p className="font-medium text-indigo-600">正在编辑本次运行图 · 不修改流程模板</p>}
      <p className="text-slate-500">{w.draft.dirty ? '有尚未提交到服务端的编辑' : `已保存版本 ${w.draft.definition.revision || '尚无'}`}</p>
      <label className="block text-slate-500">流程名称<input aria-label="流程名称" disabled={Boolean(w.draft.runTarget)} value={w.draft.definition.name} maxLength={128} onChange={e => w.update({ ...w.draft.definition, name: e.target.value })} className={field} /></label>
      <label className="block text-slate-500">本次运行补充要求<textarea aria-label="本次运行补充要求" rows={2} value={w.draft.input} onChange={e => w.input(e.target.value)} className={field} /></label>
      <p className="text-slate-500">工作区：{workspaceName}</p>
      <div className="grid grid-cols-1 gap-2">
        <button type="button" onClick={() => { w.addNode('role'); onExpand() }} className={button}><Plus size={12} className="mr-1 inline" />添加角色任务</button>
        <button type="button" onClick={() => { w.addNode('approval'); onExpand() }} className={button}><Hand size={12} className="mr-1 inline" />添加人工确认</button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" disabled={w.busy || w.conflict} onClick={() => void w.save()} className={button}>保存流程</button>
        <button type="button" disabled={w.busy || w.conflict || Boolean(w.draft.runTarget) || w.draft.dirty || !w.draft.definition.revision} onClick={() => void (async () => {
          if (await w.start()) { w.setOpen(true); onExpand() }
        })()} className="rounded-lg bg-indigo-600 px-3 py-2 text-white disabled:opacity-40"><Play size={12} className="mr-1 inline" />启动流程</button>
      </div>
      {w.conversation.type === 'group' && <div className="space-y-2 border-t border-slate-700 pt-3">
        <p className="text-slate-500">群协调者：{appointed ? coordinator?.name ?? '角色不可用' : '尚未任命，请到成员模块设置'}</p>
        <label className="flex gap-2"><input type="checkbox" checked={w.automaticFeedback} onChange={e => w.setAutomaticFeedback(e.target.checked)} />协调执行时自动处理节点反馈</label>
        {w.automaticFeedback && <p className="text-slate-500">允许协调者在本次授权内局部调整运行图并安排验证，沿用原决策预算。</p>}
        <button type="button" disabled={!appointed || w.busy || w.conflict || Boolean(w.draft.runTarget) || (!w.draft.definition.revision && !w.draft.input.trim())} className={button} onClick={() => void (async () => {
          if (await w.start('coordinated')) { w.setOpen(true); onExpand() }
        })()}>协调执行</button>
        {!w.draft.runTarget && <button type="button" disabled={!appointed || w.busy || w.conflict} className={button} onClick={() => void w.coordinate(w.draft.input, w.conversation.orchestrator_role_id ?? 0, 'design')}>规划 / 调整草稿</button>}
        <p className="text-slate-500">先填写目标或调整要求。规划只修改草稿；协调执行允许规划完成后启动。也可在聊天输入 /plan@协调者。</p>
        <details><summary className="cursor-pointer text-slate-500">固定节点与连接（可选）</summary>{w.graph.nodes.map(n => <label key={n.id} className="mt-1 flex gap-2"><input type="checkbox" checked={w.protectedNodes.includes(n.id)} onChange={e => w.setProtectedNodes(e.target.checked ? [...w.protectedNodes, n.id] : w.protectedNodes.filter(id => id !== n.id))} />{n.title}</label>)}</details>
      </div>}
      {w.run && <button type="button" onClick={() => w.setMode('run')} className="text-indigo-500">查看运行</button>}
    </section> : w.run && <section aria-label="工作流运行操作" className="space-y-3 rounded-2xl border border-slate-800 bg-panel p-4">
      <p role="status">{w.run.status === 'waiting' && waitingFeedback ? '等待反馈处置' : statusLabel(w.run.status)} · 定义快照 v{w.run.definition_revision}</p>
      <p className="text-slate-500">{w.run.runtime_version === 2 ? `活跃节点 ${w.run.activations?.filter(a => a.status === 'active').length ?? 0}` : `步骤 ${Math.min((w.run.cursor ?? 0) + 1, w.run.graph.nodes.length)} / ${w.run.graph.nodes.length}`} · 决策 {w.run.used_decisions ?? '未知'} / {w.run.decision_limit ?? '不限'}</p>
      <p className="text-slate-500">工作区：{workspaceName}</p>
      {w.run.runtime_version === 2 && <div className="space-y-2">
        <p>当前执行图：{w.run.graph_revision == null ? '旧版本记录' : `v${w.run.graph_revision}`}{w.run.pending_graph_revision ? ` · v${w.run.pending_graph_revision} 等待循环边界` : ''}</p>
        <label className="block">查看图版本<select aria-label="查看运行图版本" className={field} value={w.historyGraph?.revision ?? ''} onChange={e => void w.selectHistory(e.target.value)}>
          <option value="">当前有效图</option>{w.run.graph_versions?.map(v => <option key={v.graph_revision} value={v.graph_revision}>图 v{v.graph_revision}{v.legacy ? '（旧记录）' : ''} · {v.status}</option>)}
        </select></label>
        {w.historyGraph && <p className="text-amber-600">历史图只读；这里只展示属于该版本的尝试。</p>}
        <button type="button" disabled={w.busy || w.run.status === 'stopping' || w.run.status === 'stopped'} className={button} onClick={() => void w.editRun()}>编辑运行图</button>
        {appointed && <><details><summary className="cursor-pointer text-slate-500">固定节点与连接（本次调整）</summary>{w.graph.nodes.map(n => <label key={n.id} className="mt-1 flex gap-2"><input type="checkbox" checked={w.protectedNodes.includes(n.id)} onChange={e => w.setProtectedNodes(e.target.checked ? [...w.protectedNodes,n.id] : w.protectedNodes.filter(id => id !== n.id))} />{n.title}</label>)}</details><label className="block">运行调整要求<textarea aria-label="运行调整要求" className={field} value={w.draft.input} onChange={e => w.input(e.target.value)} /></label>
          <button type="button" disabled={w.busy || !w.draft.input.trim() || w.run.status === 'stopping' || w.run.status === 'stopped'} className={button} onClick={() => void w.coordinate(w.draft.input, w.conversation.orchestrator_role_id ?? 0, 'replan')}>让协调者调整运行</button></>}
      </div>}
      {Object.entries(w.run.loop_states ?? {}).map(([id, state]) => <p key={id} className="text-slate-500">循环 {id.slice(0, 6)} · 第 {state.iteration + 1} 轮 · {state.exited ? '已退出' : state.limited ? '达到配置上限' : '进行中'}</p>)}
      {w.run.attempts.filter(a => a.phase === 'plan' || a.phase === 'summary').map(a => <button key={a.id} type="button" className="block text-indigo-500" onClick={() => { w.selectAttempt(a.id); w.setOpen(true); onExpand() }}>{a.phase === 'plan' ? '协调规划' : '协调汇总'} · {statusLabel(a.status)} · 查看记录</button>)}
      {w.run.error_code && <p className="text-amber-600">{w.run.error_code} · 已提交事实保留，请选择节点查看。</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={w.busy || Boolean(terminal)} onClick={() => void w.control({ action: 'stop' })} className={button}><Square size={12} className="mr-1 inline" />停止此运行</button>
        {terminal && (w.run.runtime_version === 2 || ((w.run.cursor ?? 0) < w.run.graph.nodes.length && !w.run.attempts.some(a => a.current && a.node_id === w.run!.graph.nodes[w.run!.cursor ?? 0].id))) &&
          <button type="button" disabled={w.busy} onClick={() => void w.control({ action: 'resume' })} className={button}>继续执行剩余步骤</button>}
        {terminal && <button type="button" onClick={() => { if (w.newRun()) onExpand() }} className={button}>准备新运行</button>}
      </div>
      <button type="button" onClick={() => { if (w.editDefinition()) onExpand() }} className="text-indigo-500">编辑流程定义</button>
      <p className="text-slate-500">修改定义只影响后续新运行，不改变本次快照。</p>
      {!w.open && <button type="button" onClick={() => { w.setOpen(true); onExpand() }} className="text-indigo-500">查看运行画布</button>}
    </section>}

    {w.mode === 'run' && <WorkflowFeedbackPanel />}
    {w.graphNotice && <p role="status" className="text-slate-500">{w.graphNotice}</p>}
    {w.error && <p role="alert" className="text-red-500">{w.error}</p>}
    <Suspense fallback={<p className="text-slate-500">读取图管理记录…</p>}><GraphManagement /></Suspense>
    <WorkflowSettings />
    <WorkflowLinks />
    <WorkflowPresentationEditor key={w.draft.definition.id + ':' + (w.draft.runTarget ?? '')} />
    <details className="rounded-xl border border-slate-800 p-3 text-slate-500">
      <summary className="cursor-pointer">操作帮助</summary>
      <p className="mt-2">拖动仅改变节点位置。v2 支持并行依赖、结构化条件与显式循环。旧图需补齐条件及回边语义后运行。</p>
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
