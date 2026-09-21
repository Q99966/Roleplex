import { useEffect, useState } from 'react'
import { useWorkflow } from './WorkflowContext'
import { edgeDescription } from './workflow-projection'

/** 侧栏连线表单与画布共享选择和草稿，不依赖画布是否展开。 */
export function WorkflowLinks() {
  const w = useWorkflow()
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  useEffect(() => { setSource(''); setTarget('') }, [w.draft.definition.id])
  const edges = w.graph.edges.map(([a, b]) => ({ id: JSON.stringify([a, b]),
    label: `${w.graph.nodes.find(n => n.id === a)?.title ?? a} → ${w.graph.nodes.find(n => n.id === b)?.title ?? b}` }))
  if (w.mode !== 'edit') return null
  const field = 'w-full min-w-0 rounded-lg border border-slate-700 bg-panel p-2'
  return <details className="rounded-xl border border-slate-800 p-3">
    <summary className="cursor-pointer text-slate-500">连线工具</summary>
    <div className="mt-3 space-y-2">
      <label className="block">起点<select aria-label="连线起点" value={source} onChange={e => setSource(e.target.value)} className={field}>
        <option value="">选择节点</option>{w.graph.nodes.map(n => <option key={n.id} value={n.id}>{n.title}</option>)}
      </select></label>
      <label className="block">终点<select aria-label="连线终点" value={target} onChange={e => setTarget(e.target.value)} className={field}>
        <option value="">选择节点</option>{w.graph.nodes.map(n => <option key={n.id} value={n.id}>{n.title}</option>)}
      </select></label>
      <button type="button" disabled={!w.graph.nodes.some(n => n.id === source) || !w.graph.nodes.some(n => n.id === target)}
        onClick={() => {
          if (!w.graph.edges.some(([a, b]) => a === source && b === target)) w.changeGraph({ ...w.graph, edges: [...w.graph.edges, [source, target]] })
        }} className="rounded-lg border border-slate-700 px-3 py-2 disabled:opacity-40">连接节点</button>
      <label className="block">连线<select aria-label="选择连线" value={edges.some(e => e.id === w.selectedEdge) ? w.selectedEdge : ''} onChange={e => w.selectEdge(e.target.value)} className={field}>
        <option value="">选择连线</option>{edges.map(edge => <option key={edge.id} value={edge.id}>{edge.label}</option>)}
      </select></label>
      {edges.some(e => e.id === w.selectedEdge) && (() => {
        const [a, b] = JSON.parse(w.selectedEdge) as [string, string]
        const display = w.graph.presentation ?? { groups: [], edge_labels: [] }
        return <><label className="block">分支显示名称<input aria-label="分支显示名称" className={field} maxLength={80}
          placeholder="例如：通过、需要修订" value={display.edge_labels.find(label => label.source === a && label.target === b)?.label ?? ''}
          onChange={e => w.changeGraph({ ...w.graph, presentation: { ...display, edge_labels: [
            ...display.edge_labels.filter(label => label.source !== a || label.target !== b),
            ...(e.target.value.trim() ? [{ source: a, target: b, label: e.target.value }] : []),
          ] } })} /></label><p className="text-slate-500">实际规则：{edgeDescription(w.graph, a, b).condition}。名称不改变规则。</p></>
      })()}
      {w.graph.runtime_version === 2 && edges.some(e => e.id === w.selectedEdge) && <label className="block">此连线何时激活<select aria-label="连线条件" className={field}
        value={w.graph.edge_rules?.find(rule => JSON.stringify([rule.source, rule.target]) === w.selectedEdge)?.when ?? 'always'} onChange={e => {
          const [source, target] = JSON.parse(w.selectedEdge) as [string, string]
          w.changeGraph({ ...w.graph, edge_rules: [...(w.graph.edge_rules ?? []).filter(rule => JSON.stringify([rule.source, rule.target]) !== w.selectedEdge),
            { source, target, when: e.target.value as 'always' | 'true' | 'false' }] })
        }}><option value="always">并行依赖 / 始终</option><option value="true">判断为 true</option><option value="false">判断为 false</option></select></label>}
      <button type="button" disabled={!edges.some(e => e.id === w.selectedEdge)} onClick={() => {
        w.changeGraph({ ...w.graph, edges: w.graph.edges.filter(([a, b]) => JSON.stringify([a, b]) !== w.selectedEdge) }); w.selectEdge('')
      }} className="rounded-lg border border-slate-700 px-3 py-2 disabled:opacity-40">删除连线</button>
    </div>
  </details>
}
