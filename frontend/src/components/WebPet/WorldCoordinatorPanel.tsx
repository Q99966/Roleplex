import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, ChevronDown, MessageSquare, PanelRight, Send, Square, X } from 'lucide-react'
import type { Role } from '../../api/client'
import { getAuthEpoch } from '../../api/client'
import { worldOrchestrator, type WorldOrchestrator, type WorldTask } from '../../api/worldOrchestrator'
import { useAppStore } from '../../store/app'
import { coordinatorSession, type Detail, type SendMode } from './coordinatorSession'
import { ConversationChatProvider } from '../../store/conversationChat'
import { MessageParts } from '../MessageParts'
import { messageAuthor, CommunicationAddress } from '../MessageCommunication'
import { RoleUsagePanel } from '../RoleUsagePanel'
import { WorldMemories } from './WorldMemories'

const ContextPanel = lazy(() => import('../context/ConversationContext').then(module => ({ default: module.ConversationContextPanel })))
const TaskFlow = lazy(() => import('./WorldTaskFlow').then(module => ({ default: module.WorldTaskFlow })))
const statusLabel: Record<string, string> = { queued: '排队中', running: '执行中', waiting: '待处理', stopping: '正在停止', stopped: '已停止', completed: '已完成', failed: '未完成', interrupted: '已中断' }
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-indigo-400'

/** 一个 World/Owner 独立控制器，复用消息和领域事件，关闭视图只释放订阅。 */
export function WorldCoordinatorPanel({ onClose, onEditRole }: { onClose: () => void; onEditRole: (role: Role) => void }) {
  const { user, worldName } = useAppStore()
  if (!user?.is_owner) return null
  const scope = `${getAuthEpoch()}:${worldName}:${user.id}`
  return <Controller key={scope} scope={scope} onClose={onClose} onEditRole={onEditRole} />
}

function Controller({ scope, onClose, onEditRole }: { scope: string; onClose: () => void; onEditRole: (role: Role) => void }) {
  const { roles, roleDirectory, conversations, worldName, user } = useAppStore()
  const saved = useMemo(() => coordinatorSession(scope), [scope])
  const [value, setValue] = useState<WorldOrchestrator | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [text, setText] = useState(saved.draft), [details, setDetails] = useState<Detail>(saved.detail)
  const [tasks, setTasks] = useState<WorldTask[]>([]), [taskId, setTaskId] = useState<string | null>(saved.taskId)
  const [view, setView] = useState<'chat' | 'flow'>(saved.view), [selected, setSelected] = useState<string | null>(saved.selected)
  const [sendMode, setSendMode] = useState<SendMode>(saved.mode)
  const [binding, setBinding] = useState(false)
  const useScopedChat = saved.store
  const chat = useScopedChat()
  const panel = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null), messages = useRef<HTMLDivElement>(null)
  const scrollAtBottom = useRef(saved.scroll === null), restoreScroll = useRef(saved.scroll), mounted = useRef(true)
  const cid = value?.conversation?.id
  const epoch = getAuthEpoch()

  useEffect(() => {
    // 停止按钮消失后焦点可能回到 body，Esc 仍应能收起当前模态区域。
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.stopPropagation()
      if (details) setDetails(null); else if (binding) setBinding(false); else onClose()
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [details, binding, onClose])

  useEffect(() => {
    mounted.current = true
    const previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    const controller = new AbortController()
    let fetching = false
    const load = async () => {
      if (fetching) return
      fetching = true
      try {
        const [next, runs] = await Promise.all([worldOrchestrator.read(controller.signal), worldOrchestrator.tasks(controller.signal)])
        if (!controller.signal.aborted && epoch === getAuthEpoch()) { setValue(next); setTasks(runs.items); setError('') }
      } catch { if (!controller.signal.aborted && epoch === getAuthEpoch()) setError('世界协调状态读取失败，请稍后重试。') }
      finally { fetching = false }
    }
    void load()
    const timer = window.setInterval(() => void load(), 3000)
    return () => { mounted.current = false; controller.abort(); window.clearInterval(timer); if (previous?.isConnected) previous.focus() }
  }, [epoch])
  useEffect(() => {
    const token = localStorage.getItem('roleplex_token')
    if (!cid || !token) return
    const state = useScopedChat.getState()
    state.startSession(token); void state.openConversation(cid)
    return () => state.endSession()
  }, [cid, useScopedChat])
  useEffect(() => { Object.assign(saved, { draft: text, detail: details, taskId, view, selected, mode: sendMode }) }, [saved, text, details, taskId, view, selected, sendMode])
  useEffect(() => {
    if (restoreScroll.current !== null && chat.messages.length && messages.current) { messages.current.scrollTop = restoreScroll.current; restoreScroll.current = null; return }
    if (scrollAtBottom.current && messages.current) messages.current.scrollTop = messages.current.scrollHeight
  }, [chat.messages])

  async function appoint(role: string) {
    if (!value || busy || !role) return
    setBusy(true); setError('')
    try {
      const next = await worldOrchestrator.importRole(Number(role), value.revision)
      await useAppStore.getState().loadWorkspace()
      if (mounted.current && epoch === getAuthEpoch()) { setValue(next); setBinding(false); input.current?.focus() }
    } catch { if (mounted.current && epoch === getAuthEpoch()) setError('配置导入未完成，来源或版本可能已变化。当前历史和输入保留。') }
    finally { if (mounted.current && epoch === getAuthEpoch()) setBusy(false) }
  }
  async function send() {
    if (!value?.available || !text.trim()) return
    if (sendMode === 'continue' && !activeTask) { setError('请先选择尚未结束的任务。'); return }
    if (await chat.sendMessage(text, [], { world_task_mode: sendMode === 'chat' ? 'chat' : 'execute', expected_appointment_revision: value.revision,
      ...(sendMode === 'continue' && task ? { world_task_id: task.id, expected_task_revision: task.revision } : {}) })) {
      setText(''); scrollAtBottom.current = true; input.current?.focus()
      if (sendMode === 'execute') { setTaskId(null); setSelected(null); setDetails('task') }
    }
  }
  const currentRole = value?.profile ?? (value?.role_id ? roleDirectory[value.role_id] : undefined)
  const task = tasks.find(row => row.id === taskId) ?? tasks[0]
  const child = task?.children.find(row => row.id === selected)
  const activeTask = task && !['completed', 'failed', 'stopped', 'interrupted'].includes(task.status)
  const feedback = task?.children.flatMap(row => row.feedback.map(item => ({ ...item, child: row }))) ?? []
  async function stopTask() {
    if (!task) return
    try { const row = await worldOrchestrator.stopTask(task.id, task.revision); if (mounted.current && epoch === getAuthEpoch()) setTasks(items => items.map(item => item.id === row.id ? row : item)) }
    catch { if (mounted.current && epoch === getAuthEpoch()) setError('任务版本已变化或停止尚未确认，请核对最新状态。') }
  }
  async function stopChild() {
    if (!task || !child) return
    try { await worldOrchestrator.stopChild(task.id, child.id, child.revision); const next = await worldOrchestrator.tasks(); if (mounted.current && epoch === getAuthEpoch()) setTasks(next.items) }
    catch { if (mounted.current && epoch === getAuthEpoch()) setError('子任务版本已变化或停止尚未确认，请核对最新状态。') }
  }
  function openGroup(cid: number, run: string | null) {
    onClose(); window.location.hash = `#/workspace/conversation/${cid}${run ? `?workflow_run=${encodeURIComponent(run)}` : ''}`
  }
  async function enableManager() {
    if (!value || busy) return
    setBusy(true)
    try { const next = await worldOrchestrator.enable(!value.enabled, value.revision); await useAppStore.getState().loadWorkspace(); if (mounted.current && epoch === getAuthEpoch()) setValue(next) }
    catch { if (mounted.current && epoch === getAuthEpoch()) setError('启停未确认，请核对最新状态。') }
    finally { if (mounted.current && epoch === getAuthEpoch()) setBusy(false) }
  }
  return <div className="fixed inset-0 z-[45] bg-slate-950/30 p-0 backdrop-blur-sm sm:p-5" onPointerDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="世界协调面板" className="mx-auto flex h-full max-w-[1440px] flex-col overflow-hidden rounded-none border border-slate-700 bg-canvas shadow-2xl outline-none sm:rounded-2xl"
      onKeyDown={event => {
        if (event.key === 'Tab') {
          const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]') ?? []).filter(item => item.getClientRects().length)
          const first = nodes[0], last = nodes.at(-1)
          if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
        }
      }}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-panel px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3"><span className="rounded-xl bg-indigo-100 p-2 text-indigo-600"><Bot size={22} /></span><div className="min-w-0">
          <h2 className="truncate text-sm font-semibold">{worldName} · 世界协调</h2>
          <button className="mt-1 flex items-center gap-1 text-xs text-slate-500" aria-label="世界管理者设置" aria-expanded={binding || !value?.available} onClick={() => setBinding(v => !v)}>{value?.role_name ?? '世界管理者'}<ChevronDown size={12} /></button>
        </div></div>
        <div className="flex items-center gap-2">
          <span role="status" className="hidden text-xs text-slate-500 sm:inline">{activeTask ? statusLabel[task.status] ?? task.status : chat.generating ? '正在回复' : value?.available ? '等待你的要求' : value?.status === 'disabled' ? '世界管理已停用' : '待配置模型'}</span>
          {activeTask ? <button className={`${button} text-red-600`} disabled={task.status === 'stopping'} onClick={() => void stopTask()}><Square size={12} className="mr-1 inline" />停止本次任务</button>
            : chat.generating && <button className={`${button} text-red-600`} onClick={() => void chat.stopGeneration()}><Square size={12} className="mr-1 inline" />停止本次回复</button>}
          {value?.conversation && <button className={button} aria-expanded={details !== null} onClick={() => setDetails(details ? null : 'context')}><PanelRight size={14} className="mr-1 inline" />详情</button>}
          <button className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="收起世界协调面板" onClick={onClose}><X size={18} /></button>
        </div>
      </header>
      {(binding || !value?.available) && <section aria-label="世界管理者配置" className="border-b border-slate-800 bg-panel px-4 py-4 sm:px-6">
        <div className="mb-3 flex flex-wrap items-center gap-2"><span className="text-xs text-slate-500">{value?.status === 'disabled' ? '世界管理已停用' : value?.available ? '固定岗位 · 已就绪' : '请选择模型或导入角色配置'}</span>
          {currentRole && <button className={button} onClick={() => { onClose(); onEditRole(currentRole) }}>配置管理者模型与人设</button>}
          {value && <button className={button} disabled={busy} onClick={() => void enableManager()}>{value.enabled ? '停用世界管理' : '启用世界管理'}</button>}</div>
        <label className="block max-w-md text-xs">从普通角色复制配置<select aria-label="导入世界管理者配置" value="" disabled={!value || busy}
          onChange={event => void appoint(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-700 bg-canvas p-2.5 outline-none focus:border-indigo-400">
          <option value="">选择配置来源</option>{roles.filter(role => role.active).map(role => <option key={role.id} value={role.id}>{role.name} · {role.model_name}</option>)}
        </select></label><p className="mt-2 text-xs text-slate-500">管理者身份、对话与记忆属于当前世界。导入会复制模型和人设，原角色保持独立；更新执行配置后旧协调任务停止。</p>
      </section>}
      {(error || chat.error) && <p role="alert" className="border-b border-red-200 px-4 py-2 text-xs text-red-600">{error || chat.error}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-panel px-4 py-2">
        <div role="group" aria-label="世界协调视图" className="flex gap-1"><button className={button} aria-pressed={view === 'chat'} onClick={() => setView('chat')}>对话</button>
          <button className={button} aria-pressed={view === 'flow'} disabled={!task} onClick={() => { setView('flow'); setDetails('task') }}>流程</button></div>
        <div className="flex flex-wrap items-center gap-2">{!!tasks.length && <select aria-label="当前世界任务" className="max-w-64 rounded-lg border border-slate-700 bg-canvas p-2 text-xs" value={task?.id ?? ''}
          onChange={event => { setTaskId(event.target.value); setSelected(null); setDetails('task') }}>{tasks.map(row => <option key={row.id} value={row.id}>{row.title.slice(0, 38)} · {statusLabel[row.status] ?? row.status}</option>)}</select>}
          <button className={button} onClick={() => setDetails('feedback')}>反馈{feedback.length ? ` ${feedback.length}` : ''}</button>
          <button className={button} onClick={() => setDetails('memory')}>世界记忆</button>
        </div>
      </div>
      <ConversationChatProvider store={useScopedChat} draft={text}>
        <div className="relative flex min-h-0 flex-1">
          <section aria-label="世界协调对话" className={`flex min-w-0 flex-1 flex-col ${view === 'flow' ? 'hidden' : ''}`}>
            <div ref={messages} onScroll={() => { const el = messages.current!; scrollAtBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; saved.scroll = scrollAtBottom.current ? null : el.scrollTop }} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8">
              {!chat.messages.length && <div className="mx-auto mt-12 max-w-md text-center"><MessageSquare size={30} className="mx-auto text-indigo-400" /><h3 className="mt-4 font-semibold">从这个世界的目标开始</h3><p className="mt-2 text-sm leading-relaxed text-slate-500">配置管理者模型后，在这里讨论要求、查看回复和执行记录。收起面板后，已经开始的回复会继续。</p></div>}
              {chat.nextCursor && <button className={`${button} mx-auto mb-4 block`} disabled={chat.loadingOlder} onClick={() => void chat.loadOlder()}>读取更早的协调记录</button>}
              <div className="mx-auto max-w-4xl space-y-5">{chat.messages.map(message => <article key={message.id} aria-label={`协调消息 ${message.id}`} className={`rounded-2xl border p-4 ${message.sender_type === 'user' ? 'ml-4 border-indigo-100 bg-indigo-50 sm:ml-12' : 'mr-4 border-slate-800 bg-panel sm:mr-12'}`}>
                <p className="mb-2 text-xs font-medium text-slate-500">{messageAuthor(message, user, roleDirectory)}{message.status === 'generating' ? ' · 回复中' : message.status === 'error' ? ' · 执行未完成' : message.status === 'stopped' ? ' · 已停止' : ''}</p>
                <CommunicationAddress message={message} />
                <div className="break-words whitespace-pre-wrap text-sm"><MessageParts message={message} isOwner /></div>
              </article>)}</div>
            </div>
            <form className="border-t border-slate-800 bg-panel p-4 sm:px-8" onSubmit={event => { event.preventDefault(); void send() }}>
              <div className="mx-auto mb-2 flex max-w-4xl flex-wrap items-center gap-2 text-xs text-slate-500"><label>本次要求<select aria-label="世界协调发送方式" className="ml-2 rounded-lg border border-slate-700 bg-canvas p-1.5" value={sendMode} onChange={event => setSendMode(event.target.value as SendMode)}>
                <option value="chat">对话与查看</option><option value="execute">执行世界任务</option><option value="continue" disabled={!activeTask}>补充当前任务</option></select></label><span>{sendMode === 'continue' ? '在协调回合空闲后补充要求，沿用当前任务与剩余额度。' : sendMode === 'execute' ? '允许在当前世界委派；子任务共用本次决策额度。' : '查看与讨论，不授予新的委派操作。'}</span></div>
              <div className="mx-auto flex max-w-4xl items-end gap-3 rounded-2xl border border-slate-700 bg-canvas p-3 focus-within:border-indigo-400">
                <textarea ref={input} aria-label="世界协调输入" rows={2} value={text} onChange={event => setText(event.target.value)} disabled={!value?.available}
                  placeholder={value?.available ? '说说你希望这个世界完成什么…' : '先配置并启用世界管理者'} className="min-h-14 flex-1 resize-none bg-transparent text-sm outline-none" />
                <button aria-label="发送世界协调消息" className="rounded-xl bg-indigo-600 p-3 text-white disabled:opacity-40" disabled={!value?.available || !text.trim() || chat.sending || chat.subscription !== 'ready'}><Send size={16} /></button>
              </div>
            </form>
          </section>
          {view === 'flow' && task && <Suspense fallback={<p role="status">正在加载世界任务图…</p>}><TaskFlow key={task.id} task={task} selected={selected} viewport={saved.viewports[task.id]} onViewport={viewport => { saved.viewports[task.id] = viewport }} conversationNames={Object.fromEntries(conversations.map(c => [c.id, c.title]))}
            onSelect={id => { setSelected(id); setDetails('task') }} /></Suspense>}
          {details && value?.conversation && <aside aria-label="世界协调详情" className="absolute inset-0 z-10 overflow-y-auto border-l border-slate-800 bg-panel p-4 sm:relative sm:w-[360px] sm:shrink-0">
            <div className="mb-4 flex items-center justify-between gap-2"><select aria-label="协调详情内容" className="rounded-lg border border-slate-700 bg-canvas p-2 text-xs" value={details} onChange={event => setDetails(event.target.value as typeof details)}>
              <option value="task">任务详情</option><option value="feedback">反馈</option><option value="context">上下文</option><option value="usage">用量</option><option value="memory">世界记忆</option></select><button aria-label="关闭协调详情" className="p-2" onClick={() => setDetails(null)}><X size={15} /></button></div>
            {details === 'context' ? <Suspense fallback={<p role="status">正在加载上下文…</p>}><p className="mb-3 text-xs leading-relaxed text-slate-500">预览按对话模式计算；执行任务会额外计入委派工具和任务资料，以实际调用占用为准。</p><ContextPanel conversation={value.conversation} onEditRole={role => { onClose(); onEditRole(role) }} /></Suspense>
              : details === 'memory' ? <WorldMemories />
              : details === 'usage' ? <div className="space-y-4">{task && <section aria-label="世界任务用量" className="space-y-2 text-xs"><h3 className="font-semibold">当前任务及派生执行</h3>
                <p>已用决策 {task.used_decisions} / {task.decision_limit ?? '不限'}</p><p>已记录模型调用 {task.usage.recorded_calls} 次</p>
                <p>厂商输入 {task.usage.metrics.input_tokens.total?.toLocaleString() ?? '未知'} · 输出 {task.usage.metrics.output_tokens.total?.toLocaleString() ?? '未知'} Token</p>
                <p className="text-slate-500">按原始调用记录汇总，子任务不会重复计量。缺失用量保持未知。</p></section>}
                {currentRole && <RoleUsagePanel conversationId={value.conversation.id} roleId={currentRole.id} />}</div>
              : details === 'feedback' ? <section aria-label="世界任务反馈" className="space-y-3 text-xs"><h3 className="font-semibold">待处理反馈</h3>{!feedback.length && <p className="text-slate-500">当前任务没有未解决的群反馈。</p>}
                {feedback.map(item => <article key={item.id} className="space-y-2 rounded-xl border border-slate-700 p-3"><p>{item.summary}</p><p className="text-slate-500">{item.status} · {item.blocking ? '阻塞后续步骤' : '建议'} · v{item.revision}</p>
                  {item.child.conversation_id && <button className={button} onClick={() => openGroup(item.child.conversation_id!, item.child.run_id)}>进入群流程处理</button>}</article>)}</section>
              : <section aria-label="世界任务详情" className="space-y-3 text-xs">{task ? <>
                <h3 className="font-semibold">{child ? conversations.find(c => c.id === child.conversation_id)?.title ?? '子任务' : task.title}</h3>
                <p>{statusLabel[child?.status ?? task.status] ?? child?.status ?? task.status}</p>
                {(child?.error_code ?? task.error_code) && <p role="alert" className="text-amber-700">{child?.error_code ?? task.error_code}</p>}
                {task.summary && !child && <p className="whitespace-pre-wrap leading-relaxed">{task.summary}</p>}
                {!child && <div className="space-y-2">{task.children.map(row => <button key={row.id} className={`${button} block w-full text-left`} onClick={() => { setSelected(row.id); setView('flow') }}>{row.conversation_id ? conversations.find(c => c.id === row.conversation_id)?.title ?? `会话 #${row.conversation_id}` : '世界记忆'} · {statusLabel[row.status] ?? row.status}</button>)}</div>}
                {child && !child.available && <p className="text-amber-700">来源会话已不可访问；保留执行身份，隐藏其内容。</p>}
                {child?.conversation_id && child.available && <button className={button} onClick={() => openGroup(child.conversation_id!, child.run_id)}>打开群流程</button>}
                {child && !['completed', 'failed', 'stopped', 'interrupted'].includes(child.status) && <button className={`${button} text-red-600`} onClick={() => void stopChild()}>停止这个子任务</button>}
                {child?.results.map(row => <details key={row.attempt_id} className="rounded-xl border border-slate-800 p-3"><summary>{row.node_id} · {statusLabel[row.status] ?? row.status}</summary><pre className="mt-2 whitespace-pre-wrap break-words text-[11px]">{JSON.stringify(row.result, null, 2)}</pre></details>)}
              </> : <p className="text-slate-500">选择“执行世界任务”发送目标后，可在这里查看真实任务与派生进度。</p>}</section>}
          </aside>}
        </div>
      </ConversationChatProvider>
    </div>
  </div>
}
