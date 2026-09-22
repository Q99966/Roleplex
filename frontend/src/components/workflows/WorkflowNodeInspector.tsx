import { useEffect, useRef } from 'react'
import { ArrowDown, ArrowUp, Check, Trash2 } from 'lucide-react'
import { useAppStore } from '../../store/app'
import type { WorkflowNode } from '../../api/workflows'
import { NodeRuntimeSettings } from './NodeRuntimeSettings'
import { nodeColor, upstreamNodes } from './workflow-layout'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { FeedbackReportForm, WorkflowFeedbackPanel } from './WorkflowFeedback'

const button = 'rounded-lg border border-slate-700 bg-panel px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'

/** 唯一上下文面板内的节点编辑与结果；任务内容直接写入当前准确目标的草稿。 */
export function WorkflowNodeInspector() {
  const w = useWorkflow()
  const roles = useAppStore(state => state.roles).filter(role => w.conversation.role_ids.includes(role.id) && role.active)
  const { graph, selected, selectNode: setSelected } = w
  const setDetail = w.selectAttempt
  const node = graph.nodes.find(n => n.id === selected)
  const priorRoles = useRef<Record<string, number>>({})
  useEffect(() => { priorRoles.current = {} }, [w.draft.definition.id])

  const serial = graph.edges.length === graph.nodes.length - 1 && graph.nodes.slice(1).every((n, i) => graph.edges.some(([a, b]) => a === graph.nodes[i].id && b === n.id))
  function commit(nodes: WorkflowNode[], edges = graph.edges.filter(([a, b]) => nodes.some(n => n.id === a) && nodes.some(n => n.id === b))) {
    w.changeGraph({ ...graph, nodes, edges })
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
  if (!node) return null
  return <section aria-label="节点属性与结果"><fieldset disabled={w.busy}>

        <h4 className="mb-3 text-sm font-semibold">{node.title}</h4>
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
          <details><summary className="cursor-pointer text-slate-500">工具与结果规则</summary><div className="mt-3"><NodeRuntimeSettings node={node} patch={patch} /></div></details>
          <p className="text-slate-500">拖动只调整位置，执行顺序仍由连线决定。</p>
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

      {!w.historyGraph && w.mode === 'run' && node && (() => {
        const source = w.run?.attempts.find(a => a.node_id === node.id && a.current)
        return source && w.run?.runtime_version === 2 ? <FeedbackReportForm key={source.id} attempt={source} /> : null
      })()}
      {w.mode === 'run' && <WorkflowFeedbackPanel nodeId={node.id} compact />}
      {!w.historyGraph && <button type="button" className={`${button} mt-3`} disabled={w.busy || !w.conversation.orchestrator_role_id} onClick={() => w.requestCoordination(node.id)}>请协调者调整此节点</button>}

  </fieldset></section>
}
