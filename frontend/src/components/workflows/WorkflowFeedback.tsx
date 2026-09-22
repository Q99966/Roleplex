import { useRef, useState } from 'react'
import type { FeedbackAction, FeedbackCategory, FeedbackUpdate, WorkflowAttempt, WorkflowFeedback } from '../../api/workflows'
import { useWorkflow, statusLabel } from './WorkflowContext'

const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
export const feedbackCategories: Record<FeedbackCategory, string> = {
  implementation: '实现问题', contract: '契约冲突', capability: '能力缺口', unverified: '未验证', suggestion: '改进建议',
}
const states: Record<string, string> = { open: '待处理', in_progress: '处理中', waiting: '等待条件', review: '待复核', resolved: '已解决', dismissed: '未采纳', accepted: '接受遗留', obsolete: '已失效' }
const actions: Record<FeedbackAction, string> = { coordinate: '交给协调者', assign: '关联处理节点', wait: '等待条件', review: '交回复核', resolve: '核验并解决', dismiss: '不采纳', accept: '接受遗留', obsolete: '标记失效', reopen: '重新打开' }
const closed = (item: WorkflowFeedback) => ['resolved', 'dismissed', 'accepted', 'obsolete'].includes(item.status)

/** 运行反馈集中显示来源、处置和验证；原消息仍从原尝试打开。 */
export function WorkflowFeedbackPanel({ nodeId, compact = false }: { nodeId?: string; compact?: boolean } = {}) {
  const w = useWorkflow()
  const [showClosed, setShowClosed] = useState(false)
  if (!w.run) return null
  const items = (w.run.feedback ?? []).filter(item => (!nodeId || item.node_id === nodeId || item.handler_node_ids.includes(nodeId))
    && (!w.historyGraph || item.graph_revision === w.historyGraph.revision))
  const pending = items.filter(item => !closed(item))
  return <section aria-label="节点反馈" className="space-y-3 rounded-2xl border border-slate-800 bg-panel p-4 text-xs">
    <div className="flex items-center justify-between gap-2"><h4 className="font-semibold">节点反馈 · {pending.length} 项待处理</h4>
      {!!items.length && <label className="flex gap-1"><input type="checkbox" checked={showClosed} onChange={e => setShowClosed(e.target.checked)} />显示已处置</label>}
    </div>
    <p className="text-slate-500">{w.historyGraph ? '显示来源属于此图版本的反馈及其最新处置记录。返回当前运行后再处理。' : w.run.feedback_mode === 'automatic' ? '已授权协调者自动处置，沿用本次运行预算。' : '先记录意见，由你处置或交给协调者。'}</p>
    {!items.length && <p className="text-slate-500">暂无反馈。可从节点的“结果与执行事实”提交问题。</p>}
    {!!items.length && !pending.length && !showClosed && <p className="text-slate-500">本次反馈均已有处置记录；接受遗留不表示验证通过。</p>}
    {(showClosed ? items : pending).map(item => compact ? <button type="button" key={item.id} aria-label={`打开反馈：${item.summary}`} className="workflow-feedback-row" onClick={() => w.focusFeedback(item.id)}>
      <span>{feedbackCategories[item.category] ?? item.category} · {states[item.status] ?? item.status}</span><strong>{item.summary}</strong>
    </button> : <FeedbackCard key={item.id} item={item} readOnly={Boolean(w.historyGraph)} />)}
  </section>
}

export function FeedbackCard({ item, readOnly = false }: { item: WorkflowFeedback; readOnly?: boolean }) {
  const w = useWorkflow()
  const [action, setAction] = useState<FeedbackAction>('wait')
  const [reason, setReason] = useState('')
  const [manual, setManual] = useState(false)
  const [handler, setHandler] = useState('')
  const [nodes, setNodes] = useState<string[]>([])
  const requests = useRef(new Map<string, string>())
  const source = w.run!.attempts.find(attempt => attempt.id === item.attempt_id)
  const roleName = (id: number | null) => w.data.member_capabilities?.find(role => role.role_id === id)?.name ?? (id ? `角色 ${id}` : '人工节点')
  const nodeTitle = (id: string) => w.run!.graph.nodes.find(node => node.id === id)?.title ?? id
  const coordination = w.data.coordinations?.find(value => value.id === item.coordination_session_id)
  const appointed = w.conversation.orchestrator_enabled && (w.conversation.orchestrator_revision ?? 0) > 0
  const stopped = ['stopping', 'stopped'].includes(w.run!.status)
  const availableNodes = w.run!.graph.nodes.filter(node => node.kind === 'role' && node.role_id === Number(handler) && node.id !== item.node_id)
  const proof = w.run!.attempts.find(attempt => attempt.id === item.verification_attempt_id)
  async function submit(selected: FeedbackAction) {
    const payload: Omit<FeedbackUpdate, 'request_key'> = {
      action: selected, reason: reason.trim(), expected_revision: item.revision,
      ...(selected === 'assign' ? { handler_role_id: Number(handler), handler_node_ids: nodes } : {}),
      ...(selected === 'resolve' ? { manual_verification: manual, verification_attempt_id: item.verification_attempt_id ?? undefined } : {}),
    }
    const signature = JSON.stringify(payload)
    const key = requests.current.get(signature) ?? crypto.randomUUID()
    requests.current.set(signature, key)
    if (await w.updateFeedback(item.id, { ...payload, request_key: key })) { setReason(''); setManual(false) }
  }
  return <article aria-label={`反馈：${item.summary}`} className="space-y-2 rounded-xl border border-slate-700 p-3">
    <div className="flex flex-wrap gap-2 text-slate-500"><span>{feedbackCategories[item.category] ?? item.category}</span><span>{states[item.status] ?? item.status}</span>{item.blocking && !closed(item) && item.source_current && <span className="text-amber-600">影响后续步骤</span>}</div>
    <h5 className="font-semibold">{item.summary}</h5>
    <p className="text-slate-500">{source?.node_snapshot?.title ?? nodeTitle(item.node_id)} · {roleName(item.source_role_id)} · 图 {item.graph_revision == null ? '旧记录' : `v${item.graph_revision}`} · 第 {item.iteration + 1} 轮 · 尝试 {source?.number ?? '未知'}</p>
    {!item.source_current && <p className="text-amber-600">来自历史尝试，不阻塞当前结果。</p>}
    <button className="text-indigo-500" onClick={() => { w.selectAttempt(item.attempt_id); w.setOpen(true) }}>查看反馈来源</button>
    <button className="ml-3 text-indigo-500" onClick={() => w.focusFeedback(item.id)}>定位处理路径</button>
    {item.details && <p className="whitespace-pre-wrap break-words">{item.details}</p>}
    {item.category === 'capability' && <div className="space-y-1 text-slate-500">
      <p>当时分配：{item.capability_check.assigned_tools?.join('、') || '无工具'}</p>
      {!!item.capability_check.unavailable_tools?.length && <p>当前未授权：{item.capability_check.unavailable_tools.join('、')}</p>}
      {!!item.capability_check.unassigned_tools?.length && <p>角色可用但本任务未分配：{item.capability_check.unassigned_tools.join('、')}</p>}
    </div>}
    {!!item.handler_node_ids.length && <p>处理者：{roleName(item.handler_role_id)} · {item.handler_node_ids.map(nodeTitle).join('、')}</p>}
    {proof && <button className="text-indigo-500" onClick={() => { w.selectAttempt(proof.id); w.setOpen(true) }}>查看验证尝试 · {statusLabel(proof.status)}</button>}
    {item.coordination_requested && <p role="status">等待交给协调者</p>}
    {coordination && <p className="text-slate-500">协调处置：{statusLabel(coordination.status)}{coordination.error_code ? ` · ${coordination.error_code}` : ''}</p>}
    {!!item.graph_changes.length && <details><summary className="cursor-pointer">相关图修改（{item.graph_changes.length}）</summary>{item.graph_changes.map(version => <div key={version.graph_revision} className="mt-2 space-y-1">
      <button className="text-indigo-500" onClick={() => void w.selectHistory(String(version.graph_revision))}>查看图 v{version.graph_revision} · {version.status}</button>
      {!!version.changes.added?.length && <p>新增：{version.changes.added.map(nodeTitle).join('、')}</p>}
      {!!version.changes.updated?.length && <p>修改：{version.changes.updated.map(nodeTitle).join('、')}</p>}
      {!!version.changes.removed?.length && <p>移除：{version.changes.removed.map(nodeTitle).join('、')}</p>}
    </div>)}</details>}
    <details><summary className="cursor-pointer text-slate-500">处置记录（{item.history.length}）</summary><ol className="mt-2 space-y-2">{item.history.map(event => <li key={event.id}>
      <p>{event.actor_kind === 'role' ? event.action === 'created' ? '工作角色' : '协调者' : event.actor_kind === 'system' ? '系统' : 'Owner'} · {states[event.status] ?? event.status}</p>
      <p className="whitespace-pre-wrap text-slate-500">{event.reason}</p>
    </li>)}</ol></details>
    {!readOnly && <details><summary className="cursor-pointer text-indigo-500">{closed(item) ? '重新处理' : '处理这条反馈'}</summary>
      <div className="mt-2 space-y-3">
        <label className="block">处置依据<textarea aria-label="处置依据" className={field} rows={2} maxLength={4000} value={reason} onChange={e => setReason(e.target.value)} placeholder="说明裁定、验证证据或等待条件" /></label>
        {!closed(item) && <button className={button} disabled={w.busy || stopped || !appointed || !reason.trim() || item.coordination_requested} onClick={() => void submit('coordinate')}>交给协调者处理</button>}
        <label className="block">处置方式<select aria-label="处置方式" className={field} value={closed(item) ? 'reopen' : action} onChange={e => setAction(e.target.value as FeedbackAction)}>
          {(closed(item) ? ['reopen'] as const : ['wait', 'assign', 'review', 'resolve', 'dismiss', 'accept', 'obsolete'] as const).map(value => <option key={value} value={value}>{actions[value]}</option>)}
        </select></label>
        {!closed(item) && action === 'assign' && <>
          <p className="text-slate-500">先在运行图中安排尚未派发的处理任务，再关联到这条反馈。</p>
          <button className="text-indigo-500" disabled={w.busy || stopped} onClick={() => void w.editRun()}>编辑运行图</button>
          <label className="block">处理角色<select aria-label="反馈处理角色" className={field} value={handler} onChange={e => { setHandler(e.target.value); setNodes([]) }}><option value="">选择角色</option>{w.data.member_capabilities?.map(role => <option key={role.role_id} value={role.role_id}>{role.name}</option>)}</select></label>
          <fieldset><legend>处理节点</legend>{availableNodes.map(node => <label key={node.id} className="mt-1 flex gap-2"><input type="checkbox" checked={nodes.includes(node.id)} onChange={e => setNodes(e.target.checked ? [...nodes, node.id] : nodes.filter(id => id !== node.id))} />{node.title}</label>)}</fieldset>
        </>}
        {!closed(item) && action === 'resolve' && <label className="flex gap-2"><input type="checkbox" checked={manual} onChange={e => setManual(e.target.checked)} />我已完成人工核验，并在处置依据中记录证据</label>}
        {!closed(item) && action === 'accept' && <p className="text-amber-600">接受遗留会允许后续继续，记录中保留问题，不算验证通过。</p>}
        <button className={button} disabled={w.busy || !reason.trim() || (!closed(item) && action === 'assign' && (!handler || !nodes.length)) || (!closed(item) && action === 'resolve' && !manual && !proof)} onClick={() => void submit(closed(item) ? 'reopen' : action)}>记录处置</button>
      </div>
    </details>}
  </article>
}

/** Owner 从精确尝试提出新意见；网络重发沿用同一键，不自动重跑来源。 */
export function FeedbackReportForm({ attempt }: { attempt: WorkflowAttempt }) {
  const w = useWorkflow()
  const [category, setCategory] = useState<FeedbackCategory>('contract')
  const [summary, setSummary] = useState('')
  const [details, setDetails] = useState('')
  const [tools, setTools] = useState('')
  const [blocking, setBlocking] = useState(true)
  const [sent, setSent] = useState(false)
  const requests = useRef(new Map<string, string>())
  return <details className="my-3 rounded-xl border border-slate-700 p-3 text-xs"><summary className="cursor-pointer text-indigo-500">提交节点反馈</summary>
    <form className="mt-3 space-y-3" onSubmit={event => { event.preventDefault(); void (async () => {
      const body = { attempt_id: attempt.id, category, summary: summary.trim(), details, blocking, requested_tools: category === 'capability' ? tools.split(/[,，\s]+/).filter(Boolean) : [] }
      const signature = JSON.stringify(body), key = requests.current.get(signature) ?? crypto.randomUUID()
      requests.current.set(signature, key)
      if (await w.reportFeedback({ ...body, request_key: key })) { setSent(true); setSummary(''); setDetails('') }
    })() }}>
      <label className="block">反馈类型<select aria-label="反馈类型" className={field} value={category} onChange={e => { const value = e.target.value as FeedbackCategory; setCategory(value); setBlocking(value !== 'suggestion') }}>{Object.entries(feedbackCategories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="block">反馈标题<input aria-label="反馈标题" required maxLength={240} className={field} value={summary} onChange={e => { setSummary(e.target.value); setSent(false) }} /></label>
      <label className="block">问题依据<textarea aria-label="问题依据" className={field} rows={3} maxLength={8000} value={details} onChange={e => setDetails(e.target.value)} /></label>
      {category === 'capability' && <label className="block">需要的工具<input aria-label="需要的工具" className={field} value={tools} onChange={e => setTools(e.target.value)} placeholder="工具名，用逗号分隔" /></label>}
      <label className="flex gap-2"><input type="checkbox" checked={blocking} onChange={e => setBlocking(e.target.checked)} />处置前等待受影响的后续步骤</label>
      <button className={button} type="submit" disabled={w.busy || !summary.trim()}>提交反馈</button>
      {sent && <p role="status">反馈已登记，可在“节点反馈”中查看和处置。</p>}
    </form>
  </details>
}
