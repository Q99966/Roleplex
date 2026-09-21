import { test, expect } from '@playwright/test'
import type { WorkflowGraph, WorkflowRun } from '../../src/api/workflows'
import { projectGraph, viewGroups, groupSummary, edgeDescription } from '../../src/components/workflows/workflow-projection'
import { topologicalPositions } from '../../src/components/workflows/workflow-layout'

function sample(): WorkflowGraph {
  return { runtime_version: 2, nodes: ['design', 'core', 'ui', 'join', 'work', 'judge', 'end'].map(id => ({
    id, kind: id === 'judge' ? 'condition' : id === 'join' || id === 'end' ? 'join' : 'role', title: id, task: '受控', inputs: [], expected_output: '', role_id: null,
    ...(id === 'judge' ? { condition: { sources: ['work'], key: 'approved', value: true, operator: 'eq' as const, aggregate: 'all' as const } } : {}),
  })), edges: [['design', 'core'], ['design', 'ui'], ['core', 'join'], ['ui', 'join'], ['join', 'work'], ['work', 'judge'], ['judge', 'work'], ['judge', 'end']],
  loops: [{ id: 'review', entry: 'work', decision: 'judge', exit: 'end', body: ['work', 'judge'], repeat_when: false, carry_inputs: [], max_iterations: 3 }],
  presentation: { groups: [{ id: 'build', title: '并行实现', node_ids: ['core', 'ui'] }], edge_labels: [{ source: 'judge', target: 'work', label: '需要修订' }] } }
}

test('总览合并阶段与循环但保留所有真实节点映射，展开能还原全部连线', () => {
  const graph = sample(), original = JSON.stringify(graph), { groups } = viewGroups(graph)
  expect(groups.map(group => group.kind)).toContain('loop')
  expect(groups.find(group => group.customId === 'build')?.title).toBe('并行实现')
  const folded = projectGraph(graph, groups, new Set(groups.map(group => group.id)))
  expect(folded.nodes.length).toBeLessThan(graph.nodes.length)
  expect(Object.keys(folded.visibleId).sort()).toEqual(graph.nodes.map(node => node.id).sort())
  const unfolded = projectGraph(graph, groups, new Set())
  expect(unfolded.nodes.map(node => node.id)).toEqual(graph.nodes.map(node => node.id))
  expect(unfolded.edges.flatMap(edge => edge.originals)).toEqual(graph.edges)
  expect(JSON.stringify(graph)).toBe(original)
})

test('没有自定义阶段时，明确的并行分支也有总览', () => {
  const graph = sample(); graph.presentation = null
  expect(viewGroups(graph).groups.some(group => group.kind === 'parallel' && group.nodeIds.includes('core') && group.nodeIds.includes('ui'))).toBe(true)
})

test('交错阶段不折叠成误导性的循环，任务保持可见并说明原因', () => {
  const graph = sample()
  graph.presentation = { groups: [{ id: 'bad', title: '交错阶段', node_ids: ['design', 'join'] }], edge_labels: [] }
  const { groups, notices } = viewGroups(graph)
  expect(groups.some(group => group.customId === 'bad')).toBe(false)
  expect(notices.join('')).toContain('交错阶段')
  expect(projectGraph(graph, groups, new Set(groups.map(group => group.id))).visibleId.design).toBe('design')
})

test('折叠摘要保留失败、等待和当前反馈，旧尝试不会覆盖新状态', () => {
  const run = { attempts: [
    { node_id: 'core', status: 'failed', current: false }, { node_id: 'core', status: 'completed', current: true },
    { node_id: 'ui', status: 'failed', current: true },
  ], activations: [{ node_id: 'work', status: 'waiting_feedback', current: true }], feedback: [
    { node_id: 'ui', status: 'open', source_current: true, handler_node_ids: [] },
    { node_id: 'core', status: 'open', source_current: false, handler_node_ids: [] },
  ] } as unknown as WorkflowRun
  expect(groupSummary(['core', 'ui', 'work'], run)).toEqual({ total: 3, completed: 1, active: 0, waiting: 1, failed: 1, stopped: 0, feedback: 1 })
})

test('业务分支标签与实际条件都能核对，未命名布尔分支不猜测通过或失败', () => {
  const graph = sample()
  expect(edgeDescription(graph, 'judge', 'work').label).toContain('需要修订')
  expect(edgeDescription(graph, 'judge', 'work').condition).toContain('approved')
  expect(edgeDescription(graph, 'judge', 'end').label).toBe('条件成立')
  expect(edgeDescription(graph, 'judge', 'work').condition).toContain('不成立')
})

test('多个循环各有边界和投影，不合并轮次或混淆返回目标', () => {
  const graph = sample()
  graph.nodes.push({ ...graph.nodes.find(node => node.id === 'work')!, id: 'work2', title: '再次验证' },
    { ...graph.nodes.find(node => node.id === 'judge')!, id: 'judge2', title: '二次门槛', condition: { sources: ['work2'], key: 'checked', value: true, operator: 'eq', aggregate: 'all' } })
  graph.edges = graph.edges.map(([a, b]) => [a, a === 'judge' && b === 'end' ? 'work2' : b])
  graph.edges.push(['work2', 'judge2'], ['judge2', 'work2'], ['judge2', 'end'])
  graph.loops![0].exit = 'work2'
  graph.loops!.push({ id: 'second', entry: 'work2', decision: 'judge2', exit: 'end', body: ['work2', 'judge2'], repeat_when: false, carry_inputs: [], max_iterations: 2 })
  const { groups } = viewGroups(graph), folded = projectGraph(graph, groups, new Set(groups.map(group => group.id)))
  expect(folded.visibleId.work).not.toBe(folded.visibleId.work2)
  const positions = topologicalPositions(graph)
  expect(positions.judge.x + 224).toBeLessThan(positions.work2.x)
  expect(edgeDescription(graph, 'judge', 'work').condition).toContain('approved')
  expect(edgeDescription(graph, 'judge2', 'work2').condition).toContain('checked')
})
