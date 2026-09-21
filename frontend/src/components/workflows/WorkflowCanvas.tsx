import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, Check, Trash2, X } from 'lucide-react'
import { useAppStore } from '../../store/app'
import { getAuthEpoch, type Message } from '../../api/client'
import { workflows, type WorkflowNode, type WorkflowAttempt } from '../../api/workflows'
import { MessageParts } from '../MessageParts'
import { NodeRuntimeSettings } from './NodeRuntimeSettings'
import { nodeColor, upstreamNodes } from './workflow-layout'
import { useWorkflow, statusLabel } from './WorkflowContext'

const WorkflowFlow = lazy(() => import('./WorkflowFlow').then(module => ({ default: module.WorkflowFlow })))

const button = 'rounded-lg border border-slate-700 bg-panel px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'

/** 画布覆盖消息与输入，聊天保持挂载和布局；inert 阻止隐藏区抢焦点。 */
export function WorkflowSurface({ children }: { children: ReactNode }) {
  const w = useWorkflow()
  const chat = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (chat.current) chat.current.inert = w.open
    if (!w.open) return
    const previous = document.activeElement as HTMLElement | null
    canvas.current?.focus()
    return () => {
      if (previous?.isConnected) previous.focus()
      else document.querySelector<HTMLButtonElement>('[aria-label="打开会话详情"]')?.focus()
    }
  }, [w.open])
  return <div className="relative min-h-0 flex-1">
    <div ref={chat} aria-hidden={w.open || undefined} className={`flex h-full min-h-0 flex-col ${w.open ? 'invisible pointer-events-none' : ''}`}>{children}</div>
    {w.open && <div ref={canvas} tabIndex={-1} role="region" aria-label="工作流画布" className="absolute inset-0 z-20 flex min-h-0 flex-col bg-slate-950 outline-none">
      <WorkflowCanvas />
    </div>}
  </div>
}

function WorkflowCanvas() {
  const w = useWorkflow()
  const roles = useAppStore(state => state.roles).filter(role => w.conversation.role_ids.includes(role.id) && role.active)
  const { graph, selected, selectNode: setSelected } = w
  const detail = w.detailAttempt
  const setDetail = w.selectAttempt
  const node = graph.nodes.find(n => n.id === selected)
  const priorRoles = useRef<Record<string, number>>({})
  useEffect(() => { priorRoles.current = {} }, [w.draft.definition.id])

  const serial = graph.edges.length === graph.nodes.length - 1 && graph.nodes.slice(1).every((n, i) => graph.edges.some(([a, b]) => a === graph.nodes[i].id && b === n.id))
  function commit(nodes: WorkflowNode[], edges = graph.edges.filter(([a, b]) => nodes.some(n => n.id === a) && nodes.some(n => n.id === b))) {
    w.update({ ...w.draft.definition, graph: { ...graph, nodes, edges } })
  }
  function patch(update: Partial<WorkflowNode>) { commit(graph.nodes.map(n => n.id === selected ? { ...n, ...update } : n)) }
  function move(id: string, to: number) {
    const nodes = [...graph.nodes]
    const from = nodes.findIndex(n => n.id === id)
    if (!serial || from < 0 || to < 0 || to >= nodes.length || from === to) return
    nodes.splice(to, 0, nodes.splice(from, 1)[0])
    // 重排只保留仍在上游的结果引用，避免隐藏反向依赖。
    commit(nodes.map((n, i) => ({ ...n, inputs: n.inputs.filter(input => nodes.slice(0, i).some(prior => prior.id === input)) })),
      nodes.slice(1).map((n, i) => [nodes[i].id, n.id]))
  }
  return <div className="flex h-full min-h-0 flex-col" onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); if (detail) setDetail(null); else if (selected) setSelected(null); else w.setOpen(false) }
  }}>
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 p-3">
      <div><h3 className="text-sm font-semibold">{w.mode === 'run' ? w.run?.name ?? '选择运行' : w.draft.definition.name}</h3>
      </div>
      <button type="button" onClick={() => w.setOpen(false)} className={button}>返回对话</button>
    </header>
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <Suspense fallback={<p role="status" className="flex-1 p-4 text-xs text-slate-500">正在加载画布…</p>}>
      <WorkflowFlow key={`${w.mode}:${w.mode === 'run' ? w.run?.id : w.draft.definition.id}`} graph={graph} run={w.run} editable={w.mode === 'edit'}
        selected={selected} onSelect={setSelected} selectedEdge={w.selectedEdge} onSelectEdge={w.selectEdge} roleNames={Object.fromEntries(roles.map(role => [role.id, role.name]))}
        onGraphChange={w.changeGraph} onInsertAfter={id => w.addNode('role', id)} />
      </Suspense>
      {node && <aside aria-label="节点属性与结果" className="max-h-[55%] w-full shrink-0 overflow-y-auto border-t border-slate-800 bg-panel p-4 lg:max-h-none lg:w-72 lg:border-l lg:border-t-0">
        <div className="mb-3 flex items-center justify-between"><h4 className="text-sm font-semibold">{node.title}</h4><button type="button" aria-label="关闭节点详情" onClick={() => setSelected(null)}><X size={16} /></button></div>
        {w.mode === 'edit' ? <div className="space-y-3 text-xs">
          <label className="block">节点类型<select aria-label="节点类型" className={field} value={node.kind} onChange={e => {
            const kind = e.target.value as WorkflowNode['kind']
            if (node.role_id) priorRoles.current[node.id] = node.role_id
            const role = roles.find(role => role.id === priorRoles.current[node.id]) ?? roles[0]
            w.changeGraph({ ...graph, runtime_version: ['join', 'condition', 'judge'].includes(kind) ? 2 : graph.runtime_version,
              nodes: graph.nodes.map(n => n.id === node.id ? { ...n, kind, role_id: kind === 'role' ? role?.id ?? null : null,
                tools: kind === 'judge' ? [] : kind === 'role' ? n.tools : null,
                condition: ['condition', 'judge'].includes(kind) ? n.condition ?? { sources: kind === 'judge' ? ['$self'] : [], key: 'approved', operator: 'eq', value: true, aggregate: 'all' } : null,
                title: ['角色任务', '人工确认', '结果汇合', '条件选择', '模型判断'].includes(n.title) ? ({ role: '角色任务', approval: '人工确认', join: '结果汇合', condition: '条件选择', judge: '模型判断' })[kind] : n.title } : n) })
          }}><option value="role">角色任务</option><option value="approval">人工确认</option><option value="join">结果汇合</option><option value="condition">条件选择</option><option value="judge">模型判断</option></select></label>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2">节点颜色<input type="color" aria-label="节点颜色" value={nodeColor(node)} onChange={e => patch({ color: e.target.value })} className="h-8 w-12 cursor-pointer rounded border border-slate-700 bg-panel p-1" /></label>
            <button type="button" onClick={() => patch({ color: null })} className="text-indigo-500">恢复默认颜色</button>
          </div>
          <label className="block">节点名称<input aria-label="节点名称" className={field} value={node.title} onChange={e => patch({ title: e.target.value })} /></label>
          {['role', 'judge'].includes(node.kind) && <>
            <label className="block">执行角色<select aria-label="执行角色" className={field} value={node.role_id ?? ''} onChange={e => patch({ role_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">{graph.runtime_version === 2 ? '协调时自动分配（手动运行需指定）' : '选择本会话角色'}</option>{roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
            <label className="block">本步任务<textarea aria-label="本步任务" className={field} rows={4} value={node.task} onChange={e => patch({ task: e.target.value })} /></label>
            <label className="block">预期产出<textarea className={field} rows={2} value={node.expected_output} onChange={e => patch({ expected_output: e.target.value })} /></label>
          </>}
          <fieldset><legend className="mb-1">交接哪些上游结果</legend>{upstreamNodes(graph, node.id).map(n => <label key={n.id} className="my-1 flex gap-2">
            <input type="checkbox" checked={node.inputs.includes(n.id)} onChange={e => patch({ inputs: e.target.checked ? [...node.inputs, n.id] : node.inputs.filter(id => id !== n.id) })} />{n.title}</label>)}</fieldset>
          <NodeRuntimeSettings node={node} patch={patch} />
          <p className="text-slate-500">拖动只调整位置。分支、汇合与循环使用 v2 执行内核；循环需声明入口、回边和出口。</p>
          <button type="button" onClick={() => w.addNode('role', node.id)} className={button}>在后面添加角色任务 · Tab</button>
          <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!serial} onClick={() => move(node.id, graph.nodes.indexOf(node) - 1)}><ArrowUp size={12} className="inline" />前移</button>
            <button type="button" className={button} disabled={!serial} onClick={() => move(node.id, graph.nodes.indexOf(node) + 1)}><ArrowDown size={12} className="inline" />后移</button>
            <button type="button" className={button} disabled={graph.edges.some(([a, b]) => a === node.id || b === node.id)}
              title="仅可删除没有连线的节点" onClick={() => {
                if (graph.edges.some(([a, b]) => a === node.id || b === node.id)) return
                commit(graph.nodes.filter(n => n.id !== node.id).map(n => ({ ...n, inputs: n.inputs.filter(id => id !== node.id) }))); setSelected(null)
              }}><Trash2 size={12} className="inline" />删除</button></div>
          {graph.edges.some(([a, b]) => a === node.id || b === node.id) && <p className="text-slate-500">删除节点前，请先删除它的所有连线。</p>}
        </div> : <div className="space-y-3 text-xs">
          <p className="whitespace-pre-wrap">{node.task || '核对上游结果后决定是否继续。'}</p>
          {w.run?.attempts.filter(a => a.node_id === node.id && (!w.historyGraph || a.graph_revision === w.historyGraph.revision || (a.graph_revision == null && w.historyGraph.revision === 0))).map(a => <div key={a.id} className="rounded-xl border border-slate-700 p-3">
            <p>尝试 {a.number} · 图 {a.graph_revision == null ? '旧记录' : `v${a.graph_revision}`}{a.loop_id ? ` · 第 ${(a.iteration ?? 0) + 1} 轮` : ''} · {a.waiting_resource ? '等待资源' : statusLabel(a.status)}{a.current ? ' · 当前' : ' · 历史结果'}</p>
            {!!a.assigned_tools?.length && <p className="mt-1 break-words text-slate-500">分配：{a.assigned_tools.join(', ')}</p>}
            {a.error_code && <p className="mt-1 text-red-500">{a.error_code}</p>}
            <button type="button" className="mt-2 text-indigo-500" onClick={() => setDetail(a.id)}>查看结果与执行事实</button>
            {!w.historyGraph && a.current && a.status === 'waiting' && <button type="button" disabled={w.busy} className={`${button} mt-2`} onClick={() => void w.control({ action: 'confirm', attempt_id: a.id })}><Check size={12} className="mr-1 inline" />确认继续</button>}
            {!w.historyGraph && a.current && a.status === 'waiting' && w.run?.runtime_version === 2 && <button type="button" className={`${button} mt-2`} disabled={w.busy} onClick={() => void w.control({ action: 'confirm', attempt_id: a.id, decision: false })}>记录不通过并继续判断</button>}
          </div>)}
          {!w.run?.attempts.some(a => a.node_id === node.id) && <p>此节点尚未执行。</p>}
        </div>}
      </aside>}
    </div>
    {detail && w.run?.attempts.find(a => a.id === detail) && <AttemptDetails key={detail} attempt={w.run.attempts.find(a => a.id === detail)!} onClose={() => setDetail(null)} />}
  </div>
}

function AttemptDetails({ attempt, onClose }: { attempt: WorkflowAttempt; onClose: () => void }) {
  const w = useWorkflow()
  const [message, setMessage] = useState<Message | null>(null)
  const [facts, setFacts] = useState('')
  const [error, setError] = useState('')
  const [checked, setChecked] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [downstream, setDownstream] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let live = true
    const epoch = getAuthEpoch()
    const previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    Promise.allSettled([workflows.message(w.conversation.id, w.run!.id, attempt.id), workflows.facts(w.conversation.id, w.run!.id, attempt.id)])
      .then(([msg, result]) => { if (live && epoch === getAuthEpoch()) {
        if (msg.status === 'fulfilled') setMessage(msg.value)
        if (result.status === 'fulfilled') setFacts(result.value.text)
        else setError('当前文件核对不可用；已有消息与结构化记录仍保留，请检查权限和资源。')
      } })
      .catch(() => { if (live && epoch === getAuthEpoch()) setError('详情或当前事实暂不可用，请核查权限和工作区状态后重试。') })
    return () => { live = false; if (previous?.isConnected) previous.focus() }
  }, [attempt.id])
  const terminal = !w.historyGraph && w.run && (w.run.runtime_version === 2 ? ['completed', 'failed', 'blocked', 'stopped', 'interrupted'].includes(attempt.status) && w.run.status !== 'stopping' : !['queued', 'running', 'waiting', 'stopping'].includes(w.run.status))
  return <div className="absolute inset-0 z-30 flex items-center justify-center bg-slate-950/60 p-3">
    <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="节点尝试详情" className="max-h-full w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-700 bg-panel p-4 outline-none" onKeyDown={e => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose() }
      if (e.key === 'Tab') {
        const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,[tabindex="0"]') ?? []).filter(n => n.getClientRects().length)
        const first = items[0], last = items.at(-1)
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { e.preventDefault(); first?.focus() }
      }
    }}>
      <div className="flex justify-between gap-3"><h4>尝试 {attempt.number} · {statusLabel(attempt.status)}</h4><button type="button" onClick={onClose} aria-label="关闭尝试详情"><X size={16} /></button></div>
      <p className="my-2 text-xs text-slate-500">{attempt.execution_id ? `执行 ${attempt.execution_id.slice(0, 8)}` : '人工节点，无模型执行'} · {attempt.current ? '当前结果' : '历史结果，不作为当前下游输入'}</p>
      {attempt.result && <details className="my-3 text-xs" open><summary>结构化结果与协调记录</summary><pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(attempt.result, null, 2)}</pre></details>}
      {message && <div className="my-3"><MessageParts message={message} isOwner /></div>}
      {attempt.usage && <p className="text-xs">厂商用量：输入 {attempt.usage.input_tokens ?? '未知'} / 输出 {attempt.usage.output_tokens ?? '未知'}</p>}
      {message && <button type="button" className={`${button} my-2`} onClick={() => { w.setOpen(false); window.setTimeout(() => document.querySelector(`[data-reading-anchor="m-${message.id}"]`)?.scrollIntoView({ block: 'center' }), 0) }}>返回对话查看原消息</button>}
      {w.error && <p role="alert" className="text-xs text-red-500">{w.error}</p>}
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
      {facts && <details open className="my-3 text-xs"><summary>服务器核对事实</summary><pre className="mt-2 whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3">{facts}</pre></details>}
      {terminal && (attempt.current || attempt.selected_in_activation) && <div className="space-y-3 border-t border-slate-700 pt-3 text-xs">
        <label className="block">本次修订要求<textarea className={field} value={instruction} onChange={e => setInstruction(e.target.value)} /></label>
        <label className="flex gap-2"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />我已核对本次结果；未知副作用不作为自动重放依据</label>
        <label className="flex gap-2"><input type="checkbox" checked={downstream} onChange={e => setDownstream(e.target.checked)} />本节点结束后重新执行后续步骤（旧结果保留为历史）</label>
        <button type="button" className={button} disabled={!facts || !checked || w.busy} onClick={() => void w.control({ action: 'retry', attempt_id: attempt.id, instruction, acknowledge_facts: checked, rerun_downstream: downstream })}>创建新尝试</button>
      </div>}
    </div>
  </div>
}
