import { useWorkflow } from './WorkflowContext'
import type { WorkflowLoop } from '../../api/workflows'

const field = 'mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-panel p-2'

/** 明确配置入口、并发和循环域；不从画布上的环自动猜测运行条件。 */
export function WorkflowSettings() {
  const w = useWorkflow()
  if (w.mode !== 'edit') return null
  const graph = w.graph
  const loops = graph.loops ?? []
  function changeLoop(id: string, change: Partial<WorkflowLoop>) {
    w.changeGraph({ ...graph, loops: loops.map(loop => loop.id === id ? { ...loop, ...change } : loop) })
  }
  return <details className="rounded-xl border border-slate-800 p-3 text-xs">
    <summary className="cursor-pointer text-slate-500">并行与循环设置</summary>
    <div className="mt-3 space-y-3">
      <label className="block">执行内核<select aria-label="执行内核" className={field} value={graph.runtime_version ?? 1}
        onChange={e => w.changeGraph({ ...graph, runtime_version: Number(e.target.value) as 1 | 2 })}>
        <option value={1}>旧版串行兼容</option><option value={2}>并行、汇合与条件循环</option>
      </select></label>
      {graph.runtime_version === 2 && <>
        <label className="block">本运行并发容量<input aria-label="工作流并发容量" type="number" min={1} max={w.data.parallel_capacity ?? 4} className={field}
          value={graph.concurrency ?? ''} placeholder={`主机容量 ${w.data.parallel_capacity ?? 4}`} onChange={e => w.changeGraph({ ...graph, concurrency: e.target.value ? Number(e.target.value) : null })} /></label>
        <p className="text-slate-500">模型执行与文件占用分开：读取可共享，修改操作互斥。</p>
        <fieldset><legend>显式入口（单入口图可留空）</legend>{graph.nodes.map(n => <label key={n.id} className="mt-1 flex gap-2">
          <input type="checkbox" checked={graph.entries?.includes(n.id) ?? false} onChange={e => w.changeGraph({ ...graph, entries: e.target.checked ? [...(graph.entries ?? []), n.id] : graph.entries?.filter(id => id !== n.id) })} />{n.title}
        </label>)}</fieldset>
        <button type="button" className="rounded border border-slate-700 px-2 py-1" onClick={() => w.changeGraph({ ...graph, loops: [...loops, {
          id: crypto.randomUUID(), entry: '', decision: '', exit: '', body: [], carry_inputs: [], repeat_when: false, max_iterations: null,
        }] })}>添加循环声明</button>
        {loops.map((loop, index) => <fieldset key={loop.id} className="space-y-2 rounded-lg border border-slate-700 p-2" aria-label={`循环 ${index + 1}`}>
          <legend>循环 {index + 1}</legend>
          {(['entry', 'decision', 'exit'] as const).map(key => <label key={key} className="block">{({ entry: '循环入口', decision: '循环判断节点', exit: '循环出口' })[key]}
            <select aria-label={`${({ entry: '循环入口', decision: '循环判断节点', exit: '循环出口' })[key]} ${index + 1}`} className={field} value={loop[key]} onChange={e => changeLoop(loop.id, { [key]: e.target.value })}>
              <option value="">请选择</option>{graph.nodes.filter(n => key !== 'decision' || ['condition', 'judge'].includes(n.kind)).map(n => <option key={n.id} value={n.id}>{n.title}</option>)}
            </select></label>)}
          <label className="block">何时重复<select className={field} value={String(loop.repeat_when)} onChange={e => changeLoop(loop.id, { repeat_when: e.target.value === 'true' })}>
            <option value="false">判断为 false 时返工</option><option value="true">判断为 true 时重复</option>
          </select></label>
          <label className="block">次数上限（可选）<input aria-label={`循环次数上限 ${index + 1}`} type="number" min={1} className={field} value={loop.max_iterations ?? ''}
            onChange={e => changeLoop(loop.id, { max_iterations: e.target.value ? Number(e.target.value) : null })} /></label>
          <fieldset><legend>循环体（包含入口与判断）</legend>{graph.nodes.map(n => <label key={n.id} className="mt-1 flex gap-2">
            <input type="checkbox" checked={loop.body.includes(n.id)} onChange={e => changeLoop(loop.id, { body: e.target.checked ? [...loop.body, n.id] : loop.body.filter(id => id !== n.id) })} />{n.title}
          </label>)}</fieldset>
          <fieldset><legend>入口携带上一轮哪些结果</legend>{graph.nodes.filter(n => loop.body.includes(n.id)).map(n => <label key={n.id} className="mt-1 flex gap-2">
            <input type="checkbox" checked={loop.carry_inputs.includes(n.id)} onChange={e => changeLoop(loop.id, { carry_inputs: e.target.checked ? [...loop.carry_inputs, n.id] : loop.carry_inputs.filter(id => id !== n.id) })} />{n.title}
          </label>)}</fieldset>
          <button type="button" disabled={!loop.entry || !loop.decision || !loop.exit} onClick={() => {
            const pairs: [string, string][] = [[loop.decision, loop.entry], [loop.decision, loop.exit]]
            w.changeGraph({ ...graph, edges: [...graph.edges, ...pairs.filter(([a, b]) => !graph.edges.some(([x, y]) => x === a && y === b))] })
          }} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">连接回边与出口</button>
          <button type="button" className="ml-2 text-red-500" onClick={() => w.changeGraph({ ...graph, loops: loops.filter(row => row.id !== loop.id) })}>删除循环声明</button>
        </fieldset>)}
      </>}
    </div>
  </details>
}
