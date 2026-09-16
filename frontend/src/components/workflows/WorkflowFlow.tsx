import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, Panel, Handle, Position, MarkerType,
  applyNodeChanges, useNodesInitialized, useReactFlow, BaseEdge, BezierEdge,
  type Node, type NodeProps, type NodeChange, type Edge, type EdgeProps, type Connection,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './workflow-flow.css'
import type { WorkflowGraph, WorkflowRun } from '../../api/workflows'
import { statusLabel } from './WorkflowContext'

const edgeId = (source: string, target: string) => JSON.stringify([source, target])
import { defaultPosition, nodeColor } from './workflow-layout'

type TaskData = { title: string; index: number; role: string; status: string; editable: boolean; color: string }
type TaskNode = Node<TaskData, 'task'>

/** 节点只展示业务摘要，拖动/选择/焦点由 React Flow 管理。 */
function TaskCard({ data, selected, isConnectable }: NodeProps<TaskNode>) {
  return <div style={{ borderTop: `3px solid ${data.color}`, backgroundColor: `${data.color}0d` }} className={`workflow-task rounded-2xl border bg-panel p-4 shadow-sm ${selected ? 'border-indigo-500 ring-2 ring-indigo-200' : 'border-slate-700'}`}>
    <Handle type="target" position={Position.Left} id="in" isConnectable={isConnectable} aria-label={`${data.title} 输入连接点`} />
    <span className="text-[10px] text-slate-500">{String(data.index + 1).padStart(2, '0')} · {data.role}</span>
    <strong className="mt-2 block break-words text-sm">{data.title}</strong>
    {!data.editable && <span className="mt-3 block text-xs text-indigo-500">{data.status}</span>}
    <Handle type="source" position={Position.Right} id="out" isConnectable={isConnectable} aria-label={`${data.title} 输出连接点`} />
  </div>
}

/** 自环沿节点外侧显示，普通边继续使用 React Flow 的贝塞尔边。 */
function WorkflowEdge(props: EdgeProps) {
  if (props.source !== props.target) return <BezierEdge {...props} />
  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty } = props
  return <BaseEdge id={props.id} path={`M ${sx},${sy} C ${sx + 110},${sy - 150} ${tx - 110},${ty - 150} ${tx},${ty}`}
    markerEnd={props.markerEnd} style={props.style} />
}
const nodeTypes = { task: TaskCard }
const edgeTypes = { workflow: WorkflowEdge }

type Props = {
  graph: WorkflowGraph
  run: WorkflowRun | null
  editable: boolean
  selected: string | null
  selectedEdge: string
  onSelectEdge: (id: string) => void
  onInsertAfter: (id: string) => string | null
  roleNames: Record<number, string>
  onSelect: (id: string | null) => void
  onGraphChange: (graph: WorkflowGraph) => void
}

/** React Flow 的视口、拖动和连线适配；仅布局变更不会改写业务顺序。 */
export function WorkflowFlow(props: Props) {
  return <ReactFlowProvider><Flow {...props} /></ReactFlowProvider>
}

function Flow({ graph, run, editable, selected, selectedEdge, onSelectEdge: setSelectedEdge, onInsertAfter, roleNames, onSelect, onGraphChange }: Props) {
  const flow = useReactFlow<TaskNode>()
  const initialized = useNodesInitialized()
  const lastCount = useRef(-1)
  const [deleteNotice, setDeleteNotice] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const pendingFocus = useRef<string | null>(null)
  const makeNodes = useCallback((): TaskNode[] => graph.nodes.map((node, index) => {
    const attempt = !editable ? run?.attempts.find(a => a.node_id === node.id && a.current) : undefined
    return {
      id: node.id, type: 'task', position: node.position ?? defaultPosition(index), selected: selected === node.id,
      draggable: editable, connectable: editable, deletable: editable, ariaRole: 'button', ariaLabel: `节点 ${index + 1}：${node.title}`,
      data: { title: node.title, index, color: nodeColor(node), role: node.kind === 'approval' ? '人工确认' : roleNames[node.role_id ?? 0] ?? '角色不可用', editable,
        status: editable ? '' : attempt ? `${statusLabel(attempt.status)} · 尝试 ${attempt.number}` : '未执行 · 旧结果不沿用' },
    }
  }), [graph, run, editable, selected, roleNames])
  const [nodes, setNodes] = useState<TaskNode[]>(makeNodes)
  useEffect(() => {
    const next = makeNodes()
    setNodes(current => next.map(node => {
      const existing = current.find(n => n.id === node.id)
      return existing ? { ...existing, ...node, position: existing.dragging ? existing.position : node.position } : node
    }))
  }, [makeNodes])
  useEffect(() => {
    if (!initialized || lastCount.current === graph.nodes.length) return
    lastCount.current = graph.nodes.length
    void flow.fitView({ padding: .25, maxZoom: 1 })
  }, [initialized, graph.nodes.length, flow])

  useEffect(() => {
    if (!initialized || !pendingFocus.current) return
    const target = Array.from(root.current?.querySelectorAll<HTMLElement>('.react-flow__node') ?? []).find(el => el.dataset.id === pendingFocus.current)
    if (target) { target.focus(); pendingFocus.current = null }
  }, [initialized, nodes])

  const edges = useMemo<Edge[]>(() => graph.edges.map(([source, target]) => ({
    id: edgeId(source, target), source, target, sourceHandle: 'out', targetHandle: 'in', type: 'workflow',
    selected: selectedEdge === edgeId(source, target), deletable: editable,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#739bb0' },
    ariaLabel: `连线：${graph.nodes.find(n => n.id === source)?.title ?? source} → ${graph.nodes.find(n => n.id === target)?.title ?? target}`,
  })), [graph, selectedEdge, editable])

  function changes(changes: NodeChange<TaskNode>[]) {
    setNodes(current => applyNodeChanges(changes, current))
    const focus = changes.find(change => change.type === 'select' && change.selected)
    if (focus?.type === 'select') { onSelect(focus.id); setSelectedEdge(''); setDeleteNotice('') }
    // 鼠标落点和键盘移动保存坐标；测量、选择及拖动中间帧不污染定义。
    const positions = new Map<string, { x: number; y: number }>()
    for (const change of changes) {
      if (change.type === 'position' && change.position && !change.dragging) positions.set(change.id, change.position)
    }
    if (editable && positions.size) onGraphChange({ ...graph, nodes: graph.nodes.map(node => positions.has(node.id) ? { ...node, position: positions.get(node.id)! } : node) })
  }
  function connect(connection: Connection) {
    if (!editable || graph.edges.some(([a, b]) => a === connection.source && b === connection.target)) return
    onGraphChange({ ...graph, edges: [...graph.edges, [connection.source, connection.target]] })
  }
  return <div ref={root} className="workflow-flow flex min-h-40 min-w-0 flex-1 flex-col" onKeyDownCapture={event => {
    if (!editable || event.key !== 'Tab' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.repeat || event.nativeEvent.isComposing) return
    const target = event.target as HTMLElement
    if (!selected || !target.classList.contains('react-flow__node') || target.dataset.id !== selected) return
    event.preventDefault(); event.stopPropagation()
    pendingFocus.current = onInsertAfter(selected)
  }}>
    {deleteNotice && <p role="status" className="px-3 py-1 text-xs text-amber-600">{deleteNotice}</p>}
    <div className="min-h-36 flex-1">
      <ReactFlow<TaskNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onNodesChange={changes} onNodeClick={(_, node) => { onSelect(node.id); setSelectedEdge(''); setDeleteNotice('') }} onPaneClick={() => { onSelect(null); setSelectedEdge(''); setDeleteNotice('') }}
        onEdgeClick={(_, edge) => { setSelectedEdge(edge.id); onSelect(null); setDeleteNotice('') }} onEdgesChange={changes => { const change = changes.find(c => c.type === 'select' && c.selected); if (change?.type === 'select') { setSelectedEdge(change.id); onSelect(null); setDeleteNotice('') } }}
        onConnect={connect} nodesDraggable={editable} nodesConnectable={editable} edgesReconnectable={false} deleteKeyCode={editable ? 'Delete' : null}
        onBeforeDelete={async ({ nodes: candidates, edges: candidateEdges }) => {
          // 快捷键只属于获得焦点的画布；输入框及其他弹窗不能误删图。
          const active = document.activeElement
          if (!editable || !active || !root.current?.contains(active) || active.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]')) return false
          const removable = candidates.filter(node => !graph.edges.some(([a, b]) => a === node.id || b === node.id))
          if (removable.length !== candidates.length) setDeleteNotice('节点仍有连线，请先删除所有相连的线。')
          // React Flow 默认会连带收集节点的边；这里只删除用户明确选中的边。
          return { nodes: removable, edges: candidateEdges.filter(edge => edge.selected) }
        }}
        onDelete={({ nodes: removedNodes, edges: removedEdges }) => {
          if (!editable) return
          const ids = new Set(removedNodes.map(node => node.id))
          const edgeIds = new Set(removedEdges.map(edge => edge.id))
          onGraphChange({ nodes: graph.nodes.filter(node => !ids.has(node.id)).map(node => ({ ...node, inputs: node.inputs.filter(id => !ids.has(id)) })),
            edges: graph.edges.filter(([a, b]) => !edgeIds.has(edgeId(a, b))) })
          if (selected && ids.has(selected)) onSelect(null)
          if (edgeIds.has(selectedEdge)) setSelectedEdge('')
        }}
        fitView fitViewOptions={{ padding: .25, maxZoom: 1 }} minZoom={.15} maxZoom={2} colorMode="light"
        ariaLabelConfig={{ 'controls.ariaLabel': '画布视图控制', 'controls.zoomIn.ariaLabel': '放大画布', 'controls.zoomOut.ariaLabel': '缩小画布',
          'controls.fitView.ariaLabel': '适配视图', 'minimap.ariaLabel': '工作流缩略图',
          'node.a11yDescription.default': '按 Enter 或空格选择节点，方向键调整位置，Tab 在当前节点后插入角色任务，Shift+Tab 返回焦点导航，Delete 删除无连线节点；执行顺序由连线决定。',
          'node.a11yDescription.keyboardDisabled': '按 Enter 或空格选择节点。', 'edge.a11yDescription.default': '按 Enter 或空格选择连线，按 Delete 删除。',
          'node.a11yDescription.ariaLiveMessage': ({ x, y }) => `节点已移动到 ${Math.round(x)}，${Math.round(y)}` }}>
        <Background color="#cbdde5" gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={node => String(node.data.color ?? '#a9c9d7')} maskColor="rgba(243,249,252,.7)" className="!hidden sm:!block" />
        {!graph.nodes.length && <Panel position="top-center"><p className="p-4 text-center text-xs text-slate-500">添加角色任务，编排你的工作流。</p></Panel>}
      </ReactFlow>
    </div>
  </div>
}
