import type { WorkflowNode, WorkflowCondition, WorkflowScalar } from '../../api/workflows'
import { useWorkflow } from './WorkflowContext'
import { upstreamNodes } from './workflow-layout'

const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2'

/** 工具需求是本任务清单，不写角色全局配置；条件只比较结构化标量。 */
export function NodeRuntimeSettings({ node, patch }: { node: WorkflowNode; patch: (update: Partial<WorkflowNode>) => void }) {
  const w = useWorkflow()
  const capable = w.data.member_capabilities ?? []
  const available = node.kind === 'judge' ? [] : node.role_id ? capable.find(r => r.role_id === node.role_id)?.tools ?? [] : [...new Set(capable.flatMap(r => r.tools))]
  const condition: WorkflowCondition = node.condition ?? { sources: node.kind === 'judge' ? ['$self'] : [], key: 'approved', operator: 'eq', value: true, aggregate: 'all' }
  const resultKeys = [...new Set([...(node.result_keys ?? []), ...Object.keys(node.result_schema ?? {})])]
  const change = (value: Partial<WorkflowCondition>) => patch({ condition: { ...condition, ...value } })
  if (w.graph.runtime_version !== 2) return null
  return <div className="space-y-3">
    {['role', 'judge'].includes(node.kind) && <>
      <fieldset className="rounded-lg border border-slate-700 p-2"><legend>本任务工具分配</legend>
        <label className="flex gap-2"><input type="checkbox" checked={node.tools == null} onChange={e => patch({ tools: e.target.checked ? null : [] })} />{node.role_id ? '按角色当前授权初始化' : '交由协调者申请工具'}</label>
        {node.tools != null && [...new Set([...available, ...node.tools])].map(name => <label className="mt-1 flex break-all gap-2" key={name}>
          <input type="checkbox" checked={node.tools!.includes(name)} onChange={e => patch({ tools: e.target.checked ? [...node.tools!, name] : node.tools!.filter(n => n !== name) })} />{name}{available.includes(name) ? '' : '（当前不可分配）'}
        </label>)}
        <p className="mt-1 text-slate-500">勾选的是本次任务需要的工具，不会打开角色全局开关。</p>
      </fieldset>
      <label className="block">必需的结构化结果字段<input aria-label="结构化结果字段" className={field} value={resultKeys.join(',')}
        onChange={e => { const keys = e.target.value.split(',').map(s => s.trim()).filter(Boolean); patch({ result_keys: keys, result_schema: Object.fromEntries(Object.entries(node.result_schema ?? {}).filter(([key]) => keys.includes(key))) }) }} placeholder="例如 approved,notes" /></label>
      {resultKeys.map(key => <label key={key} className="block">{key} 的结果类型<select aria-label={`结果类型 ${key}`} className={field} value={node.result_schema?.[key] ?? ''} onChange={e => {
        const schema = { ...node.result_schema }; if (e.target.value) schema[key] = e.target.value as NonNullable<WorkflowNode['result_schema']>[string]; else delete schema[key]; patch({ result_schema: schema })
      }}><option value="">由条件推导 / 通用标量</option><option value="boolean">布尔值</option><option value="integer">整数</option><option value="number">数字</option><option value="string">文本</option><option value="null">空值</option></select></label>)}
    </>}
    {['condition', 'judge'].includes(node.kind) && <fieldset className="space-y-2 rounded-lg border border-slate-700 p-2">
      <legend>结构化条件</legend>
      <fieldset><legend>结果来源</legend>
        {node.kind === 'judge' && <label className="flex gap-2"><input type="checkbox" checked={condition.sources.includes('$self')} onChange={e => change({ sources: e.target.checked ? [...condition.sources, '$self'] : condition.sources.filter(id => id !== '$self') })} />本节点模型判断</label>}
        {upstreamNodes(w.graph, node.id).filter(n => ['role', 'judge', 'approval'].includes(n.kind)).map(n => <label className="mt-1 flex gap-2" key={n.id}>
          <input type="checkbox" checked={condition.sources.includes(n.id)} onChange={e => change({ sources: e.target.checked ? [...condition.sources, n.id] : condition.sources.filter(id => id !== n.id) })} />{n.title}
        </label>)}
      </fieldset>
      <label className="block">结果字段<input aria-label="条件结果字段" className={field} value={condition.key} onChange={e => change({ key: e.target.value })} /></label>
      <label className="block">比较<select className={field} value={condition.operator} onChange={e => change({ operator: e.target.value as 'eq' | 'ne' })}><option value="eq">等于</option><option value="ne">不等于</option></select></label>
      <label className="block">值类型<select className={field} value={condition.value === null ? 'null' : typeof condition.value} onChange={e => change({ value: ({ boolean: true, string: '', number: 0, null: null } as Record<string, WorkflowScalar>)[e.target.value] })}>
        <option value="boolean">布尔值</option><option value="string">文本</option><option value="number">数字</option><option value="null">空值</option>
      </select></label>
      {typeof condition.value === 'boolean' ? <select aria-label="条件比较值" className={field} value={String(condition.value)} onChange={e => change({ value: e.target.value === 'true' })}><option value="true">true</option><option value="false">false</option></select>
        : condition.value !== null && <input aria-label="条件比较值" type={typeof condition.value === 'number' ? 'number' : 'text'} className={field} value={String(condition.value)} onChange={e => change({ value: typeof condition.value === 'number' ? Number(e.target.value) : e.target.value })} />}
      <label className="block">多个来源<select className={field} value={condition.aggregate} onChange={e => change({ aggregate: e.target.value as 'all' | 'any' })}><option value="all">全部满足</option><option value="any">任一满足</option></select></label>
      <p className="text-slate-500">选中判断节点的出边设置条件；循环回边和出口在高级设置中声明。</p>
    </fieldset>}
  </div>
}
