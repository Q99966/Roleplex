import type { Node } from '@xyflow/react'
import type { WorkflowGraph } from '../../api/workflows'
import type { WorkflowEdgeData } from './WorkflowEdge'

/** 外侧走廊只影响绘图；循环身份仍来自声明，不能用节点坐标推断执行条件。 */
export function returnRoutes(graph: WorkflowGraph, nodes: Node[]): Map<string, WorkflowEdgeData> {
  const boxes = nodes.map(node => ({ id: node.id, x: node.position.x, y: node.position.y,
    right: node.position.x + (node.measured?.width ?? 204), bottom: node.position.y + (node.measured?.height ?? 112) }))
  const byId = new Map(boxes.map(box => [box.id, box]))
  const routes = new Map<string, WorkflowEdgeData>()
  const returns = graph.edges.filter(([s, t]) => {
    const a = byId.get(s), b = byId.get(t)
    return a && b && (s === t || b.x <= a.x || graph.loops?.some(loop => loop.decision === s && loop.entry === t))
  }).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
  returns.forEach(([source, target], index) => {
    const loop = graph.loops?.find(loop => loop.decision === source && loop.entry === target)
    const body = new Set(loop?.body ?? [source, target])
    body.add(source); body.add(target)
    const bounds = boxes.filter(box => body.has(box.id))
    const margin = 48 + Math.floor(index / 2) * 28
    const left = Math.min(...bounds.map(box => box.x)) - margin
    const right = Math.max(...bounds.map(box => box.right)) + margin
    // 把走廊横跨范围内的其他节点也算作障碍，途经点不会落在它们内部。
    const obstacles = boxes.filter(box => box.right + 18 >= left && box.x - 18 <= right)
    const y = index % 2 === 0 ? Math.min(...obstacles.map(box => box.y)) - margin : Math.max(...obstacles.map(box => box.bottom)) + margin
    routes.set(JSON.stringify([source, target]), { returnEdge: true, waypoints: [{ x: right, y }, { x: left, y }] })
  })
  return routes
}
