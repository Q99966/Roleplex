import { useState } from 'react'
import { useWorkflow } from './WorkflowContext'

/** 展示阶段独立保存，不用删除真实节点或新增业务循环来整理画布。 */
export function WorkflowPresentationEditor() {
  const w = useWorkflow()
  const [id, setId] = useState(''), [title, setTitle] = useState(''), [members, setMembers] = useState<string[]>([])
  if (w.mode !== 'edit') return null
  const value = w.graph.presentation ?? { groups: [], edge_labels: [] }
  const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2'
  return <details className="rounded-xl border border-slate-800 p-3 text-xs">
    <summary className="cursor-pointer text-slate-500">展示阶段</summary>
    <p className="mt-2 text-slate-500">只决定总览如何分组，执行顺序和循环规则仍由原图决定。</p>
    <div className="my-3 space-y-2">{value.groups.map(group => <div key={group.id} className="rounded-lg border border-slate-700 p-2">
      <span>{group.title} · {group.node_ids.length} 个节点</span>
      <div className="mt-2 flex gap-3"><button className="text-indigo-500" onClick={() => { setId(group.id); setTitle(group.title); setMembers(group.node_ids) }}>编辑阶段：{group.title}</button>
        <button className="text-slate-500" onClick={() => { w.changeGraph({ ...w.graph, presentation: { ...value, groups: value.groups.filter(row => row.id !== group.id) } }); if (id === group.id) { setId(''); setTitle(''); setMembers([]) } }}>移除分组</button></div>
    </div>)}</div>
    <label className="block">阶段名称<input aria-label="阶段名称" maxLength={80} value={title} onChange={e => setTitle(e.target.value)} className={field} /></label>
    <fieldset className="my-3 max-h-52 overflow-y-auto"><legend>包含哪些节点</legend>{w.graph.nodes.map(node => <label key={node.id} className="mt-2 flex gap-2">
      <input type="checkbox" checked={members.includes(node.id)} onChange={e => setMembers(e.target.checked ? [...members, node.id] : members.filter(id => id !== node.id))} />{node.title}
    </label>)}</fieldset>
    <button className="rounded-lg border border-slate-700 px-3 py-2 disabled:opacity-40" disabled={!title.trim() || !members.length} onClick={() => {
      const group = { id: id || crypto.randomUUID(), title: title.trim(), node_ids: members.filter(id => w.graph.nodes.some(node => node.id === id)) }
      w.changeGraph({ ...w.graph, presentation: { ...value, groups: [...value.groups.filter(row => row.id !== group.id), group] } })
      setId(''); setTitle(''); setMembers([])
    }}>{id ? '更新阶段' : '添加展示阶段'}</button>
  </details>
}
