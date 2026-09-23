import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { getAuthEpoch, type Conversation } from '../../api/client'
import { workflows, type WorkflowDefinition, type WorkflowList, type WorkflowRun, type WorkflowGraph, type Coordination, type CoordinateRequest } from '../../api/workflows'
import { defaultPosition, displayPositions, serialOrder } from './workflow-layout'
import { cleanPresentation } from './workflow-projection'
import { workflowTarget, type InspectorPage } from './workflow-target'
import { useAppStore } from '../../store/app'

import { DraftWriter, readLocalDrafts, deleteLocalDraft, type Draft, type LocalDraft, type DraftStatus } from './local-drafts'
const errorText: Record<string, string> = {
  WORKFLOW_DRAFT_PRESERVE_FAILED: '当前草稿尚未安全保留，请先下载备份或重试本地保存。',
  WORKFLOW_HISTORY_READ_ONLY: '当前正在查看历史版本，请先明确返回当前运行或编辑模板。',
  WORKFLOW_FEEDBACK_REVISION_CONFLICT: '反馈已被其他操作更新，请查看最新处置记录后再提交。',
  WORKFLOW_FEEDBACK_REQUEST_CONFLICT: '这次提交与原请求不一致，请核对已经保存的反馈。',
  WORKFLOW_FEEDBACK_EVIDENCE_REQUIRED: '解决反馈需要实际完成的验证尝试，或 Owner 明确记录人工核验。',
  WORKFLOW_FEEDBACK_HANDLER_REQUIRED: '请选择本次运行中尚未派发、且由所选角色执行的处理节点。',
  WORKFLOW_FEEDBACK_STATE_CONFLICT: '反馈状态已改变，请查看记录后重新选择处置。',
  WORKFLOW_FEEDBACK_COORDINATION_ACTIVE: '协调者正在处理本次运行，请等待当前处置结束。',
  WORKFLOW_FEEDBACK_LOOP_BOUNDARY: '反馈正在阻塞本轮循环，请保持入口、判断和出口，先在未派发区域补充处置节点。',
  WORKFLOW_GRAPH_REVISION_CONFLICT: '图已被其他编辑更新。本地草稿已保留，请核对版本差异。',
  WORKFLOW_GRAPH_FROZEN: '修改涉及已派发的节点或已生效依赖，请保留这些内容，或先停止并核对结果。',
  WORKFLOW_GRAPH_SCOPE: '本次请求没有该目标或操作的授权。',
  WORKFLOW_GRAPH_READ_REQUIRED: '整体写入前需要读取目标的完整最新图。',
  WORKFLOW_GRAPH_MUTATION_CONFLICT: '修改键已被用于不同操作，请核对上次提交结果。',
  WORKFLOW_GRAPH_INVALID: '图的结构或引用无效，请按错误位置修正。',
  WORKFLOW_COORDINATION_REVOKED: '本次协调授权已失效，已提交的修改仍保留。',
  ORCHESTRATOR_REQUIRED: '请先在会话成员模块任命有效的群协调者。',
  ORCHESTRATOR_APPOINTMENT_CHANGED: '群协调者任命已变更，旧协调运行已停止派发。',
  ORCHESTRATOR_EXECUTION_FAILED: '协调模型执行失败，已封闭后续派发；请核对协调记录后重试。',
  WORKFLOW_TOOL_NOT_GRANTED: '节点请求的工具超出当前角色或工作区授权，请检查工具需求。',
  WORKFLOW_LOOP_CONFIG_REQUIRED: '存在回边但缺少循环声明，请配置循环入口、判断、循环体和出口。',
  WORKFLOW_LOOP_CONFIG_INVALID: '循环必须是独立单入口区域，包含判断、回边和出口；当前不支持嵌套或交叉循环域。',
  WORKFLOW_CONDITION_INVALID: '请为判断配置结构化来源、字段和比较值。缺失结果不能默认循环。',
  WORKFLOW_EDGE_CONFIG_INVALID: '判断节点出边需要明确的 true/false 标签，其他边使用并行依赖。',
  WORKFLOW_ENTRY_REQUIRED: '多入口图需要明确选择全部入口节点。',
  WORKFLOW_CONCURRENCY_UNAVAILABLE: '并发数超过当前后端配置容量，请调整后重试。',
  WORKFLOW_RETRY_ACTIVE_DEPENDENTS: '相关下游仍在运行，请先等待收口或停止该运行再重试。',
  WORKFLOW_REVISION_CONFLICT: '内容或运行状态已更新，请刷新后检查，再重新操作。未保存编辑仍保留在当前会话。',
  WORKFLOW_EXECUTION_UNSUPPORTED: '图已保存，但当前仅支持完整串行路径运行。分支、环路或断开的节点请调整后再启动。',
  WORKFLOW_ROLE_UNAVAILABLE: '流程包含已退出、停用或删除的角色，请检查各节点。',
  WORKFLOW_INPUT_UNAVAILABLE: '节点所需的上游结果尚未完成，请先处理上游节点。',
  WORKFLOW_CAPABILITY_CHANGED: '角色或工作区工具权限已撤销，请核查权限后再选择重试。',
  WORKFLOW_RESOURCE_CHANGED: '工作区已改变或不可用，请核查资源后创建新运行。',
  WORKFLOW_RUN_ACTIVE: '本会话已有活动流程，请先停止或完成它。',
  WORKFLOW_RETRY_REVIEW_REQUIRED: '请先核对所选尝试的执行事实，并确认重试范围。',
  WORKFLOW_STATE_CONFLICT: '当前状态不支持这个操作，请刷新运行。',
}

function useController(conversation: Conversation) {
  const { worldName, user } = useAppStore()
  const epoch = getAuthEpoch()
  const key = `${epoch}:${worldName}:${user?.id}:${conversation.id}`
  const blank = (): Draft => ({ definition: { id: crypto.randomUUID(), name: '新工作流', revision: 0, graph: { nodes: [], edges: [], runtime_version: 2, loops: [], edge_rules: [], entries: [] } }, dirty: false, input: '', requestKey: crypto.randomUUID() })
  const [draft, setDraft] = useState<Draft>(blank)
  const [data, setData] = useState<WorkflowList>({ definitions: [], runs: [] })
  const [runId, setRunId] = useState<string | null>(null)
  const [mode, setMode] = useState<'edit' | 'run'>('edit')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState('')
  const [graphNotice, setGraphNotice] = useState('')
  const [historyGraph, setHistoryGraph] = useState<{ runId: string; revision: number; graph: WorkflowGraph } | null>(null)
  const [protectedNodes, setProtectedNodesState] = useState<string[]>([])
  const [protectionEdited, setProtectionEdited] = useState(false)
  const [automaticFeedback, setAutomaticFeedback] = useState(false)
  const [coordinationId, setCoordinationId] = useState<string | null>(null)
  const coordinationRequests = useRef(new Map<string, string>())
  const historyRequest = useRef(0)
  const [detailAttempt, setDetailAttempt] = useState<string | null>(null)
  const [feedbackFocusId, setFeedbackFocusId] = useState<string | null>(null)
  const [inspectorPage, setInspectorPage] = useState<InspectorPage>('context')
  const [coordinationNodeId, setCoordinationNodeId] = useState<string | null>(null)
  const [coordinationInputs, setCoordinationInputs] = useState<Record<string, string>>({})
  const [startMode, setStartMode] = useState<'manual' | 'coordinated'>('manual')
  const alive = useRef(true)
  const fetching = useRef(false)
  const pending = useRef(false)
  const current = () => alive.current && epoch === getAuthEpoch()
  const run = data.runs.find(row => row.id === runId) ?? null
  const target = workflowTarget(mode, draft, run, runId, historyGraph)
  const coordinationInputKey = `${target.kind === 'template' ? 'template' : 'run'}:${target.id}:${coordinationNodeId ?? ''}`
  const graph = mode === 'run' && run ? historyGraph?.runId === run.id ? historyGraph.graph : run.graph : draft.definition.graph
  const remoteDefinition = data.definitions.find(d => d.id === draft.definition.id && d.revision > draft.definition.revision)
  const remoteRun = draft.runTarget ? data.runs.find(r => r.id === draft.runTarget && (r.latest_graph_revision ?? 0) > draft.definition.revision) : undefined
  const conflict = draft.dirty && Boolean(draft.runTarget ? remoteRun : remoteDefinition)
  const [localReady, setLocalReady] = useState(false)
  const [localStatus, setLocalStatus] = useState<DraftStatus>('saving')
  const [localNotice, setLocalNotice] = useState('')
  const [localCopies, setLocalCopies] = useState<LocalDraft[]>([])
  const [localRetry, setLocalRetry] = useState(0)
  const [localGeneration, setLocalGeneration] = useState(0)
  const writer = useRef<DraftWriter | null>(null)
  const hydrated = useRef(false)
  const restoreSelection = useRef<string | null>(null)
  async function preserveDraft(manualOnly = false) {
    if (writer.current && !await writer.current.preserve(manualOnly)) throw { code: 'WORKFLOW_DRAFT_PRESERVE_FAILED' }
  }
  function revealInspector(page: InspectorPage = 'context', force = true) {
    setInspectorPage(page)
    if (page === 'drafts') void reloadLocalCopies()
    if (current()) window.dispatchEvent(new CustomEvent('roleplex:workflow-inspector', { detail: { conversationId: conversation.id, force } }))
  }
  async function restoreLocal(record: LocalDraft, restoreView = false) {
    if (localReady && writer.current && !await writer.current.preserve()) {
      setLocalNotice('当前编辑尚未安全保留，请先下载草稿备份或重试本地保存。'); return
    }
    if (!current()) return
    const view = restoreView ? record.view.target : undefined
    const nextMode = view?.mode ?? 'edit', nextRun = view?.mode === 'run' ? view.runId : record.draft.runTarget ?? null
    const history = view?.mode === 'run' ? view.historyRevision : null
    const ticket = ++historyRequest.current
    setDraft(structuredClone(record.draft)); setMode(nextMode); setRunId(nextRun)
    setOpen(restoreView ? record.view.open : true); setProtectedNodesState(record.view.protectedNodes); setProtectionEdited(record.view.protectionEdited)
    restoreSelection.current = mode !== nextMode || runId !== nextRun || draft.definition.id !== record.draft.definition.id ? record.view.selected : null
    setSelected(record.view.selected); setSelectedEdge(''); setFeedbackFocusId(null); setInspectorPage('context'); setHistoryGraph(null); setCoordinationId(null)
    setLocalNotice(record.draft.dirty ? '已恢复本地未提交的编辑。' : '')
    if (nextRun && history != null) {
      // 恢复失败也保留历史只读对象，不能悄悄进入当前运行的编辑模式。
      setHistoryGraph({ runId: nextRun, revision: history, graph: { nodes: [], edges: [], runtime_version: 2 } })
      try {
        const value = await workflows.readGraph(conversation.id, 'run', nextRun, history)
        if (current() && ticket === historyRequest.current) setHistoryGraph({ runId: nextRun, revision: value.graph_revision, graph: value.graph })
      } catch { if (current()) setError('历史版本未能恢复，可重试或明确返回当前运行。') }
    }
    if ((!restoreView || record.view.open) && current()) window.dispatchEvent(new CustomEvent('roleplex:workflow-show', { detail: conversation.id }))
  }
  useEffect(() => {
    let cancelled = false
    if (!user?.is_owner) { setLocalReady(true); return }
    void (async () => {
      try {
        const { scope } = await workflows.draftScope(conversation.id)
        const result = await readLocalDrafts(scope)
        if (cancelled || !current()) return
        setLocalCopies(result.items)
        if (!hydrated.current && result.preferred) await restoreLocal(result.preferred, true)
        hydrated.current = true
        writer.current = new DraftWriter(scope, status => { if (!cancelled && current()) setLocalStatus(status) })
        setLocalGeneration(value => value + 1)
        setLocalStatus(result.storageError ? 'error' : 'saving')
        if (result.corrupt) setLocalNotice('部分本地副本损坏，已跳过；原始副本仍保留。')
        setLocalReady(true)
      } catch {
        if (!cancelled && current()) { setLocalStatus('error'); setLocalNotice('无法读取草稿存储身份，请重试。恢复完成前暂不开放编辑。') }
      }
    })()
    return () => { cancelled = true; writer.current?.dispose(); writer.current = null }
  }, [key, localRetry])
  // 在浏览器处理离开事件前交接最新已提交的 React 状态，避免防抖窗口丢失末次输入。
  useLayoutEffect(() => {
    if (localReady) writer.current?.schedule(draft, { open, selected, protectedNodes, protectionEdited,
      target: { mode, runId, historyRevision: mode === 'run' ? historyGraph?.revision ?? null : null } })
  }, [localReady, localGeneration, draft, open, selected, protectedNodes, protectionEdited, mode, runId, historyGraph?.revision])
  function retryLocal() {
    if (writer.current) void writer.current.flush()
    else { setLocalNotice(''); setLocalRetry(value => value + 1) }
  }
  function exportLocal() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 1, draft, view: { open, selected, protectedNodes, protectionEdited,
      target: { mode, runId, historyRevision: historyGraph?.revision ?? null } } }, null, 2)], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'workflow-draft.json'; anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  async function removeLocal(record: LocalDraft) {
    try { await deleteLocalDraft(record); setLocalCopies(values => values.filter(value => value.id !== record.id)) }
    catch { setLocalNotice('副本删除失败或已被其他页面更新，请稍后重新打开会话核对。') }
  }
  async function reloadLocalCopies() {
    const controller = writer.current
    if (!controller) return
    controller.emergency(); await controller.flush()
    const records = await readLocalDrafts(controller.scope)
    if (current()) setLocalCopies(records.items)
  }

  useEffect(() => { setSelected(restoreSelection.current); restoreSelection.current = null; setSelectedEdge(''); setGraphNotice(''); setDetailAttempt(null); setFeedbackFocusId(null); setInspectorPage('context'); setCoordinationNodeId(null) }, [mode, runId, draft.definition.id])

  async function refresh() {
    if (fetching.current || !user?.is_owner) return
    fetching.current = true
    try {
      const result = await workflows.list(conversation.id)
      if (current()) setData(previous => {
        // HTTP 快照可能早于刚返回的保存/控制响应；无删除接口时保留已知较新对象。
        const merge = <T extends { id: string; revision: number }>(incoming: T[], known: T[]) => [
          ...incoming.map(row => { const prior = known.find(r => r.id === row.id); return prior && prior.revision > row.revision ? prior : row }),
          ...known.filter(row => !incoming.some(r => r.id === row.id)),
        ]
        return { ...result, definitions: merge(result.definitions, previous.definitions), runs: merge(result.runs, previous.runs), coordinations: merge(result.coordinations ?? [], previous.coordinations ?? []) }
      })
    } catch (cause) {
      if (current()) setError(errorText[(cause as { code?: string })?.code ?? ''] ?? '工作流读取失败，可点击刷新重试。')
    } finally { fetching.current = false }
  }
  useEffect(() => {
    alive.current = true
    void refresh()
    const interval = window.setInterval(() => void refresh(), 1500)
    const notify = (event: Event) => { if ((event as CustomEvent).detail === conversation.id) void refresh() }
    window.addEventListener('roleplex:workflow', notify)
    return () => { alive.current = false; clearInterval(interval); window.removeEventListener('roleplex:workflow', notify) }
  }, [key])
  const openedByLink = useRef<string | null>(null)
  useEffect(() => {
    if (!localReady) return
    const jump = () => {
      const [path, query = ''] = window.location.hash.split('?')
      const id = new URLSearchParams(query).get('workflow_run')
      if (path !== `#/workspace/conversation/${conversation.id}` || !id || openedByLink.current === id || !data.runs.some(row => row.id === id)) return
      openedByLink.current = id
      void selectRun(id)
    }
    jump(); window.addEventListener('hashchange', jump)
    return () => window.removeEventListener('hashchange', jump)
  }, [localReady, conversation.id, data.runs])
  // 服务端图更新可自动接纳，人工脏草稿则保留原版本，交给冲突面板显式处理。
  useEffect(() => {
    if (draft.dirty || draft.runTarget || !remoteDefinition) return
    const next = remoteDefinition
    setDraft(value => value.dirty || value.runTarget || value.definition.id !== next.id || value.definition.revision >= next.revision ? value : { ...value, definition: structuredClone(next) })
    setGraphNotice(`已同步流程版本 ${next.revision}`)
  }, [data.definitions, draft.definition.id, draft.definition.revision, draft.dirty, draft.runTarget])
  useEffect(() => {
    const active = data.coordinations?.find(c => c.id === coordinationId)
    if (!active?.started_run_id || draft.dirty) return
    if (data.runs.some(r => r.id === active.started_run_id)) { setRunId(active.started_run_id); setMode('run'); setHistoryGraph(null); setCoordinationId(null) }
  }, [data.coordinations, data.runs, coordinationId, draft.dirty])

  async function perform(action: () => Promise<void>) {
    if (pending.current || !localReady || !user?.is_owner) return false
    pending.current = true; setBusy(true); setError('')
    let succeeded = false
    try { await action(); succeeded = true }
    catch (cause) { if (current()) { const value = cause as { code?: string; details?: { fields?: unknown; node_id?: string; operation_index?: number } }; setError((errorText[value.code ?? ''] ?? '操作未完成，请检查节点、依赖和角色权限；刷新核对结果后再试。') + (value.details ? ` 定位：${JSON.stringify(value.details)}` : '')) } }
    finally { pending.current = false; if (current()) { setBusy(false); void refresh() } }
    return succeeded && current()
  }
  function update(definition: WorkflowDefinition) {
    if (!target.editable || pending.current || !user?.is_owner) return
    setDraft(value => ({ ...value, definition, dirty: true, requestKey: crypto.randomUUID() }))
  }
  async function choose(definition?: WorkflowDefinition) {
    return perform(async () => {
      await preserveDraft()
      const records = writer.current ? await readLocalDrafts(writer.current.scope) : null
      if (!current()) return
      if (records) setLocalCopies(records.items)
      // 按目标取最新副本，再判断 dirty；不能捡起比已提交版本更早的脏恢复点。
      const cached = definition ? records?.items.find(row => row.draft.definition.id === definition.id && !row.draft.runTarget) : undefined
      const keepProtection = definition?.id === draft.definition.id && !draft.runTarget && mode === 'edit'
      historyRequest.current++
      if (definition?.id === draft.definition.id && !draft.runTarget) { /* 当前模板草稿原样保留。 */ }
      else setDraft(cached?.draft.dirty && cached.autoRestore !== false ? structuredClone(cached.draft) : definition ? { definition: structuredClone(definition), dirty: false, input: '', requestKey: crypto.randomUUID() } : blank())
      setRunId(null); setMode('edit'); setError(''); setOpen(true); setHistoryGraph(null); setCoordinationId(null)
      if (!keepProtection) {
        const view = cached?.autoRestore !== false && cached?.view.target?.mode !== 'run' ? cached?.view : undefined
        setProtectionEdited(view?.protectionEdited ?? false)
        setProtectedNodesState(view?.protectedNodes ?? Object.keys(data.coordinations?.find(c => c.definition_id === definition?.id)?.constraints?.nodes ?? {}))
      }
      window.dispatchEvent(new CustomEvent('roleplex:workflow-show', { detail: conversation.id }))
    })
  }
  async function save() {
    if (!target.editable || historyGraph) { setError(errorText.WORKFLOW_HISTORY_READ_ONLY); return false }
    const invalid = draft.definition.graph.nodes.find(node => !node.title.trim() || (draft.definition.graph.runtime_version !== 2 && ['role', 'judge'].includes(node.kind) && (!node.role_id || !node.task.trim())))
    if (invalid) { setError(`请补齐节点“${invalid.title || '未命名'}”的名称、执行角色和任务。`); return }
    return await perform(async () => {
      const written = await workflows.writeGraph(conversation.id, draft.runTarget ? 'run' : 'definition', draft.runTarget ?? draft.definition.id, draft.definition.graph, draft.definition.revision, draft.requestKey, draft.runTarget ? undefined : draft.definition.name)
      const result: WorkflowDefinition = { id: draft.definition.id, name: written.name, revision: written.graph_revision, graph: written.graph }
      if (draft.runTarget) {
        if (current()) { setDraft(value => value.definition === draft.definition ? { ...value, definition: result, dirty: false, requestKey: crypto.randomUUID() } : value); setGraphNotice(written.status === 'pending' ? '修订已保存，将在受影响循环的下一轮边界采用。' : '运行图修订已提交，原有执行记录保留。') }
        return
      }
      if (current()) setData(value => ({ ...value, definitions: [...value.definitions.filter(d => d.id !== result.id), result] }))
      if (current()) setDraft(value => value.definition.id !== draft.definition.id || value.runTarget ? value : ({ ...value,
        definition: value.definition === draft.definition ? result : { ...value.definition, revision: result.revision },
        dirty: value.definition !== draft.definition, requestKey: crypto.randomUUID() }))
    })
  }
  async function start(mode: 'manual' | 'coordinated' = 'manual') {
    if (target.kind !== 'template') { setError(errorText.WORKFLOW_HISTORY_READ_ONLY); return false }
    if (mode === 'coordinated') return coordinate(draft.input || `完成流程：${draft.definition.name}`, conversation.orchestrator_role_id ?? 0, 'execute')
    if (draft.runTarget) { setError('请保存运行图修订；不要把运行图作为模板重新启动。'); return }
    if (draft.dirty || !draft.definition.revision) { setError('请先保存流程，再启动冻结版本。'); return }
    return await perform(async () => {
      const result = await workflows.start(conversation.id, draft.definition, `${mode}-${draft.requestKey}`, draft.input, mode)
      if (current()) { setRunId(result.id); setMode('run'); setData(value => ({ ...value, runs: [result, ...value.runs.filter(r => r.id !== result.id)] })) }
    })
  }
  async function control(body: Parameters<typeof workflows.control>[2]) {
    if (!run || target.kind !== 'run') { setError(errorText.WORKFLOW_HISTORY_READ_ONLY); return false }
    return await perform(async () => {
      const result = await workflows.control(conversation.id, run, body)
      if (current()) setData(value => ({ ...value, runs: value.runs.map(r => r.id === result.id ? result : r) }))
    })
  }
  async function coordinate(goal: string, roleId: number, intent: Coordination['mode'], targetId?: string) {
    if (historyGraph && intent === 'replan') { setError(errorText.WORKFLOW_HISTORY_READ_ONLY); return false }
    if (!user?.is_owner || conversation.type !== 'group' || !conversation.orchestrator_enabled || roleId !== conversation.orchestrator_role_id || !(conversation.orchestrator_revision ?? 0)) { setError('请通过 @ 选择本群当前任命的协调者。'); return false }
    if (!goal.trim()) { setError('请输入规划或调整要求。'); return false }
    if (intent === 'design' && (mode === 'run' || draft.runTarget) && targetId === undefined) { setError('当前查看的是运行，请明确选择流程定义或新草稿；调整运行使用对应入口。'); return false }
    if (conflict && target.editable) { setError('请先核对服务端新版本与本地草稿的冲突。'); return false }
    if (intent === 'replan' && mode === 'edit' && draft.runTarget && draft.dirty) { setError('请先提交本次运行调整，再发送协调要求。'); return false }
    let chosen = targetId === '' ? undefined : targetId ? data.definitions.find(d => d.id === targetId) : draft.definition
    if (intent !== 'replan' && chosen?.id === draft.definition.id && draft.dirty) {
      if (!await save()) return false
      // 保存已将该版本原子提交；本次协调引用刚提交的版本，不使用下一次渲染前的旧状态。
      chosen = { ...draft.definition, revision: draft.definition.revision + 1 }
    }
    return perform(async () => {
      const targetRun = intent === 'replan' ? mode === 'edit' && draft.runTarget ? data.runs.find(r => r.id === draft.runTarget) : run : null
      if (intent !== 'replan' && chosen?.id !== draft.definition.id) await preserveDraft()
      if (intent === 'replan' && !targetRun) throw new Error('missing run')
      const body: Omit<CoordinateRequest, 'request_key'> = { role_id: roleId, mode: intent, goal,
        feedback_mode: intent === 'execute' && automaticFeedback ? 'automatic' : 'manual',
        ...(targetRun ? { run_id: targetRun.id, definition_id: targetRun.definition_id, expected_graph_revision: targetRun.latest_graph_revision ?? 0 }
          : chosen?.revision ? { definition_id: chosen.id, expected_graph_revision: chosen.revision } : {}) }
      if (targetRun && protectionEdited) body.protected_nodes = protectedNodes.filter(id => targetRun.graph.nodes.some(n => n.id === id))
      if (!targetRun && chosen?.id === draft.definition.id) body.protected_nodes = protectedNodes.filter(id => chosen?.graph.nodes.some(n => n.id === id))
      const previous = data.coordinations?.find(c => c.mode === 'design' && c.status === 'completed' && c.definition_id === body.definition_id && c.role_id === roleId)
      if (intent === 'execute' && previous && !data.runs.some(r => r.chain_id === previous.chain_id)) body.continue_session_id = previous.id
      const signature = JSON.stringify(body)
      const requestKey = coordinationRequests.current.get(signature) ?? crypto.randomUUID()
      coordinationRequests.current.set(signature, requestKey)
      const result = await workflows.coordinate(conversation.id, { ...body, request_key: requestKey })
      if (!current()) return
      setCoordinationId(result.id); setHistoryGraph(null)
      setData(value => ({ ...value, coordinations: [result, ...(value.coordinations ?? []).filter(c => c.id !== result.id)] }))
      if (intent !== 'replan') {
        if (!chosen?.revision) setDraft(value => {
          const wasBlank = !draft.definition.revision && !draft.definition.graph.nodes.length
          const untouched = value.definition === draft.definition && !value.dirty
          if (!untouched && !(wasBlank && value.definition.id === draft.definition.id)) return value
          return { definition: { id: result.definition_id, name: value.dirty ? value.definition.name : '协调流程', revision: 0,
            graph: wasBlank && value.dirty ? value.definition.graph : { nodes: [], edges: [], runtime_version: 2 } },
            dirty: wasBlank && value.dirty, input: goal, requestKey: crypto.randomUUID() }
        })
        else if (chosen.id !== draft.definition.id) setDraft({ definition: structuredClone(chosen), dirty: false, input: goal, requestKey: crypto.randomUUID() })
        setMode('edit')
      }
      setOpen(true)
      window.dispatchEvent(new CustomEvent('roleplex:workflow-show', { detail: conversation.id }))
    })
  }
  async function cancelCoordination(value: Coordination) {
    if (historyGraph) { setError(errorText.WORKFLOW_HISTORY_READ_ONLY); return false }
    return perform(async () => { await workflows.cancelCoordination(conversation.id, value) })
  }
  async function reportFeedback(body: Parameters<typeof workflows.reportFeedback>[2]) {
    if (!run || historyGraph || mode !== 'run') return false
    return perform(async () => { await workflows.reportFeedback(conversation.id, run.id, body) })
  }
  async function updateFeedback(fid: string, body: Parameters<typeof workflows.updateFeedback>[3]) {
    if (!run || historyGraph || mode !== 'run') return false
    return perform(async () => { await workflows.updateFeedback(conversation.id, run.id, fid, body) })
  }
  async function selectHistory(revision: string) {
    const ticket = ++historyRequest.current
    setFeedbackFocusId(null)
    if (!revision || !run) { setHistoryGraph(null); return }
    return perform(async () => {
      const value = await workflows.readGraph(conversation.id, 'run', run.id, Number(revision))
      if (current() && ticket === historyRequest.current) { setHistoryGraph({ runId: run.id, revision: value.graph_revision, graph: value.graph }); setSelected(null); setDetailAttempt(null); setOpen(true) }
    })
  }
  async function editRun() {
    if (!run || historyGraph) return false
    return perform(async () => {
      await preserveDraft()
      const records = writer.current ? await readLocalDrafts(writer.current.scope) : null
      const cached = records?.items.find(row => row.draft.runTarget === run.id)
      const value = await workflows.readGraph(conversation.id, 'run', run.id)
      if (current()) { historyRequest.current++; setDraft(cached?.draft.dirty && cached.autoRestore !== false ? structuredClone(cached.draft) : { runTarget: run.id, definition: { id: run.definition_id, name: run.name, revision: value.graph_revision, graph: value.graph }, dirty: false, input: '', requestKey: crypto.randomUUID() }); setMode('edit'); setOpen(true); setHistoryGraph(null) }
    })
  }
  async function acceptRemote() {
    return perform(async () => {
      await preserveDraft()
      const value = await workflows.readGraph(conversation.id, draft.runTarget ? 'run' : 'definition', draft.runTarget ?? draft.definition.id)
      if (current()) setDraft(old => old.definition === draft.definition ? { ...old, definition: { ...old.definition, graph: value.graph, revision: value.graph_revision, name: value.name }, dirty: false, requestKey: crypto.randomUUID() } : old)
    })
  }
  async function forkDraft() {
    return perform(async () => {
      await preserveDraft(true)
      if (!current()) return
      setDraft(value => ({ ...value, runTarget: undefined, definition: { ...value.definition, id: crypto.randomUUID(), revision: 0, name: `${value.definition.name}（草稿副本）` }, dirty: true, requestKey: crypto.randomUUID() }))
      setMode('edit'); setRunId(null); setHistoryGraph(null)
    })
  }

  /** 画布和侧栏共用的定义编辑入口，连线变化才重新计算串行顺序。 */
  function changeGraph(next: WorkflowGraph) {
    next = { ...graph, ...next }
    next.edge_rules = (next.edge_rules ?? []).filter(rule => next.edges.some(([a, b]) => a === rule.source && b === rule.target))
    next.entries = (next.entries ?? []).filter(id => next.nodes.some(node => node.id === id))
    next.presentation = cleanPresentation(next)
    if (mode !== 'edit') return
    if (JSON.stringify(next.edges) === JSON.stringify(graph.edges)) {
      update({ ...draft.definition, graph: next }); return
    }
    const positions = displayPositions(next)
    const positioned = { ...next, nodes: next.nodes.map(n => ({ ...n, position: n.position ?? positions[n.id] })) }
    const ordered = graph.runtime_version === 2 ? null : serialOrder(positioned)
    if (ordered) {
      const nodes = ordered.map((n, i) => ({ ...n, inputs: n.inputs.filter(id => ordered.slice(0, i).some(prior => prior.id === id)) }))
      if (nodes.some((n, i) => n.inputs.length !== ordered[i].inputs.length)) setGraphNotice('执行顺序已按连线更新，不再属于上游的结果引用已移除。')
      update({ ...draft.definition, graph: { ...positioned, nodes, edges: nodes.slice(1).map((n, i) => [nodes[i].id, n.id]) } })
    } else update({ ...draft.definition, graph: positioned })
  }
  /** 在指定节点后插入任务并承接其出边；追加按钮默认接在最后一个节点后。 */
  function addNode(kind: import('../../api/workflows').WorkflowNode['kind'], afterId?: string) {
    if (mode !== 'edit') return null
    const index = afterId ? graph.nodes.findIndex(node => node.id === afterId) : graph.nodes.length - 1
    if (afterId && index < 0) return null
    const source = graph.nodes[index]
    const id = crypto.randomUUID()
    const available = useAppStore.getState().roles.filter(role => conversation.role_ids.includes(role.id) && role.active)
    const role = available.find(role => role.id === source?.role_id) ?? available[0]
    const visiblePositions = displayPositions(graph)
    const position = source ? { x: visiblePositions[source.id].x + 320, y: visiblePositions[source.id].y } : defaultPosition(0)
    const nodes = graph.nodes.map((node, i) => {
      const current = visiblePositions[node.id] ?? defaultPosition(i)
      // 旧图的列间距可能小于 320；按来源列让位，避免新增任务覆盖原本紧邻的后继。
      return { ...node, position: source && current.x > visiblePositions[source.id].x ? { ...current, x: current.x + 320 } : current }
    })
    nodes.splice(index + 1, 0, { id, kind, title: ({ role: '角色任务', approval: '人工确认', join: '结果汇合', condition: '条件选择', judge: '模型判断' })[kind], role_id: kind === 'role' ? role?.id ?? null : null,
      task: '', expected_output: '', inputs: source ? [source.id] : [], position,
      condition: ['condition', 'judge'].includes(kind) ? { sources: kind === 'judge' ? ['$self'] : [], key: 'approved', operator: 'eq', value: true, aggregate: 'all' } : null })
    const edges: [string, string][] = source
      ? [...graph.edges.map(([a, b]): [string, string] => [a === source.id ? id : a, b]), [source.id, id]] : []
    changeGraph({ ...graph, runtime_version: ['join', 'condition', 'judge'].includes(kind) ? 2 : graph.runtime_version, nodes, edges })
    setSelected(id); setSelectedEdge(''); setOpen(true)
    return id
  }
  function editDefinition() {
    const definition = data.definitions.find(d => d.id === target.templateId)
    return definition ? choose(definition) : false
  }
  async function selectRun(id: string) {
    if (!data.runs.some(row => row.id === id)) return false
    return perform(async () => {
      await preserveDraft()
      if (!current()) return
      historyRequest.current++; setHistoryGraph(null); setCoordinationId(null); setRunId(id); setMode('run'); setOpen(true)
      setProtectedNodesState(Object.keys(data.runs.find(r => r.id === id)?.constraints?.nodes ?? {})); setProtectionEdited(false)
    })
  }
  function resumeDraft() {
    if (pending.current || !user?.is_owner) return
    historyRequest.current++; setHistoryGraph(null); setMode('edit'); setRunId(draft.runTarget ?? null); setOpen(true)
  }
  return { localReady, localStatus, localNotice, localCopies, retryLocal, exportLocal, restoreLocal, removeLocal, protectedNodes, setProtectedNodes: (values: string[]) => { setProtectedNodesState(values); setProtectionEdited(true) }, conflict, remoteDefinition, remoteRun, acceptRemote, forkDraft, coordinate, cancelCoordination, editRun, historyGraph, selectHistory, coordinationId, detailAttempt, selectAttempt: setDetailAttempt, conversation, draft, data, run, graph, selected, selectedEdge, graphNotice, addNode, changeGraph, editDefinition,
    target, inspectorPage, revealInspector, setInspectorPage, coordinationNodeId, startMode, setStartMode, resumeDraft,
    coordinationInput: coordinationInputs[coordinationInputKey] ?? (target.kind === 'template' ? draft.input : ''),
    setCoordinationInput: (input: string) => {
      setCoordinationInputs(values => ({ ...values, [coordinationInputKey]: input }))
      if (target.kind === 'template' && !coordinationNodeId) setDraft(value => ({ ...value, input, requestKey: crypto.randomUUID() }))
    },
    requestCoordination: (nodeId: string | null = null) => { setCoordinationNodeId(nodeId); revealInspector('coordination') },
    feedbackFocusId, focusFeedback: (id: string) => {
      const item = run?.feedback?.find(item => item.id === id)
      if (!item) return
      setFeedbackFocusId(id); setSelected(item.node_id); setSelectedEdge(''); setOpen(true)
      revealInspector('context')
    },
    selectNode: (id: string | null) => { setSelected(id); setFeedbackFocusId(null); setInspectorPage('context'); if (id) { setSelectedEdge(''); revealInspector('context', false) } },
    selectEdge: (id: string) => { setSelectedEdge(id); if (id) { setSelected(null); setFeedbackFocusId(null); revealInspector('context', false) } }, mode, setMode, open, setOpen, busy, error, setError, refresh,
    update, choose, save, start, control, automaticFeedback, setAutomaticFeedback, reportFeedback, updateFeedback,
    selectRun,
    input: (input: string) => setDraft(value => ({ ...value, input, requestKey: crypto.randomUUID() })),
    newRun: async () => {
      const definition = data.definitions.find(d => d.id === run?.definition_id)
      if (!definition || !await choose(definition)) return false
      setDraft(value => ({ ...value, requestKey: crypto.randomUUID() }))
      return true
    },
  }
}
const Context = createContext<ReturnType<typeof useController> | null>(null)
export function WorkflowProvider({ conversation, children }: { conversation: Conversation; children: ReactNode }) {
  const controller = useController(conversation)
  return <Context.Provider value={controller}>{children}</Context.Provider>
}
export function useWorkflow() {
  const value = useContext(Context)
  if (!value) throw new Error('WorkflowProvider missing')
  return value
}
export const statusLabel = (status: string) => ({ pending: '待执行', queued: '排队中', running: '执行中', waiting: '等待人工确认', waiting_feedback: '等待反馈处置', stopping: '正在停止', stopped: '已停止', failed: '失败', interrupted: '已中断', blocked: '受阻', skipped: '已跳过', dormant: '历史轮次', active: '执行中', completed: '本次执行结束' }[status] ?? status)
