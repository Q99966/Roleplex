import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { getAuthEpoch, type Conversation } from '../../api/client'
import { workflows, type WorkflowDefinition, type WorkflowList, type WorkflowRun, type WorkflowGraph } from '../../api/workflows'
import { defaultPosition, serialOrder } from './workflow-layout'
import { useAppStore } from '../../store/app'

type Draft = { definition: WorkflowDefinition; dirty: boolean; input: string; requestKey: string }
const drafts = new Map<string, Draft>()
/** 关闭页面时检查整个认证会话的草稿；切到其他会话或主页也不能漏掉旧编辑。 */
function warnUnsavedDrafts(event: BeforeUnloadEvent) {
  if ([...drafts].some(([key, draft]) => key.startsWith(`${getAuthEpoch()}:`) && draft.dirty)) {
    event.preventDefault(); event.returnValue = ''
  }
}
window.addEventListener('beforeunload', warnUnsavedDrafts)
if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener('beforeunload', warnUnsavedDrafts))
const errorText: Record<string, string> = {
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
  const blank = (): Draft => ({ definition: { id: crypto.randomUUID(), name: '新工作流', revision: 0, graph: { nodes: [], edges: [] } }, dirty: false, input: '', requestKey: crypto.randomUUID() })
  const [draft, setDraft] = useState<Draft>(() => drafts.get(key) ?? blank())
  const [data, setData] = useState<WorkflowList>({ definitions: [], runs: [] })
  const [runId, setRunId] = useState<string | null>(null)
  const [mode, setMode] = useState<'edit' | 'run'>('edit')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState('')
  const [graphNotice, setGraphNotice] = useState('')
  const alive = useRef(true)
  const fetching = useRef(false)
  const pending = useRef(false)
  const current = () => alive.current && epoch === getAuthEpoch()
  const run = data.runs.find(row => row.id === runId) ?? null
  const graph = mode === 'run' && run ? run.graph : draft.definition.graph
  useEffect(() => { setSelected(null); setSelectedEdge(''); setGraphNotice('') }, [mode, runId, draft.definition.id])

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
        return { definitions: merge(result.definitions, previous.definitions), runs: merge(result.runs, previous.runs) }
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
  useEffect(() => {
    drafts.set(key, draft)
    // 登出后的旧身份草稿不留在下一身份会话中。
    for (const stored of drafts.keys()) if (!stored.startsWith(`${epoch}:`)) drafts.delete(stored)
  }, [key, draft])


  async function perform(action: () => Promise<void>) {
    if (pending.current) return false
    pending.current = true; setBusy(true); setError('')
    let succeeded = false
    try { await action(); succeeded = true }
    catch (cause) { if (current()) setError(errorText[(cause as { code?: string })?.code ?? ''] ?? '操作未完成，请检查节点、依赖和角色权限；刷新核对结果后再试。') }
    finally { pending.current = false; if (current()) { setBusy(false); void refresh() } }
    return succeeded && current()
  }
  function update(definition: WorkflowDefinition) { setDraft(value => ({ ...value, definition, dirty: true, requestKey: crypto.randomUUID() })) }
  function choose(definition?: WorkflowDefinition) {
    if (draft.dirty && !confirm('当前流程有未保存的编辑，放弃这些编辑并切换吗？')) return false
    setDraft(definition ? { definition: structuredClone(definition), dirty: false, input: '', requestKey: crypto.randomUUID() } : blank())
    setMode('edit'); setError(''); setOpen(true)
    return true
  }
  async function save() {
    const invalid = draft.definition.graph.nodes.find(node => !node.title.trim() || (node.kind === 'role' && (!node.role_id || !node.task.trim())))
    if (invalid) { setError(`请补齐节点“${invalid.title || '未命名'}”的名称、执行角色和任务。`); return }
    return await perform(async () => {
      const result = await workflows.save(conversation.id, draft.definition)
      if (current()) setData(value => ({ ...value, definitions: [...value.definitions.filter(d => d.id !== result.id), result] }))
      if (current()) setDraft(value => ({ ...value,
        definition: value.definition === draft.definition ? result : { ...value.definition, revision: result.revision },
        dirty: value.definition !== draft.definition, requestKey: crypto.randomUUID() }))
    })
  }
  async function start() {
    if (draft.dirty || !draft.definition.revision) { setError('请先保存流程，再启动冻结版本。'); return }
    return await perform(async () => {
      const result = await workflows.start(conversation.id, draft.definition, draft.requestKey, draft.input)
      if (current()) { setRunId(result.id); setMode('run'); setData(value => ({ ...value, runs: [result, ...value.runs.filter(r => r.id !== result.id)] })) }
    })
  }
  async function control(body: Parameters<typeof workflows.control>[2]) {
    if (!run) return
    return await perform(async () => {
      const result = await workflows.control(conversation.id, run, body)
      if (current()) setData(value => ({ ...value, runs: value.runs.map(r => r.id === result.id ? result : r) }))
    })
  }
  /** 画布和侧栏共用的定义编辑入口，连线变化才重新计算串行顺序。 */
  function changeGraph(next: WorkflowGraph) {
    if (mode !== 'edit') return
    if (JSON.stringify(next.edges) === JSON.stringify(graph.edges)) {
      update({ ...draft.definition, graph: next }); return
    }
    const positioned = { ...next, nodes: next.nodes.map((n, i) => ({ ...n, position: n.position ?? defaultPosition(i) })) }
    const ordered = serialOrder(positioned)
    if (ordered) {
      const nodes = ordered.map((n, i) => ({ ...n, inputs: n.inputs.filter(id => ordered.slice(0, i).some(prior => prior.id === id)) }))
      if (nodes.some((n, i) => n.inputs.length !== ordered[i].inputs.length)) setGraphNotice('执行顺序已按连线更新，不再属于上游的结果引用已移除。')
      update({ ...draft.definition, graph: { nodes, edges: nodes.slice(1).map((n, i) => [nodes[i].id, n.id]) } })
    } else update({ ...draft.definition, graph: positioned })
  }
  /** 在指定节点后插入任务并承接其出边；追加按钮默认接在最后一个节点后。 */
  function addNode(kind: 'role' | 'approval', afterId?: string) {
    if (mode !== 'edit') return null
    const index = afterId ? graph.nodes.findIndex(node => node.id === afterId) : graph.nodes.length - 1
    if (afterId && index < 0) return null
    const source = graph.nodes[index]
    const id = crypto.randomUUID()
    const available = useAppStore.getState().roles.filter(role => conversation.role_ids.includes(role.id) && role.active)
    const role = available.find(role => role.id === source?.role_id) ?? available[0]
    const position = source ? { x: (source.position ?? defaultPosition(index)).x + 280, y: (source.position ?? defaultPosition(index)).y } : defaultPosition(0)
    const nodes = graph.nodes.map((node, i) => {
      const current = node.position ?? defaultPosition(i)
      return { ...node, position: current.x >= position.x ? { ...current, x: current.x + 280 } : current }
    })
    nodes.splice(index + 1, 0, { id, kind, title: kind === 'role' ? '角色任务' : '人工确认', role_id: kind === 'role' ? role?.id ?? null : null,
      task: '', expected_output: '', inputs: source ? [source.id] : [], position })
    const edges: [string, string][] = source
      ? [...graph.edges.map(([a, b]): [string, string] => [a === source.id ? id : a, b]), [source.id, id]] : []
    changeGraph({ nodes, edges })
    setSelected(id); setSelectedEdge(''); setOpen(true)
    return id
  }
  function editDefinition() {
    if (run?.definition_id === draft.definition.id) { setMode('edit'); setOpen(true); return true }
    const definition = data.definitions.find(d => d.id === run?.definition_id)
    return definition ? choose(definition) : false
  }
  return { conversation, draft, data, run, graph, selected, selectedEdge, graphNotice, addNode, changeGraph, editDefinition,
    selectNode: (id: string | null) => { setSelected(id); if (id) setSelectedEdge('') },
    selectEdge: (id: string) => { setSelectedEdge(id); if (id) setSelected(null) }, mode, setMode, open, setOpen, busy, error, setError, refresh,
    update, choose, save, start, control,
    selectRun: (id: string) => { setRunId(id || null); setMode(id ? 'run' : 'edit') },
    input: (input: string) => setDraft(value => ({ ...value, input, requestKey: crypto.randomUUID() })),
    newRun: () => {
      const definition = data.definitions.find(d => d.id === run?.definition_id)
      return definition ? choose(definition) : false
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
export const statusLabel = (status: string) => ({ pending: '待执行', queued: '排队中', running: '执行中', waiting: '等待人工确认', stopping: '正在停止', stopped: '已停止', failed: '失败', interrupted: '已中断', blocked: '受阻', completed: '本次执行结束' }[status] ?? status)
