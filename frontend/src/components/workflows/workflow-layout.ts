/** 旧定义缺少坐标时的兼容布局；只影响显示，不决定执行顺序。 */
export const defaultPosition = (index: number) => ({ x: index * 280, y: 80 })

import type { WorkflowGraph, WorkflowNode } from '../../api/workflows'

export type NodeSize = { width: number; height: number }
export type CanvasPosition = { x: number; y: number }
export const taskSize: NodeSize = { width: 224, height: 144 }
const columnGap = 96, rowGap = 64

/** 仅剔除声明的回边；未声明环路交给布局的强连通分量处理，不替它补执行语义。 */
export function forwardEdges(graph: Pick<WorkflowGraph, 'edges' | 'loops'>): [string, string][] {
  const back = new Set((graph.loops ?? []).map(loop => JSON.stringify([loop.decision, loop.entry])))
  return graph.edges.filter(edge => !back.has(JSON.stringify(edge)))
}

type LayoutGraph = Pick<WorkflowGraph, 'nodes' | 'edges' | 'loops'>

/**
 * 画布布局入口；合法独立循环先作为区域排布，再展开内部拓扑，避免穿插无关分支。
 * @param graph 原节点/依赖与显式循环，不修改任何字段。
 * @param sizes 节点测量宽高，单位为画布像素；缺省使用卡片标准尺寸。
 */
export function topologicalPositions(graph: LayoutGraph, sizes: Record<string, NodeSize> = {}): Record<string, CanvasPosition> {
  const occupied = new Set<string>(), ids = new Set(graph.nodes.map(node => node.id)), edges = forwardEdges(graph)
  const loops = (graph.loops ?? []).filter(loop => {
    const body = new Set(loop.body)
    if (body.size < 2 || !body.has(loop.entry) || !body.has(loop.decision) || body.has(loop.exit) || !ids.has(loop.exit)
      || [...body].some(id => !ids.has(id) || occupied.has(id)) || edges.some(([a, b]) => !body.has(a) && body.has(b) && b !== loop.entry
        || body.has(a) && !body.has(b) && (a !== loop.decision || b !== loop.exit))) return false
    for (const id of body) occupied.add(id)
    return true
  })
  if (!loops.length) return layeredPositions(graph, sizes)
  const mapping: Record<string, string> = Object.fromEntries(graph.nodes.map(node => [node.id, node.id]))
  const expanded = loops.map(loop => {
    const id = '@layout-loop:' + loop.id, members = graph.nodes.filter(node => loop.body.includes(node.id))
    const positions = layeredPositions({ nodes: members, edges: edges.filter(([a, b]) => loop.body.includes(a) && loop.body.includes(b)), loops: [] }, sizes)
    const left = Math.min(...Object.values(positions).map(p => p.x)), top = Math.min(...Object.values(positions).map(p => p.y))
    const right = Math.max(...members.map(node => positions[node.id].x + (sizes[node.id] ?? taskSize).width))
    const bottom = Math.max(...members.map(node => positions[node.id].y + (sizes[node.id] ?? taskSize).height))
    for (const node of members) mapping[node.id] = id
    return { id, members, positions, left, top, size: { width: right - left + 56, height: bottom - top + 80 } }
  })
  const outer: LayoutGraph = { nodes: [...graph.nodes.filter(node => !occupied.has(node.id)), ...expanded.map(group => ({ ...group.members[0], id: group.id }))],
    edges: edges.map(([a, b]): [string, string] => [mapping[a], mapping[b]]).filter(([a, b]) => a !== b), loops: [] }
  const positions = layeredPositions(outer, { ...sizes, ...Object.fromEntries(expanded.map(group => [group.id, group.size])) })
  const result: Record<string, CanvasPosition> = {}
  for (const node of graph.nodes.filter(node => !occupied.has(node.id))) result[node.id] = positions[node.id]
  for (const group of expanded) for (const node of group.members) result[node.id] = {
    x: positions[group.id].x + group.positions[node.id].x - group.left + 28,
    y: positions[group.id].y + group.positions[node.id].y - group.top + 54,
  }
  return result
}

function layeredPositions(graph: LayoutGraph, sizes: Record<string, NodeSize>): Record<string, CanvasPosition> {
  const ids = graph.nodes.map(node => node.id).sort(), known = new Set(ids)
  const edges = forwardEdges(graph).filter(([a, b]) => known.has(a) && known.has(b))
  const outgoing = new Map(ids.map(id => [id, [] as string[]])), incoming = new Map(ids.map(id => [id, [] as string[]]))
  for (const [a, b] of edges) { outgoing.get(a)!.push(b); incoming.get(b)!.push(a) }
  for (const values of [...outgoing.values(), ...incoming.values()]) values.sort()
  // 非递归强连通分量：未配置完成的草稿也能布局，不让一个坏环卡死整个画布。
  const visited = new Set<string>(), finished: string[] = []
  for (const id of ids) {
    const stack: [string, boolean][] = [[id, false]]
    while (stack.length) {
      const [current, done] = stack.pop()!
      if (done) { finished.push(current); continue }
      if (visited.has(current)) continue
      visited.add(current); stack.push([current, true])
      for (const next of [...outgoing.get(current)!].reverse()) if (!visited.has(next)) stack.push([next, false])
    }
  }
  const components: string[][] = [], component = new Map<string, number>()
  for (const id of finished.reverse()) {
    if (component.has(id)) continue
    const members: string[] = [], stack = [id], index = components.length
    while (stack.length) {
      const current = stack.pop()!
      if (component.has(current)) continue
      component.set(current, index); members.push(current)
      stack.push(...incoming.get(current)!.filter(next => !component.has(next)))
    }
    components.push(members.sort())
  }
  const next = components.map(() => new Set<number>()), degree = components.map(() => 0), ranks = components.map(() => 0)
  for (const [a, b] of edges) {
    const from = component.get(a)!, to = component.get(b)!
    if (from !== to && !next[from].has(to)) { next[from].add(to); degree[to]++ }
  }
  const queue = degree.map((value, i) => value === 0 ? i : -1).filter(i => i >= 0)
  while (queue.length) {
    queue.sort((a, b) => components[a][0].localeCompare(components[b][0]))
    const current = queue.shift()!
    for (const target of next[current]) { ranks[target] = Math.max(ranks[target], ranks[current] + 1); if (--degree[target] === 0) queue.push(target) }
  }
  const layers: string[][] = []
  for (const id of ids) (layers[ranks[component.get(id)!]] ??= []).push(id)
  // 重心排序减少分支交叉；稳定 ID 作为相同重心时的次序，数组顺序不冒充业务顺序。
  const orders = new Map(ids.map(id => [id, 0]))
  for (const layer of layers) layer.forEach((id, i) => orders.set(id, i))
  for (let pass = 0; pass < 4; pass++) {
    const traversal = pass % 2 ? [...layers].reverse() : layers
    const adjacent = pass % 2 ? outgoing : incoming
    const center = (id: string) => {
      const peers = adjacent.get(id)!.filter(peer => ranks[component.get(peer)!] !== ranks[component.get(id)!])
      return peers.length ? peers.reduce((sum, peer) => sum + orders.get(peer)!, 0) / peers.length : orders.get(id)!
    }
    for (const layer of traversal) {
      layer.sort((a, b) => center(a) - center(b) || a.localeCompare(b))
      layer.forEach((id, i) => orders.set(id, i))
    }
  }
  const size = (id: string) => sizes[id] ?? taskSize
  const heights = layers.map(layer => layer.reduce((sum, id) => sum + size(id).height, 0) + Math.max(0, layer.length - 1) * rowGap)
  const maxHeight = Math.max(0, ...heights), positions: Record<string, CanvasPosition> = {}
  let x = 56
  layers.forEach((layer, rank) => {
    let y = 96 + (maxHeight - heights[rank]) / 2
    for (const id of layer) { positions[id] = { x, y }; y += size(id).height + rowGap }
    x += Math.max(...layer.map(id => size(id).width)) + columnGap
  })
  return positions
}

/** 保留已保存的手动坐标，未定位节点采用画布布局。 */
export function displayPositions(graph: WorkflowGraph, sizes: Record<string, NodeSize> = {}): Record<string, CanvasPosition> {
  const positions = topologicalPositions(graph, sizes)
  if (!graph.nodes.some(node => node.position)) return positions
  const result: Record<string, CanvasPosition> = Object.fromEntries(graph.nodes.filter(node => node.position).map(node => [node.id, { ...node.position! }]))
  const size = (id: string) => sizes[id] ?? taskSize
  const edges = forwardEdges(graph)
  const overlaps = (id: string, p: CanvasPosition) => Object.entries(result).some(([other, q]) =>
    p.x < q.x + size(other).width + 28 && p.x + size(id).width + 28 > q.x && p.y < q.y + size(other).height + 28 && p.y + size(id).height + 28 > q.y)
  for (const node of [...graph.nodes].sort((a, b) => positions[a.id].x - positions[b.id].x || positions[a.id].y - positions[b.id].y)) {
    if (result[node.id]) continue
    const parents = edges.filter(([, target]) => target === node.id).map(([source]) => source).filter(id => result[id])
    const candidate = parents.length ? { x: Math.max(...parents.map(id => result[id].x + size(id).width)) + columnGap,
      y: parents.reduce((sum, id) => sum + result[id].y, 0) / parents.length } : { ...positions[node.id] }
    while (overlaps(node.id, candidate)) candidate.y += size(node.id).height + rowGap
    result[node.id] = candidate
  }
  return result
}

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
