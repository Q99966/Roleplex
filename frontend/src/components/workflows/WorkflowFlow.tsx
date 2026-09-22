import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, Panel, MarkerType, ViewportPortal,
  applyNodeChanges, useNodesInitialized, useReactFlow, type NodeChange, type Edge, type Connection,
} from '@xyflow/react'
import { SmartEdgeProvider, type SmartEdgeProviderOptions } from '@tisoap/react-flow-smart-edge'
import { LocateFixed, LayoutGrid, Undo2 } from 'lucide-react'
import { WorkflowEdge } from './WorkflowEdge'
import { WorkflowCard, kindNames, type WorkflowCardNode, type WorkflowCardData } from './WorkflowCards'
import { returnRoutes } from './workflow-routing'
import { displayPositions, topologicalPositions, nodeColor, taskSize, type NodeSize, type CanvasPosition } from './workflow-layout'
import { projectGraph, viewGroups, groupSummary, edgeDescription, relatedNodes } from './workflow-projection'
import '@xyflow/react/dist/style.css'
import './workflow-flow.css'
import type { WorkflowGraph, WorkflowNode, WorkflowRun } from '../../api/workflows'
import { statusLabel, useWorkflow } from './WorkflowContext'

const edgeId = (source: string, target: string) => JSON.stringify([source, target])
const routingOptions: SmartEdgeProviderOptions = { preset: 'smoothstep', borderRadius: 12, nodePadding: 18, gridRatio: 10, routeOnlyWhenBlocked: false, routeWhileDragging: true, debounceMs: 24 }
const nodeTypes = { task: WorkflowCard }, edgeTypes = { workflow: WorkflowEdge }
type Relation = 'control' | 'input' | 'feedback'
type Props = {
  graph: WorkflowGraph; run: WorkflowRun | null; editable: boolean; selected: string | null; selectedEdge: string;
  onSelectEdge: (id: string) => void; onInsertAfter: (id: string) => string | null; roleNames: Record<number, string>;
  onSelect: (id: string | null) => void; onGraphChange: (graph: WorkflowGraph) => void;
}

/** 同一执行图的总览/节点投影；视图折叠、聚焦和运行视图整理均不提交业务修改。 */
export function WorkflowFlow(props: Props) {
  return <ReactFlowProvider><Flow {...props} /></ReactFlowProvider>
}

function Flow({ graph, run, editable, selected, selectedEdge, onSelectEdge, onInsertAfter, roleNames, onSelect, onGraphChange }: Props) {
  const workflow = useWorkflow(), flow = useReactFlow<WorkflowCardNode>(), initialized = useNodesInitialized()
  const graphKey = JSON.stringify(graph)
  const { groups, notices } = useMemo(() => viewGroups(graph), [graphKey])
  const [choice, setChoice] = useState<'overview' | 'detail' | null>(null)
  const [expanded, setExpanded] = useState(new Set<string>())
  const [relation, setRelation] = useState<Relation>('control')
  const [organizedView, setOrganizedView] = useState(false)
  const [sizes, setSizes] = useState<Record<string, NodeSize>>({})
  const [notice, setNotice] = useState('')
  const [undo, setUndo] = useState<{ before: Record<string, CanvasPosition | null>; after: Record<string, CanvasPosition> } | null>(null)
  const root = useRef<HTMLDivElement>(null), pendingFocus = useRef<string | null>(null), pendingFit = useRef<string[] | 'all' | null>(null)
  const fittedScope = useRef('')
  const overview = groups.length > 0 && (choice ?? 'overview') === 'overview'
  const collapsed = useMemo(() => new Set(overview ? groups.filter(group => !expanded.has(group.id)).map(group => group.id) : []), [groups, expanded, overview])
  const projection = useMemo(() => projectGraph(graph, groups, collapsed), [graphKey, groups, collapsed])
  const hasFolded = projection.nodes.some(node => node.group)
  const canEdit = editable && !workflow.busy && !hasFolded && relation === 'control'
  const visibleRun = useMemo(() => {
    if (editable) return null
    if (!run || !workflow.historyGraph) return run
    const version = workflow.historyGraph.revision
    const attempts = run.attempts.filter(a => a.graph_revision === version || (a.graph_revision == null && version === 0))
    const latest = new Map(attempts.map(a => [a.node_id, a.id]))
    return { ...run, activations: run.activations?.filter(a => a.graph_revision === version).map(a => ({ ...a, current: true })), attempts: attempts.map(a => ({ ...a, current: latest.get(a.node_id) === a.id })) }
  }, [run, workflow.historyGraph, editable])
  const focusedFeedback = visibleRun?.feedback?.find(item => item.id === workflow.feedbackFocusId)
  const seeds = focusedFeedback ? [focusedFeedback.node_id, ...focusedFeedback.handler_node_ids] : selected ? [selected] : []
  const relevant = useMemo(() => relatedNodes(graph, seeds), [graphKey, JSON.stringify(seeds)])

  const projectedGraph = useMemo<WorkflowGraph>(() => ({ ...graph,
    nodes: projection.nodes.map(item => item.node ?? { id: item.id, title: item.group!.title, kind: 'join', role_id: null, task: '', inputs: [], expected_output: '' }),
    edges: projection.edges.map(edge => [edge.source, edge.target]),
    loops: (graph.loops ?? []).map(loop => ({ ...loop, entry: projection.visibleId[loop.entry], decision: projection.visibleId[loop.decision],
      exit: projection.visibleId[loop.exit], body: [...new Set(loop.body.map(id => projection.visibleId[id]))] })).filter(loop => loop.entry !== loop.decision),
  }), [graphKey, projection])
  const positions = useMemo(() => hasFolded || organizedView ? topologicalPositions(projectedGraph, sizes) : displayPositions(graph, sizes), [graphKey, projectedGraph, hasFolded, organizedView, sizes])
  const expand = useCallback((id: string) => {
    setExpanded(value => new Set([...value, id])); setChoice('overview'); pendingFit.current = 'all'
  }, [])
  const loopCaption = (loopId?: string) => {
    const loop = graph.loops?.find(loop => loop.id === loopId), state = loopId ? visibleRun?.loop_states?.[loopId] : null
    if (!loop) return ''
    const exit = graph.nodes.find(node => node.id === loop.exit)?.title ?? loop.exit
    return `${state ? `第 ${state.iteration + 1}${loop.max_iterations ? ' / ' + loop.max_iterations : ''} 轮${state.exited ? ' · 已退出' : state.limited ? ' · 达到上限' : ''}` : loop.max_iterations ? `最多 ${loop.max_iterations} 轮` : '按条件循环'} · 出口：${exit}`
  }
  const makeNodes = useCallback((): WorkflowCardNode[] => projection.nodes.map(item => {
    const node = item.node, group = item.group
    const activation = node ? visibleRun?.activations?.find(a => a.node_id === node.id && (a.current ?? !a.loop_id)) : undefined
    const attempt = node ? visibleRun?.attempts.find(a => a.node_id === node.id && a.current) : undefined
    const state = activation?.status === 'waiting_feedback' ? activation.status : attempt?.status ?? activation?.status
    const tone: WorkflowCardData['tone'] = ['failed', 'blocked', 'interrupted'].includes(state ?? '') ? 'failed'
      : ['waiting', 'waiting_feedback'].includes(state ?? '') ? 'waiting' : ['active', 'queued', 'running'].includes(state ?? '') ? 'active' : state === 'completed' ? 'completed' : 'neutral'
    const summary = groupSummary(item.memberIds, visibleRun), index = node ? graph.nodes.findIndex(n => n.id === node.id) : -1
    return { id: item.id, type: 'task', position: positions[item.id], selected: selected === item.id,
      draggable: canEdit, connectable: canEdit, deletable: canEdit, ariaRole: group ? 'group' : 'button',
      ariaLabel: group ? `${group.kind === 'loop' ? '循环' : '阶段'}：${group.title}` : `节点 ${index + 1}：${node!.title}`,
      data: { title: node?.title ?? group!.title, index, kind: node?.kind ?? 'group', color: node ? nodeColor(node) : group?.kind === 'loop' ? '#558f87' : '#4385a0', editable,
        role: node ? roleNames[attempt?.assigned_role_id ?? node.role_id ?? 0] ?? kindNames[node.kind] : '',
        tone, dimmed: relevant.size > 0 && !item.memberIds.some(id => relevant.has(id)) && !summary.failed && !summary.waiting && !summary.feedback, feedbackCount: summary.feedback,
        status: state ? `${attempt?.waiting_resource ? '等待资源' : statusLabel(state)}${attempt?.loop_id ? ` · 第 ${(attempt.iteration ?? 0) + 1} 轮` : ''}${attempt ? ` · 尝试 ${attempt.number}` : ''}` : '尚未执行',
        group, summary, loopText: loopCaption(group?.loopId), expand: group ? () => expand(group.id) : undefined,
        membersText: group?.nodeIds.map(id => graph.nodes.find(node => node.id === id)?.title ?? id).join('、'),
      } }
  }), [projection, positions, graphKey, selected, canEdit, editable, visibleRun, roleNames, relevant, expand])
  const [nodes, setNodes] = useState<WorkflowCardNode[]>(makeNodes)
  useEffect(() => { setNodes(current => makeNodes().map(node => {
    const existing = current.find(item => item.id === node.id)
    return existing ? { ...existing, ...node, position: existing.dragging ? existing.position : node.position } : node
  })) }, [makeNodes])

  useEffect(() => {
    if (!selected || projection.visibleId[selected] === selected) return
    setExpanded(value => new Set([...value, ...groups.filter(group => group.nodeIds.includes(selected)).map(group => group.id)]))
  }, [selected, projection, groups])
  useEffect(() => {
    if (!workflow.feedbackFocusId) return
    setChoice('detail'); setRelation('feedback'); pendingFit.current = seeds
  }, [workflow.feedbackFocusId])
  useEffect(() => {
    if (!initialized || !nodes.length) return
    const scope = `${workflow.historyGraph?.revision ?? 'current'}:${projection.nodes.map(node => node.id).join(',')}`
    const request = pendingFit.current
    if (request || fittedScope.current !== scope) {
      if (Array.isArray(request) && request.some(id => !nodes.some(node => node.id === id))) return
      fittedScope.current = scope; pendingFit.current = null
      // 默认保持文字可读；完整缩小到全图由“适配视图”显式触发。
      void flow.fitView({ padding: .2, minZoom: .65, maxZoom: 1, ...(Array.isArray(request) ? { nodes: request.map(id => ({ id })) } : {}) })
    }
    if (pendingFocus.current) {
      const target = Array.from(root.current?.querySelectorAll<HTMLElement>('.react-flow__node') ?? []).find(el => el.dataset.id === pendingFocus.current)
      if (target) { target.focus(); pendingFocus.current = null }
    }
  }, [initialized, nodes, projection, workflow.historyGraph, flow])

  const edges = useMemo<Edge[]>(() => {
    const routes = returnRoutes(projectedGraph, nodes)
    const color = relation === 'input' ? '#6684bd' : relation === 'feedback' ? '#a87932' : '#739bb0'
    const projected = relation === 'control' ? projection.edges : projectGraph({ ...graph, edges: relation === 'input'
      ? graph.nodes.flatMap(node => node.inputs.map(source => [source, node.id] as [string, string]))
      : (visibleRun?.feedback ?? []).filter(item => !focusedFeedback || item.id === focusedFeedback.id).flatMap(item => item.handler_node_ids.map(id => [item.node_id, id] as [string, string])) }, groups, collapsed).edges
    return projected.map(edge => {
      const exact = edge.originals.length === 1 && edge.source === edge.originals[0][0] && edge.target === edge.originals[0][1]
      const descriptions = edge.originals.map(([a, b]) => edgeDescription(graph, a, b))
      const back = edge.originals.some(([a, b]) => graph.loops?.some(loop => loop.decision === a && loop.entry === b))
      const labels = [...new Set(descriptions.map(item => item.label).filter(Boolean))]
      return { id: relation === 'control' ? edge.id : relation + ':' + edge.id, source: edge.source, target: edge.target, sourceHandle: 'out', targetHandle: 'in', type: 'workflow',
        data: { ...(routes.get(edgeId(edge.source, edge.target)) ?? { returnEdge: false }), relation,
          explanation: relation === 'control' ? edge.originals.map(([a, b], index) => `${graph.nodes.find(node => node.id === a)?.title ?? a} → ${graph.nodes.find(node => node.id === b)?.title ?? b}：${descriptions[index].condition}`).join('；') : relation === 'input' ? '显式交接结构化结果；不代表控制连线' : '反馈来源与实际处理节点的关联；不新增执行依赖' },
        selected: relation === 'control' && selectedEdge === edge.id, selectable: exact, deletable: canEdit,
        label: relation === 'input' ? '结果交接' : relation === 'feedback' ? '反馈处置' : back ? `返回下一轮 · ${labels.join(' / ')}` : labels.join(' / ') || (edge.originals.length > 1 ? `${edge.originals.length} 条依赖` : undefined),
        style: { stroke: color, strokeWidth: 1.6, opacity: relevant.size && !edge.originals.some(([a, b]) => relevant.has(a) && relevant.has(b)) ? .18 : 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color },
        ariaLabel: `连线：${projectedGraph.nodes.find(node => node.id === edge.source)?.title ?? edge.source} → ${projectedGraph.nodes.find(node => node.id === edge.target)?.title ?? edge.target}`,
      }
    })
  }, [graphKey, projectedGraph, projection, nodes, relation, visibleRun, focusedFeedback, groups, collapsed, selectedEdge, canEdit, relevant])

  function changes(changes: NodeChange<WorkflowCardNode>[]) {
    setNodes(current => applyNodeChanges(changes, current))
    const dimensions = changes.filter(change => change.type === 'dimensions' && change.dimensions)
    if (dimensions.length) setSizes(current => {
      const next = { ...current }; let changed = false
      for (const change of dimensions) if (change.type === 'dimensions' && change.dimensions && (next[change.id]?.width !== change.dimensions.width || next[change.id]?.height !== change.dimensions.height)) { next[change.id] = change.dimensions; changed = true }
      return changed ? next : current
    })
    const focus = changes.find(change => change.type === 'select' && change.selected)
    if (focus?.type === 'select' && graph.nodes.some(node => node.id === focus.id) && focus.id !== selected) { onSelect(focus.id); onSelectEdge(''); setNotice('') }
    const positions = new Map<string, CanvasPosition>()
    for (const change of changes) if (change.type === 'position' && change.position && !change.dragging) positions.set(change.id, change.position)
    if (canEdit && positions.size) onGraphChange({ ...graph, nodes: graph.nodes.map(node => positions.has(node.id) ? { ...node, position: positions.get(node.id)! } : node) })
  }
  function connect(connection: Connection) {
    if (!canEdit || graph.edges.some(([a, b]) => a === connection.source && b === connection.target)) return
    onGraphChange({ ...graph, edges: [...graph.edges, [connection.source, connection.target]] })
  }
  function organize() {
    setChoice('detail'); pendingFit.current = 'all'; onSelectEdge('')
    if (editable) {
      const next = topologicalPositions(graph, sizes)
      setUndo({ before: Object.fromEntries(graph.nodes.map(node => [node.id, node.position ?? null])), after: next })
      onGraphChange({ ...graph, nodes: graph.nodes.map(node => ({ ...node, position: next[node.id] })) })
      setNotice('已整理节点位置，保存流程后生效；执行关系保持原图。')
    } else { setOrganizedView(true); setNotice('已整理本次视图，原保存位置保留。') }
  }
  const canUndo = Boolean(undo && graph.nodes.length === Object.keys(undo.after).length && graph.nodes.every(node => JSON.stringify(node.position) === JSON.stringify(undo.after[node.id])))
  function undoLayout() {
    if (editable && undo && canUndo) onGraphChange({ ...graph, nodes: graph.nodes.map(node => ({ ...node, position: undo.before[node.id] })) })
    setUndo(null); setOrganizedView(false); setNotice('已恢复整理前的位置。'); pendingFit.current = 'all'
  }
  const selectedPair = graph.edges.find(([a, b]) => edgeId(a, b) === selectedEdge)
  return <div ref={root} className="workflow-flow flex min-h-40 min-w-0 flex-1 flex-col" onKeyDownCapture={event => {
    if (!canEdit || event.key !== 'Tab' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.repeat || event.nativeEvent.isComposing) return
    const target = event.target as HTMLElement
    if (!selected || !target.classList.contains('react-flow__node') || target.dataset.id !== selected) return
    event.preventDefault(); event.stopPropagation(); pendingFocus.current = onInsertAfter(selected)
  }}>
    <div className="workflow-viewbar" role="toolbar" aria-label="画布视图操作">
      <div className="workflow-view-switch"><button type="button" aria-pressed={overview} disabled={!groups.length} onClick={() => { setChoice('overview'); setExpanded(new Set()); onSelect(null); onSelectEdge(''); pendingFit.current = 'all' }}>阶段总览</button>
        <button type="button" aria-pressed={!overview} onClick={() => { setChoice('detail'); pendingFit.current = 'all' }}>节点细节</button></div>
      <button type="button" onClick={organize} disabled={!graph.nodes.length}><LayoutGrid size={14} />{editable ? '整理布局' : '整理视图'}</button>
      {(canUndo || organizedView) && <button type="button" onClick={undoLayout}><Undo2 size={14} />撤销整理</button>}
      <button type="button" disabled={!seeds.length} onClick={() => { void flow.fitView({ nodes: seeds.map(id => ({ id: projection.visibleId[id] })), padding: .4, minZoom: .7, maxZoom: 1 }) }}><LocateFixed size={14} />聚焦选中</button>
      <select className="workflow-node-jump" aria-label="定位节点" value={selected && graph.nodes.some(node => node.id === selected) ? selected : ''} onChange={e => {
        const id = e.target.value
        onSelect(id || null); onSelectEdge('')
        if (id) pendingFit.current = [id]
      }}><option value="">定位节点…</option>{graph.nodes.map(node => <option key={node.id} value={node.id}>{node.title}</option>)}</select>
      <label>关系<select aria-label="画布关系" value={relation} onChange={e => { setRelation(e.target.value as Relation); onSelectEdge('') }}><option value="control">执行依赖</option><option value="input">结果交接</option><option value="feedback">反馈处置</option></select></label>
    </div>
    <div className="workflow-view-caption">
      <span>{hasFolded ? `${graph.nodes.length} 个任务 / ${projection.nodes.length} 个展示节点 · 展开后编辑连线` : `${graph.nodes.length} 个节点 · 执行顺序由连线决定`}</span>
      {!!seeds.length && <button type="button" onClick={() => { onSelect(null); setRelation('control') }}>清除聚焦</button>}
    </div>
    {notice && <p role="status" className="workflow-view-notice">{notice}</p>}
    {notices.map(text => <p key={text} className="workflow-view-notice">{text}</p>)}
    {selectedPair && relation === 'control' && <p className="workflow-view-notice" role="note">实际规则：{edgeDescription(graph, ...selectedPair).condition}</p>}
    {focusedFeedback && <p className="workflow-view-notice">反馈：{focusedFeedback.summary} · {focusedFeedback.handler_node_ids.length ? '突出来源与处理路径' : '尚未关联处理节点'}</p>}
    {relation !== 'control' && !edges.length && <p className="workflow-view-notice">{relation === 'input' ? '当前没有显式结果交接。' : '当前没有可显示的反馈处理关联。'}</p>}
    <div className="min-h-36 flex-1">
      <SmartEdgeProvider nodes={nodes} options={routingOptions}>
        <ReactFlow<WorkflowCardNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          onNodesChange={changes} onNodeClick={(_, node) => { if (node.data.group) return; onSelect(node.id); onSelectEdge(''); setNotice('') }}
          onPaneClick={() => { onSelect(null); onSelectEdge(''); setNotice('') }}
          onEdgeClick={(_, edge) => { if (relation === 'control' && graph.edges.some(([a, b]) => edgeId(a, b) === edge.id)) { onSelectEdge(edge.id); setNotice('') } }}
          onEdgesChange={changes => { const item = changes.find(c => c.type === 'select' && c.selected); if (item?.type === 'select' && relation === 'control' && graph.edges.some(([a, b]) => edgeId(a, b) === item.id)) onSelectEdge(item.id) }}
          onConnect={connect} nodesDraggable={canEdit} nodesConnectable={canEdit} edgesReconnectable={false} deleteKeyCode={canEdit ? 'Delete' : null}
          onBeforeDelete={async ({ nodes: candidates, edges: candidateEdges }) => {
            const active = document.activeElement
            if (!canEdit || !active || !root.current?.contains(active) || active.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]')) return false
            const removable = candidates.filter(node => graph.nodes.some(item => item.id === node.id) && !graph.edges.some(([a, b]) => a === node.id || b === node.id))
            if (removable.length !== candidates.length) setNotice('节点仍有连线，请先删除所有相连的线。')
            return { nodes: removable, edges: candidateEdges.filter(edge => edge.selected) }
          }}
          onDelete={({ nodes: removedNodes, edges: removedEdges }) => {
            if (!canEdit) return
            const ids = new Set(removedNodes.map(node => node.id)), edges = new Set(removedEdges.map(edge => edge.id))
            onGraphChange({ ...graph, nodes: graph.nodes.filter(node => !ids.has(node.id)).map(node => ({ ...node, inputs: node.inputs.filter(id => !ids.has(id)) })), edges: graph.edges.filter(([a, b]) => !edges.has(edgeId(a, b))) })
            if (selected && ids.has(selected)) onSelect(null)
            if (edges.has(selectedEdge)) onSelectEdge('')
          }}
          minZoom={.15} maxZoom={2} colorMode="light"
          ariaLabelConfig={{ 'controls.ariaLabel': '画布视图控制', 'controls.zoomIn.ariaLabel': '放大画布', 'controls.zoomOut.ariaLabel': '缩小画布', 'controls.fitView.ariaLabel': '适配视图', 'minimap.ariaLabel': '工作流缩略图',
            'node.a11yDescription.default': '按 Enter 或空格选择节点。展开节点细节后，方向键调整位置，Tab 插入任务，Delete 删除无连线节点；连线决定执行顺序。',
            'node.a11yDescription.keyboardDisabled': '按 Enter 或空格选择节点。', 'edge.a11yDescription.default': '选择连线可核对实际规则；编辑模式按 Delete 删除执行连线。',
            'node.a11yDescription.ariaLiveMessage': ({ x, y }) => `节点已移动到 ${Math.round(x)}，${Math.round(y)}` }}>
          <Background color="#cbdde5" gap={20} /><Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeColor={node => String(node.data.color ?? '#a9c9d7')} maskColor="rgba(243,249,252,.7)" className="!hidden sm:!block" />
          <ViewportPortal>{groups.filter(group => group.loopId && !collapsed.has(group.id)).map(group => {
            const members = nodes.filter(node => projection.nodes.find(item => item.id === node.id)?.memberIds.every(id => group.nodeIds.includes(id)))
            if (!members.length) return null
            const left = Math.min(...members.map(node => node.position.x)) - 28, top = Math.min(...members.map(node => node.position.y)) - 54
            const right = Math.max(...members.map(node => node.position.x + (node.measured?.width ?? taskSize.width))) + 28
            const bottom = Math.max(...members.map(node => node.position.y + (node.measured?.height ?? taskSize.height))) + 26
            return <div key={group.id} className="workflow-loop-region" role="group" aria-label={`循环范围：${group.title}`} style={{ left, top, width: right - left, height: bottom - top }}>
              <div className="workflow-loop-label"><span title={loopCaption(group.loopId)}>{group.title} · {loopCaption(group.loopId)}</span><button type="button" className="nodrag nopan" onClick={() => {
                const open = overview ? new Set(expanded) : new Set(groups.map(group => group.id)); open.delete(group.id)
                setExpanded(open); setChoice('overview'); onSelect(null); pendingFit.current = 'all'
              }}>收起循环：{group.title}</button></div>
            </div>
          })}</ViewportPortal>
          {!graph.nodes.length && <Panel position="top-center"><p className="p-4 text-center text-xs text-slate-500">添加角色任务，编排你的工作流。</p></Panel>}
        </ReactFlow>
      </SmartEdgeProvider>
    </div>
  </div>
}
