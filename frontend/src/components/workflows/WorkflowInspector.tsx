import { lazy, Suspense } from 'react'
import { X } from 'lucide-react'
import { useAppStore } from '../../store/app'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { WorkflowFeedbackPanel, FeedbackCard } from './WorkflowFeedback'
import { WorkflowLinks } from './WorkflowLinks'
import { WorkflowSettings } from './WorkflowSettings'
import { WorkflowPresentationEditor } from './WorkflowPresentationEditor'
import { workflowRunStatus } from './workflow-target'

const GraphManagement = lazy(() => import('./GraphManagement').then(module => ({ default: module.GraphManagement })))
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40'
const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2 text-sm'

/** 唯一上下文入口；节点、连线、反馈和记录互斥显示，不另开一套画布属性栏。 */
export function WorkflowInspector({ drawer, onClose }: { drawer: boolean; onClose: () => void }) {
  const w = useWorkflow()
  const names = { context: '当前对象', feedback: '节点反馈', coordination: '协调调整', records: '运行与修改记录', advanced: '高级设置', drafts: '草稿与恢复' }
  const feedback = w.run?.feedback?.find(item => item.id === w.feedbackFocusId)
  return <section aria-label="工作流上下文" className="workflow-inspector space-y-4 text-xs">
    <div className="workflow-inspector-heading"><div><span className="text-slate-500">{w.target.label}</span><h3>{w.inspectorPage === 'context' ? feedback ? '反馈处置' : w.selected ? '节点属性与结果' : w.selectedEdge ? '连线条件' : '流程概况' : names[w.inspectorPage]}</h3></div>
      <button type="button" aria-label={drawer ? '返回画布' : '关闭工作流详情'} onClick={onClose}><X size={16} /></button></div>
    {(w.inspectorPage !== 'context' || w.selected || w.selectedEdge || feedback) && <button type="button" className="text-indigo-600" onClick={() => { w.selectNode(null); w.selectEdge(''); w.setInspectorPage('context') }}>流程概况</button>}
    {drawer && w.error && <p role="alert" className="text-red-500">{w.error}</p>}
    <div hidden={w.inspectorPage !== 'context'}>
      {feedback ? <><button type="button" className="mb-3 text-indigo-600" onClick={() => { w.selectNode(null); w.setInspectorPage('feedback') }}>返回反馈列表</button>
        <FeedbackCard key={feedback.id} item={feedback} readOnly={Boolean(w.historyGraph)} /></>
        : w.selected ? <WorkflowNodeInspector key={`${w.target.kind}:${w.target.id}:${w.selected}`} />
          : w.selectedEdge ? <WorkflowLinks selectionOnly /> : <WorkflowOverview />}
    </div>
    <div hidden={w.inspectorPage !== 'feedback'}>{w.mode === 'run' && <WorkflowFeedbackPanel compact />}</div>
    {w.inspectorPage === 'coordination' && <WorkflowCoordination key={`${w.target.kind}:${w.target.id}:${w.coordinationNodeId ?? ''}`} />}
    {w.inspectorPage === 'records' && <>
      {w.mode === 'run' && w.run && <>
        <label className="block">查看图版本<select aria-label="查看运行图版本" value={w.historyGraph?.revision ?? ''} className={field} onChange={e => void w.selectHistory(e.target.value)}>
          <option value="">当前有效图</option>{w.run.graph_versions?.map(version => <option key={version.graph_revision} value={version.graph_revision}>版本 {version.graph_revision} · {({ applied: '已采用', pending: '等待循环边界', not_applied: '未采用', superseded: '已被替代' } as Record<string, string>)[version.status] ?? version.status}</option>)}
        </select></label>
        {w.historyGraph && <p className="text-amber-700">历史只读。返回当前运行后再确认、重试或调整。</p>}
        {w.run.attempts.filter(a => ['plan', 'summary'].includes(a.phase ?? '') && (!w.historyGraph || a.graph_revision === w.historyGraph.revision)).map(attempt => <button type="button" key={attempt.id} className="mt-2 block text-indigo-600" onClick={() => w.selectAttempt(attempt.id)}>{attempt.phase === 'plan' ? '协调规划' : '协调汇总'} · 查看完整记录</button>)}
      </>}
      <Suspense fallback={<p>读取记录…</p>}><GraphManagement /></Suspense>
    </>}
    {w.inspectorPage === 'advanced' && <div className="space-y-3">
      {w.target.editable && <><WorkflowSettings /><WorkflowPresentationEditor /><WorkflowLinks /></>}
      <details><summary className="cursor-pointer">兼容与原始数据</summary><p className="mt-2 text-slate-500">{w.graph.runtime_version === 2 ? '支持并行、条件和显式循环。' : '旧版流程按完整串行路径执行；升级前需补齐分支与循环规则。'}</p>
        <pre className="mt-2 whitespace-pre-wrap break-all text-[11px]">{JSON.stringify(w.graph, null, 2)}</pre></details>
      <details><summary className="cursor-pointer">操作帮助</summary><p className="mt-2 text-slate-500">画布选中真实节点后，Tab 在后方插入任务；Delete 只删除选中执行连线或无连接节点。输入框和只读视图不会触发结构修改。返回对话不停止运行。</p></details>
    </div>}
    {w.inspectorPage === 'drafts' && <WorkflowDrafts />}
  </section>
}

function WorkflowOverview() {
  const w = useWorkflow(), bindings = useAppStore(state => state.workspaceBindings)
  const id = w.mode === 'run' ? w.run?.workspace_binding_id : w.conversation.workspace_binding_id
  const active = w.data.runs.find(run => ['queued', 'running', 'waiting', 'stopping'].includes(run.status))
  return <div className="space-y-4">
    {w.target.editable ? <>
      <label className="block">流程名称<input aria-label="流程名称" value={w.draft.definition.name} disabled={w.busy || Boolean(w.draft.runTarget)} maxLength={128} className={field} onChange={e => w.update({ ...w.draft.definition, name: e.target.value })} /></label>
      {w.target.kind === 'template' && <label className="block">本次运行补充要求<textarea aria-label="本次运行补充要求" value={w.draft.input} disabled={w.busy} rows={3} className={field} onChange={e => w.input(e.target.value)} /></label>}
      {w.target.kind === 'run-edit' && <p className="text-indigo-700">提交只调整本次运行图；原有执行、工具权限和已提交事实保留。</p>}
      {!w.graph.nodes.length && <p className="text-slate-500">从顶部“更多操作”添加任务，或请协调者根据目标规划。</p>}
    </> : <>
      <h4 className="font-medium">{w.target.name}</h4>
      <p>{workflowRunStatus(w.run, statusLabel)}</p>
      {w.run && <p className="text-slate-500">决策 {w.run.used_decisions ?? '未知'} / {w.run.decision_limit ?? '不限'}</p>}
      {w.run?.error_code && <p className="text-amber-700">{w.run.error_code} · 请选节点核对原记录。</p>}
      <p className="text-slate-500">选中节点查看结果、反馈或请求局部调整。</p>
    </>}
    <p className="text-slate-500">工作区：{id ? bindings.find(binding => binding.id === id)?.display_name ?? '信息不可用' : '未绑定'}</p>
    <p className="text-slate-500">{w.graph.nodes.length} 个节点 · {w.graph.edges.length} 条依赖</p>
    {active && w.mode !== 'run' && <button type="button" className="text-indigo-600" onClick={() => void w.selectRun(active.id)}>查看正在进行的运行</button>}
    {w.target.kind === 'template' && w.conversation.type === 'group' && <>
      <p>群协调者：{w.conversation.orchestrator_role_id ? w.data.member_capabilities?.find(role => role.role_id === w.conversation.orchestrator_role_id)?.name ?? '已任命' : '未任命，请在会话成员中设置'}</p>
      <label className="flex gap-2"><input type="checkbox" checked={w.automaticFeedback} onChange={e => w.setAutomaticFeedback(e.target.checked)} />协调执行时自动处理节点反馈</label>
      <p className="text-slate-500">启用后允许本次协调执行局部调整并安排验证，沿用原预算。</p>
    </>}
    <button type="button" className="text-indigo-600" onClick={() => w.revealInspector('advanced')}>查看高级设置</button>
  </div>
}

function WorkflowCoordination() {
  const w = useWorkflow(), template = w.target.kind === 'template'
  const goal = w.coordinationInput
  const node = w.graph.nodes.find(node => node.id === w.coordinationNodeId)
  const active = w.data.coordinations?.filter(c => (template ? c.definition_id === w.target.id && !c.run_id : c.run_id === w.target.id || c.started_run_id === w.target.id) && ['queued', 'running', 'stopping'].includes(c.status)) ?? []
  const appointed = Boolean(w.conversation.orchestrator_role_id && w.conversation.orchestrator_enabled)
  return <div className="space-y-3">
    <p>{template ? '规划当前模板' : '调整本次运行'}{node ? ` · ${node.title}` : ''}</p>
    <p className="text-slate-500">{template ? '协调者读取目标、成员能力及约束后修改模板，规划本身不启动。' : '协调者先核对执行与文件事实，再调整必要节点；人工确认仍由你完成。'}</p>
    <label className="block">{node ? '本节点调整要求' : template ? '规划要求' : '运行调整要求'}<textarea aria-label={node ? '本节点调整要求' : template ? '规划要求' : '运行调整要求'} rows={5} className={field} value={goal} disabled={w.busy} onChange={e => w.setCoordinationInput(e.target.value)} /></label>
    <details><summary className="cursor-pointer">固定节点与连接</summary>{w.graph.nodes.map(node => <label key={node.id} className="mt-2 flex gap-2"><input type="checkbox" checked={w.protectedNodes.includes(node.id)} onChange={e => w.setProtectedNodes(e.target.checked ? [...w.protectedNodes, node.id] : w.protectedNodes.filter(id => id !== node.id))} />{node.title}</label>)}</details>
    <button type="button" className={button} disabled={w.busy || !appointed || !goal.trim() || Boolean(w.historyGraph) || (!template && ['stopping', 'stopped'].includes(w.run?.status ?? ''))} onClick={() => void w.coordinate(
      node ? `针对节点 ${node.id}（${node.title}）：${goal}\n先核对原记录，只调整本节点及必要依赖，保留其他任务。` : goal,
      w.conversation.orchestrator_role_id ?? 0, template ? 'design' : 'replan')}>发送协调要求</button>
    {!appointed && <p className="text-slate-500">请先到会话成员任命群协调者。</p>}
    {active.map(item => <div key={item.id} className="rounded-lg border border-slate-700 p-3"><p>协调请求 · {statusLabel(item.status)}</p>
      <button type="button" className="mt-2 text-indigo-600" disabled={w.busy || item.status === 'stopping'} onClick={() => void w.cancelCoordination(item)}>停止协调</button>
      <p className="mt-1 text-slate-500">{item.started_run_id ? '此请求已启动运行，停止协调也会停止该运行；已提交的修改仍保留。' : '已提交的修改保留；取消本次调整请求不会重放或抹去原执行。'}</p></div>)}
    <button type="button" className="text-indigo-600" onClick={() => w.setInspectorPage('records')}>查看协调记录</button>
  </div>
}

function WorkflowDrafts() {
  const w = useWorkflow()
  return <section aria-label="本地草稿" className="space-y-3">
    <p role="status">{w.localStatus === 'saved' ? '草稿已保存到此浏览器' : w.localStatus === 'saving' ? '正在保存本地草稿…' : '本地保存失败，请备份或重试。'}</p>
    {w.localNotice && <p>{w.localNotice}</p>}
    <p className="text-slate-500">本地保存与提交模板分开；清除站点数据会删除浏览器副本。</p>
    <button type="button" className={button} onClick={w.exportLocal}>下载草稿备份</button>
    {w.localStatus === 'error' && <button type="button" className={`${button} ml-2`} onClick={w.retryLocal}>重试本地保存</button>}
    <div className="space-y-2">{w.localCopies.map(record => <div key={record.id} className="rounded-lg border border-slate-700 p-3">
      <p>{record.draft.definition.name} · {record.draft.runTarget ? '运行图草稿' : '模板草稿'}</p>
      {record.autoRestore === false && <p className="text-slate-500">另存前的源草稿 · 仅手动恢复</p>}
      <p className="text-slate-500">{new Date(record.updatedAt).toLocaleString()}</p>
      <div className="mt-2 flex gap-3"><button type="button" className="text-indigo-600" onClick={() => void w.restoreLocal(record)}>恢复</button>
        <button type="button" onClick={() => { if (confirm('删除这个恢复副本？当前编辑不受影响。')) void w.removeLocal(record) }}>删除副本</button></div>
    </div>)}</div>
  </section>
}
