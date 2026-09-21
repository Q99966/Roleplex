/** 旧定义缺少坐标时的兼容布局；只影响显示，不决定执行顺序。 */
export const defaultPosition = (index: number) => ({ x: index * 280, y: 80 })

import type { WorkflowGraph, WorkflowNode } from '../../api/workflows'

/** 完整串行路径的展示顺序；分支、环路和断路返回 null，不猜测执行语义。 */
export function serialOrder(graph: WorkflowGraph): WorkflowNode[] | null {
  if (!graph.nodes.length || graph.edges.length !== graph.nodes.length - 1) return null
  const nodes = new Map(graph.nodes.map(node => [node.id, node]))
  const next = new Map<string, string>()
  const incoming = new Set<string>()
  for (const [source, target] of graph.edges) {
    if (!nodes.has(source) || !nodes.has(target) || next.has(source) || incoming.has(target)) return null
    next.set(source, target); incoming.add(target)
  }
  const roots = graph.nodes.filter(node => !incoming.has(node.id))
  if (roots.length !== 1) return null
  const ordered: WorkflowNode[] = [], seen = new Set<string>()
  let cursor: string | undefined = roots[0].id
  while (cursor && !seen.has(cursor)) {
    ordered.push(nodes.get(cursor)!); seen.add(cursor); cursor = next.get(cursor)
  }
  return ordered.length === graph.nodes.length ? ordered : null
}

/** 颜色仅用于节点辨识；未设置时按类型配色，任务状态仍使用文字表达。 */
export const nodeColor = (node: WorkflowNode) => node.color ?? (({ role: '#4385a0', approval: '#b58b45', join: '#558f87', condition: '#b17b4a', judge: '#8b70b2' })[node.kind])


/** 条件与节点输入仅列出图上的前驱；已声明回边不作为本轮上游。 */
export function upstreamNodes(graph: WorkflowGraph, nodeId: string): WorkflowNode[] {
  const back = new Set((graph.loops ?? []).map(loop => JSON.stringify([loop.decision, loop.entry])))
  const found = new Set<string>(), pending = [nodeId]
  while (pending.length) {
    const target = pending.pop()!
    for (const [a, b] of graph.edges) {
      if (b === target && a !== nodeId && !found.has(a) && !back.has(JSON.stringify([a, b]))) { found.add(a); pending.push(a) }
    }
  }
  return graph.nodes.filter(node => found.has(node.id))
}
