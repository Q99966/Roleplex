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
export const nodeColor = (node: WorkflowNode) => node.color ?? (node.kind === 'approval' ? '#b58b45' : '#4385a0')
