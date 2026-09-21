import type { WorkflowGraph, WorkflowNode, WorkflowRun } from '../../api/workflows'
import { forwardEdges } from './workflow-layout'

export type ViewGroup = { id: string; title: string; kind: 'phase' | 'parallel' | 'loop'; nodeIds: string[]; loopId?: string; customId?: string }
export type ProjectedNode = { id: string; node?: WorkflowNode; group?: ViewGroup; memberIds: string[] }
export type ProjectedEdge = { id: string; source: string; target: string; originals: [string, string][] }
export type GraphProjection = { nodes: ProjectedNode[]; edges: ProjectedEdge[]; visibleId: Record<string, string> }

/** 折叠只生成前端投影，不写回业务图。 */
export function projectGraph(graph: WorkflowGraph, groups: ViewGroup[], collapsed: Set<string>): GraphProjection {
  const visibleId = Object.fromEntries(graph.nodes.map(node => [node.id, node.id]))
  const groupMap = new Map(groups.map(group => [group.id, group]))
  // 先覆盖小组再覆盖父阶段，外层折叠时所有成员只投影一次。
  for (const group of [...groups].sort((a, b) => a.nodeIds.length - b.nodeIds.length)) {
    if (collapsed.has(group.id)) for (const id of group.nodeIds) visibleId[id] = group.id
  }
  const nodes: ProjectedNode[] = [], seen = new Set<string>()
  for (const node of graph.nodes) {
    const id = visibleId[node.id]
    if (seen.has(id)) continue
    seen.add(id)
    const group = groupMap.get(id)
    nodes.push(group ? { id, group, memberIds: group.nodeIds } : { id, node, memberIds: [id] })
  }
  const edges = new Map<string, ProjectedEdge>()
  for (const [a, b] of graph.edges) {
    const source = visibleId[a], target = visibleId[b]
    if (!source || !target || (source === target && groupMap.has(source))) continue
    const id = JSON.stringify([source, target])
    const edge = edges.get(id) ?? { id, source, target, originals: [] }
    edge.originals.push([a, b]); edges.set(id, edge)
  }
  return { nodes, edges: [...edges.values()], visibleId }
}

/** 分组只从已声明循环、展示阶段与可确定的并行汇合区域得到。 */
export function viewGroups(graph: WorkflowGraph): { groups: ViewGroup[]; notices: string[] } {
  const groups: ViewGroup[] = [], notices: string[] = [], ids = new Set(graph.nodes.map(node => node.id))
  const edges = forwardEdges(graph)
  const outgoing = new Map([...ids].map(id => [id, edges.filter(([a]) => a === id).map(([, b]) => b)]))
  const reachable = (start: string, stop?: string) => {
    const found = new Set<string>(), stack = [start]
    while (stack.length) {
      const id = stack.pop()!
      if (id === stop || found.has(id) || !ids.has(id)) continue
      found.add(id); stack.push(...outgoing.get(id)!)
    }
    return found
  }
  const same = (a: string[], b: string[]) => a.length === b.length && a.every(id => b.includes(id))
  const convex = (members: Set<string>) => {
    const exits = edges.filter(([a, b]) => members.has(a) && !members.has(b)).map(([, b]) => b)
    return exits.every(exit => ![...reachable(exit)].some(id => members.has(id)))
  }
  const add = (group: ViewGroup, explain = false) => {
    group.nodeIds = [...new Set(group.nodeIds)].filter(id => ids.has(id))
    if (!group.nodeIds.length || groups.some(existing => same(existing.nodeIds, group.nodeIds))) return
    const overlap = groups.some(existing => {
      const common = existing.nodeIds.filter(id => group.nodeIds.includes(id)).length
      return common > 0 && common !== existing.nodeIds.length && common !== group.nodeIds.length
    })
    if (overlap || !convex(new Set(group.nodeIds))) {
      if (explain) notices.push(`“${group.title}”包含交错依赖，保留节点展开显示。`)
      return
    }
    groups.push(group)
  }
  for (const loop of graph.loops ?? []) {
    const custom = graph.presentation?.groups.find(group => same(group.node_ids, loop.body))
    const decision = graph.nodes.find(node => node.id === loop.decision)
    add({ id: '@loop:' + loop.id, title: custom?.title ?? `${decision?.title ?? loop.id} · 循环`, kind: 'loop',
      nodeIds: [...loop.body], loopId: loop.id, customId: custom?.id })
  }
  for (const group of graph.presentation?.groups ?? []) add({ id: '@phase:' + group.id, title: group.title, kind: 'phase', nodeIds: [...group.node_ids], customId: group.id }, true)
  for (const node of graph.nodes) {
    const branches = [...new Set(outgoing.get(node.id))]
    if (branches.length < 2 || ['condition', 'judge'].includes(node.kind)) continue
    const reach = branches.map(branch => reachable(branch))
    const common = [...reach[0]].filter(id => reach.every(set => set.has(id)) && id !== node.id)
    // 最近共同汇合在其他共同后继之前，避免把交付阶段一起吞入并行组。
    const merge = common.find(id => !common.some(other => other !== id && reachable(other).has(id)))
    if (!merge) continue
    const members = [...new Set(branches.flatMap(branch => [...reachable(branch, merge)]))]
    if (members.length < 2 || members.includes(node.id)) continue
    add({ id: '@parallel:' + node.id, kind: 'parallel', title: '并行任务', nodeIds: members })
  }
  return { groups, notices }
}

/** 折叠后的异常仍可见，不能把节点执行结束显示成业务验收通过。 */
export function groupSummary(members: string[], run: WorkflowRun | null) {
  const result = { total: members.length, completed: 0, active: 0, waiting: 0, failed: 0, stopped: 0, feedback: 0 }
  if (!run) return result
  for (const id of members) {
    const attempt = run.attempts.find(attempt => attempt.node_id === id && attempt.current)
    const activation = run.activations?.find(activation => activation.node_id === id && (activation.current ?? !activation.loop_id))
    const status = activation?.status === 'waiting_feedback' ? activation.status : attempt?.status ?? activation?.status
    if (status === 'completed') result.completed++
    else if (['active', 'queued', 'running'].includes(status ?? '')) result.active++
    else if (['waiting', 'waiting_feedback'].includes(status ?? '')) result.waiting++
    else if (['failed', 'blocked', 'interrupted'].includes(status ?? '')) result.failed++
    else if (status === 'stopped') result.stopped++
  }
  result.feedback = (run.feedback ?? []).filter(item => item.source_current && !['resolved', 'accepted', 'dismissed', 'obsolete'].includes(item.status)
    && (members.includes(item.node_id) || item.handler_node_ids.some(id => members.includes(id)))).length
  return result
}

/** 文案不参与求值；未命名分支明确使用条件成立/不成立。 */
export function edgeDescription(graph: WorkflowGraph, source: string, target: string) {
  const loop = graph.loops?.find(loop => loop.decision === source && [loop.entry, loop.exit].includes(target))
  const when = loop ? (target === loop.entry ? loop.repeat_when : !loop.repeat_when) ? 'true' : 'false'
    : graph.edge_rules?.find(rule => rule.source === source && rule.target === target)?.when ?? 'always'
  const custom = graph.presentation?.edge_labels.find(label => label.source === source && label.target === target)?.label
  const label = custom || (when === 'true' ? '条件成立' : when === 'false' ? '条件不成立' : source === target ? '自环' : '')
  const condition = graph.nodes.find(node => node.id === source)?.condition
  const title = (id: string) => id === '$self' ? '本节点' : graph.nodes.find(node => node.id === id)?.title ?? id
  const expression = condition ? `${condition.aggregate === 'any' ? '任一' : '全部'} ${condition.sources.map(title).join('、')} · ${condition.key} ${condition.operator === 'ne' ? '≠' : '='} ${JSON.stringify(condition.value)}` : '前置节点完成'
  return { label, condition: `${expression}${when === 'always' ? '' : when === 'true' ? '，条件成立时进入' : '，条件不成立时进入'}${loop && target === loop.entry ? '；返回下一轮' : ''}` }
}

/** 仅保留仍有真实对象的展示引用；旧草稿可缺省整个 presentation。 */
export function cleanPresentation(graph: WorkflowGraph) {
  if (!graph.presentation) return graph.presentation
  const ids = new Set(graph.nodes.map(node => node.id)), edges = new Set(graph.edges.map(edge => JSON.stringify(edge)))
  return { groups: graph.presentation.groups.map(group => ({ ...group, node_ids: group.node_ids.filter(id => ids.has(id)) })).filter(group => group.node_ids.length),
    edge_labels: graph.presentation.edge_labels.filter(label => edges.has(JSON.stringify([label.source, label.target]))) }
}

/** 节点聚焦只沿真实上/下游路径，回边不把所有轮次混成一个可达集合。 */
export function relatedNodes(graph: WorkflowGraph, seeds: string[]): Set<string> {
  const edges = forwardEdges(graph), found = new Set(seeds)
  for (const reverse of [false, true]) {
    const seen = new Set(seeds), queue = [...seeds]
    while (queue.length) {
      const id = queue.pop()!
      for (const [source, target] of edges) {
        const from = reverse ? target : source, to = reverse ? source : target
        if (from === id && !seen.has(to)) { seen.add(to); found.add(to); queue.push(to) }
      }
    }
  }
  return found
}
