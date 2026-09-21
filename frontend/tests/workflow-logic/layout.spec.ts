import { test, expect } from '@playwright/test'
import { topologicalPositions, displayPositions } from '../../src/components/workflows/workflow-layout'
import type { WorkflowGraph, WorkflowNode } from '../../src/api/workflows'

const node = (id: string, position?: { x: number; y: number }): WorkflowNode => ({ id, title: id, kind: 'role', role_id: 1, task: '受控任务', inputs: [], expected_output: '', position })
const diamond = (): WorkflowGraph => ({ runtime_version: 2, nodes: ['end', 'b', 'start', 'a', 'join'].map(id => node(id)),
  edges: [['start', 'a'], ['start', 'b'], ['a', 'join'], ['b', 'join'], ['join', 'end']] })

test('布局按拓扑分层，乱序数组和重复计算不改变依赖或坐标', () => {
  const graph = diamond(), before = JSON.stringify(graph)
  const positions = topologicalPositions(graph)
  for (const [source, target] of graph.edges) expect(positions[target].x).toBeGreaterThan(positions[source].x)
  expect(positions.a.x).toBe(positions.b.x)
  expect(Math.abs(positions.a.y - positions.b.y)).toBeGreaterThanOrEqual(160)
  expect(topologicalPositions({ ...graph, nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() })).toEqual(positions)
  expect(JSON.stringify(graph)).toBe(before)
})

test('显式回边不拉乱主干，未声明环路与断开节点也能显示', () => {
  const graph = diamond()
  graph.edges.push(['join', 'start'])
  graph.loops = [{ id: 'revise', entry: 'start', decision: 'join', exit: 'end', body: ['start', 'a', 'b', 'join'], repeat_when: false, max_iterations: 3, carry_inputs: [] }]
  let positions = topologicalPositions(graph)
  expect(positions.start.x).toBeLessThan(positions.join.x)
  graph.loops = []
  graph.nodes.push(node('isolated'))
  positions = topologicalPositions(graph)
  expect(Object.keys(positions).sort()).toEqual(graph.nodes.map(n => n.id).sort())
  expect(Object.values(positions).every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true)
  expect(new Set(Object.values(positions).map(p => `${p.x},${p.y}`)).size).toBe(graph.nodes.length)
})

test('已有手动位置保持不变，新增节点放进可用空间', () => {
  const graph = diamond()
  graph.nodes = graph.nodes.map(n => n.id === 'start' ? { ...n, position: { x: 40, y: 190 } } : n.id === 'end' ? { ...n, position: { x: 900, y: -40 } } : n)
  const positions = displayPositions(graph)
  expect(positions.start).toEqual({ x: 40, y: 190 })
  expect(positions.end).toEqual({ x: 900, y: -40 })
  const values = Object.entries(positions)
  for (let i = 0; i < values.length; i++) for (let j = i + 1; j < values.length; j++) {
    const a = values[i][1], b = values[j][1]
    expect(a.x + 220 <= b.x || b.x + 220 <= a.x || a.y + 140 <= b.y || b.y + 140 <= a.y).toBe(true)
  }
})

test('布局按测量尺寸留出长标题与大卡片间距', () => {
  const graph = diamond(), sizes = { a: { width: 280, height: 210 }, b: { width: 280, height: 270 } }
  const positions = topologicalPositions(graph, sizes)
  expect(positions.join.x).toBeGreaterThan(positions.a.x + 280)
  expect(positions.a.y + 210 < positions.b.y || positions.b.y + 270 < positions.a.y).toBe(true)
})

test('循环区域与无关并行分支各占空间，不把独立节点圈进循环', () => {
  const graph: WorkflowGraph = { runtime_version: 2, nodes: ['start', 'work', 'judge', 'outside', 'end'].map(id => node(id)),
    edges: [['start', 'work'], ['start', 'outside'], ['work', 'judge'], ['judge', 'work'], ['judge', 'end'], ['outside', 'end']],
    loops: [{ id: 'check', entry: 'work', decision: 'judge', exit: 'end', body: ['work', 'judge'], repeat_when: false, carry_inputs: [], max_iterations: 2 }] }
  const positions = topologicalPositions(graph)
  const left = Math.min(positions.work.x, positions.judge.x) - 28, right = Math.max(positions.work.x, positions.judge.x) + 224 + 28
  const top = Math.min(positions.work.y, positions.judge.y) - 54, bottom = Math.max(positions.work.y, positions.judge.y) + 144 + 26
  expect(positions.outside.x + 224 <= left || positions.outside.x >= right || positions.outside.y + 144 <= top || positions.outside.y >= bottom).toBe(true)
})
