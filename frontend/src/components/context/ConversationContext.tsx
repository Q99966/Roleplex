import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getAuthEpoch, type Conversation, type Role } from '../../api/client'
import { conversationContext, type ContextPage, type ContextPreview } from '../../api/context'
import { useAppStore } from '../../store/app'
import { useChatStore } from '../../store/chat'
import { useContextDraft } from '../../store/contextDraft'
import { ConversationPromptSettings } from '../prompts/PromptSettings'
import { CompressionPanel } from './CompressionPanel'
import { MemoryPanel } from './MemoryPanel'
import { SummaryBody } from './SummaryBody'

const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-indigo-400'
const card = 'rounded-xl border border-slate-800 bg-panel p-3'
const number = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString()
const reasons: Record<string, string> = { generating: '生成中，尚未纳入', failed: '失败片段保留在原消息', interrupted: '中断片段保留在原消息', empty: '没有可用正文', tool_pending: '工具尚未收口' }
const parts: Record<string, string> = { system: '规则、角色与技能', tools: '可用工具定义', current: '本次消息', history: '已选会话历史', interruption: '中断执行事实', summary: '已采用历史摘要' }
type ContextTab = 'usage' | 'material' | 'prompts' | 'compression' | 'memory'
const viewPreferences = new Map<string, { tab: ContextTab; roleId: number }>()

/** 共享材料、角色输入和提示词分开查看，草稿只预估，不触发执行。 */
export function ConversationContextPanel({ conversation, onEditRole }: { conversation: Conversation; onEditRole: (role: Role) => void }) {
  const { user, worldName } = useAppStore()
  const epoch = getAuthEpoch()
  useEffect(() => { for (const key of viewPreferences.keys()) if (!key.startsWith(`${epoch}:`)) viewPreferences.delete(key) }, [epoch])
  if (!user?.is_owner) return <p className="text-xs text-slate-500">上下文由 Owner 管理。</p>
  const scope = `${getAuthEpoch()}:${worldName}:${user.id}:${conversation.id}`
  return <ContextPanel key={scope} scope={scope} conversation={conversation} onEditRole={onEditRole} />
}

function ContextPanel({ conversation, scope, onEditRole }: { conversation: Conversation; scope: string; onEditRole: (role: Role) => void }) {
  const [tab, setTab] = useState<ContextTab>(() => viewPreferences.get(scope)?.tab ?? 'usage')
  const roles = useAppStore(state => state.roles).filter(role => conversation.role_ids.includes(role.id) && role.active && !role.deleted_at)
  const [roleId, setRoleId] = useState(() => viewPreferences.get(scope)?.roleId ?? conversation.role_ids[0] ?? 0)
  useEffect(() => { viewPreferences.set(scope, { tab, roleId }) }, [scope, tab, roleId])
  const selected = roles.find(role => role.id === roleId) ?? roles[0]
  return <section aria-label="会话上下文管理" className="space-y-4 text-xs text-slate-300">
    <div role="group" aria-label="上下文视图" className="flex rounded-xl bg-slate-950/40 p-1">
      {([['usage', '占用与输入'], ['material', '会话材料'], ['compression', '主动压缩'], ['memory', '历史检索'], ['prompts', '提示词']] as const).map(([id, title]) =>
        <button key={id} type="button" aria-pressed={tab === id} onClick={() => setTab(id)} className={`flex-1 whitespace-nowrap rounded-lg px-1 py-2 focus-visible:outline focus-visible:outline-indigo-400 ${tab === id ? 'bg-panel font-medium text-indigo-500 shadow-sm' : 'text-slate-500'}`}>{title}</button>)}
    </div>
    {['usage', 'memory'].includes(tab) && <>
      <label className="block">查看角色<select aria-label="上下文角色" value={selected?.id ?? ''} onChange={event => setRoleId(Number(event.target.value))}
        className="mt-2 block w-full rounded-xl border border-slate-700 bg-panel p-2.5">
        {!roles.length && <option value="">没有可用角色</option>}{roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}
      </select></label>
      {tab === 'usage' && (selected ? <Usage key={selected.id} conversationId={conversation.id} role={selected} /> : <p>共享会话材料仍保留，可切换查看。</p>)}
    </>}
    {tab === 'material' && <Material conversationId={conversation.id} />}
    {tab === 'compression' && <CompressionPanel conversation={conversation} roles={roles} scope={`${scope}:compression`} />}
    {tab === 'memory' && (selected ? <MemoryPanel key={selected.id} conversationId={conversation.id} role={selected} scope={`${scope}:${selected.id}:memory`} onEditRole={onEditRole} /> : <p>请选择一个当前可用角色，按它的权限检索历史。</p>)}
    {tab === 'prompts' && <ConversationPromptSettings conversation={conversation} />}
  </section>
}

function useMessageBoundary(conversationId: number) {
  return useChatStore(state => state.conversationId === conversationId
    ? state.messages.slice(-8).map(message => `${message.id}:${message.status === 'generating' ? 'generating' : message.revision}:${message.status}`).join(',') : '')
}

function Usage({ conversationId, role }: { conversationId: number; role: Role }) {
  const active = useChatStore(state => state.conversationId === conversationId && state.activeGenerationIds.length > 0)
  const boundary = useMessageBoundary(conversationId)
  const draft = useContextDraft(state => state.scope === `${getAuthEpoch()}:${conversationId}` ? state.text : '')
  const [value, setValue] = useState<ContextPreview | null>(null), [error, setError] = useState('')
  const [content, setContent] = useState(false), [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const update = () => setRefresh(value => value + 1)
    const contextUpdate = (event: Event) => { if ((event as CustomEvent).detail === conversationId) update() }
    window.addEventListener('roleplex:prompts-updated', update)
    window.addEventListener('roleplex:context-updated', contextUpdate)
    return () => { window.removeEventListener('roleplex:prompts-updated', update); window.removeEventListener('roleplex:context-updated', contextUpdate) }
  }, [conversationId])
  useEffect(() => {
    const controller = new AbortController(), epoch = getAuthEpoch()
    let pending = false
    setBusy(true); setError('')
    async function load() {
      if (pending) return
      pending = true
      try {
        const result = await conversationContext.preview(conversationId, role.id, draft, content, controller.signal)
        if (!controller.signal.aborted && epoch === getAuthEpoch()) { setValue(result); setError('') }
      } catch {
        if (!controller.signal.aborted && epoch === getAuthEpoch()) {
          // 权限撤销或来源变化时立即移除旧内容，不能继续展示失效的私有输入。
          setValue(null); setError('上下文暂时无法读取，来源或权限可能已变化。请刷新重试。')
        }
      } finally { pending = false; if (!controller.signal.aborted) setBusy(false) }
    }
    const timer = window.setTimeout(() => void load(), 350)
    const poll = active ? window.setInterval(() => void load(), 2500) : undefined
    return () => { controller.abort(); window.clearTimeout(timer); if (poll) window.clearInterval(poll) }
  }, [conversationId, role.id, role.revision, role.context_window_tokens, draft, boundary, active, content, refresh])
  const request = value?.request, call = value?.latest_call
  const pressure = request ? (request.before_truncation_tokens + request.before_truncation_safety_margin_tokens + request.output_reserved_tokens) / request.effective_context_window * 100 : 0
  return <section aria-label="角色上下文占用" className="space-y-4">
    <div className="flex items-center justify-between gap-2"><span className="min-w-0 break-all text-slate-500">{role.model_name}</span>
      <button type="button" className={button} aria-label="刷新上下文" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={12} /></button></div>
    {error && <p role="alert" className="text-red-500">{error}</p>}
    {!value && !error && <p role="status">正在组装上下文预览…</p>}
    {value && request && <>
      <div className={card}>
        <div className="flex items-baseline justify-between"><h4 className="font-medium">裁剪前预计占用</h4><strong className={`font-mono text-xl ${pressure >= 100 ? 'text-amber-600' : 'text-indigo-500'}`}>{pressure.toFixed(1)}%</strong></div>
        <div role="meter" aria-label="上下文预计占用" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, pressure)} aria-valuetext={`${pressure.toFixed(1)}%，含输出预留和安全余量`}
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800"><div className={`h-full ${pressure >= 100 ? 'bg-amber-500' : 'bg-indigo-500'}`} style={{ width: `${Math.min(100, pressure)}%` }} /></div>
        <p className="mt-3 leading-relaxed text-slate-500">窗口 {number(request.effective_context_window)} Token · 含输出预留 {number(request.output_reserved_tokens)} 与安全余量 {number(request.before_truncation_safety_margin_tokens)}</p>
        <p className="mt-2 text-slate-500">保守估算，非厂商用量。{busy ? '正在更新…' : draft ? '已计入消息输入框草稿。' : '尚未计入新的消息正文。'}</p>
      </div>
      {request.blocked && <p role="alert" className="rounded-xl border border-amber-500 p-3 leading-relaxed">固定规则、工具与本次消息已超过窗口，当前输入无法派发。请缩短输入或调整角色窗口。</p>}
      {request.truncated_message_count > 0 && <p className="rounded-xl border border-amber-400 p-3 leading-relaxed text-amber-700">预计有 {request.truncated_message_count} 条稳定历史不会发送。原消息与共享材料仍保留，可在“会话材料”查看。</p>}
      <div className={card}>
        <h4 className="font-medium">下一次普通发言的输入</h4>
        <p className="mt-2 text-slate-500">采用 {request.included_message_count} 条历史 · 输入估算 {number(request.estimated_tokens)} Token</p>
        <dl className="mt-3 space-y-2">{Object.entries(request.breakdown).map(([key, count]) => <div key={key} className="flex justify-between gap-2"><dt className="text-slate-500">{parts[key] ?? key}</dt><dd className="font-mono">{number(count)}</dd></div>)}
          <div className="flex justify-between border-t border-slate-800 pt-2"><dt className="text-slate-500">本次安全余量</dt><dd>{number(request.safety_margin_tokens)}</dd></div></dl>
        <p className="mt-3 leading-relaxed text-slate-500">共享材料 v{value.shared.revision} · {value.shared.counts.included} 条稳定来源 · {value.shared.counts.pending} 条生成中 · {value.shared.counts.excluded} 条未纳入</p>
        {value.material.summary_id && <p className="mt-2 text-indigo-500">已采用历史摘要，原消息可在“历史检索”回读。</p>}
        {value.material.summary_omitted_reason && <p className="mt-2 text-amber-700">{value.material.summary_omitted_reason === 'does_not_fit' ? '当前摘要无法装入这个角色的窗口，按原文选择历史。' : '摘要不适用于当前来源或执行边界，按原文选择历史。'}</p>}
        {request.recovery_omitted && <p className="mt-2 text-amber-600">中断事实未能装入，不能据此认定操作未执行。</p>}
        <button type="button" aria-expanded={content} className={`${button} mt-3`} onClick={() => setContent(value => !value)}>{content ? '收起输入内容' : '查看输入内容'}</button>
      </div>
      {content && <section aria-label="实际输入预览" className="space-y-2">
        <p className="leading-relaxed text-slate-500">按当前材料与草稿组装；只预览。群聊后续角色会再接收前序角色的终态，工作流节点按明确上游单独组装。</p>
        {busy && <p role="status">正在更新输入内容…</p>}
        {!busy && value.messages?.map((message, index) => <details key={index} className={card}><summary className="cursor-pointer">{index + 1}. {message.type === 'system' ? '规则与角色' : index === value.messages!.length - 1 ? '本次消息' : message.type === 'ai' ? '角色自身回复' : '历史或执行事实'}</summary>
          <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans leading-relaxed">{message.content || '（空）'}</pre></details>)}
        <p className="text-slate-500">来源消息：{value.material.sources.map(source => `#${source.message_id} v${source.revision}`).join('、') || '无历史来源'}</p>
      </section>}
      <section aria-label="最近模型调用上下文" className={`${card} space-y-2`}>
        <h4 className="font-medium">最近实际调用</h4>
        {!call ? <p className="text-slate-500">还没有模型调用记录。</p> : <>
          <p>第 {call.call_index} 次调用 · {call.status === 'started' ? '执行中' : call.status === 'completed' ? '已返回' : '结果未完整记录'}</p>
          <p className="break-all text-slate-500">{call.model_name} · {new Date(call.recorded_at).toLocaleString()}</p>
          <p>该次输入估算 {number(call.input_estimate?.estimated_tokens)} Token</p>
          {call.input_estimate && <p className="text-slate-500">已包含 {call.input_estimate.tool_message_count} 条工具结果；每次模型调用开始时更新。</p>}
          <p>厂商输入 {number(call.provider_usage.input_tokens)} · 输出 {number(call.provider_usage.output_tokens)} Token</p>
          <p className="text-slate-500">{call.provider_mode === 'fake' ? 'fake Provider 不提供真实 Token 用量。' : '厂商未报告的字段保持未知。'}累计用量在成员的“执行用量”查看。</p>
          <p className="text-slate-500">{call.snapshot?.material?.scope === 'conversation' ? `采用共享材料 v${call.snapshot.material.revision}` : call.snapshot?.material?.scope === 'context_compaction' ? '上下文压缩维护请求' : call.snapshot?.material ? '采用工作流指定材料' : '旧执行没有来源快照'} · {call.execution_status === 'running' ? '本次执行仍在进行' : '历史执行记录'}</p>
        </>}
      </section>
    </>}
  </section>
}

function Material({ conversationId }: { conversationId: number }) {
  const directory = useAppStore(state => state.roleDirectory)
  const boundary = useMessageBoundary(conversationId)
  const [value, setValue] = useState<ContextPage | null>(null), [error, setError] = useState('')
  const [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    const update = (event: Event) => { if ((event as CustomEvent).detail === conversationId) setRefresh(value => value + 1) }
    window.addEventListener('roleplex:context-updated', update)
    return () => window.removeEventListener('roleplex:context-updated', update)
  }, [conversationId])
  useEffect(() => {
    const abort = new AbortController(), epoch = getAuthEpoch()
    controller.current?.abort(); controller.current = abort
    setBusy(true); setError(''); setValue(null)
    void conversationContext.read(conversationId, abort.signal).then(result => {
      if (!abort.signal.aborted && epoch === getAuthEpoch()) setValue(result)
    }).catch(() => { if (!abort.signal.aborted) setError('会话材料读取失败，请刷新重试。') })
      .finally(() => { if (!abort.signal.aborted) setBusy(false) })
    return () => abort.abort()
  }, [conversationId, boundary, refresh])
  async function more() {
    if (!value?.next_before || busy) return
    const abort = controller.current!, epoch = getAuthEpoch()
    setBusy(true)
    try {
      const next = await conversationContext.read(conversationId, abort.signal, value.next_before, value.revision)
      if (!abort.signal.aborted && epoch === getAuthEpoch()) setValue({ ...next, entries: [...value.entries, ...next.entries] })
    } catch {
      if (!abort.signal.aborted) { setValue(null); setError('材料版本或权限已变化，请刷新后重新查看。') }
    } finally { if (!abort.signal.aborted) setBusy(false) }
  }
  return <section aria-label="共享会话材料" className="space-y-3">
    <p className="leading-relaxed text-slate-500">所有角色共用一份稳定材料，按消息同步保存。生成中、失败与中断的片段保留在聊天中，并明确列出未纳入原因。</p>
    <button type="button" className={button} onClick={() => setRefresh(value => value + 1)}>刷新会话材料</button>
    {error && <p role="alert" className="text-red-500">{error}</p>}
    {busy && <p role="status">正在读取会话材料…</p>}
    {value && <>
      <p className="text-slate-500">材料 v{value.revision} · 稳定 {value.counts.included} · 生成中 {value.counts.pending} · 未纳入 {value.counts.excluded}</p>
      {value.active_summary && <details className={card}><summary className="cursor-pointer font-medium">当前摘要 · 覆盖 {value.active_summary.source_count} 条原消息</summary><div className="mt-3"><SummaryBody text={value.active_summary.text} /></div><p className="mt-2 text-slate-500">下方来源保留完整记录，输入会按摘要和未覆盖消息组装。</p></details>}
      {value.summary_unavailable && <p className="text-amber-700">原摘要的来源已变化，当前使用原文。</p>}
      {!value.entries.length && <p className="py-4 text-center text-slate-500">还没有会话材料。发送消息后自动同步。</p>}
      {value.entries.map(entry => <details key={entry.message_id} className={card}>
        <summary className="cursor-pointer"><span className="font-medium">{entry.sender_type === 'role' ? directory[entry.sender_id ?? 0]?.name ?? `角色 #${entry.sender_id}` : entry.sender_type === 'user' ? `用户 #${entry.sender_id}` : entry.sender_type}</span>
          <span className="ml-2 text-slate-500">#{entry.message_id} · v{entry.revision}</span><span className="mt-1 block text-slate-500">{entry.state === 'included' ? entry.status === 'stopped' ? '已纳入，含停止标记' : '稳定材料' : reasons[entry.reason ?? ''] ?? '暂未纳入'}</span></summary>
        <p className="mt-3 whitespace-pre-wrap break-words leading-relaxed">{entry.text || '正文与执行记录保留在原消息中。'}</p>
        {entry.text_truncated && <p className="mt-2 text-amber-600">这里只展示前 4,000 字符；材料正文完整保留。</p>}
      </details>)}
      {value.next_before && <button type="button" disabled={busy} className={`${button} w-full`} onClick={() => void more()}>更早的会话材料</button>}
    </>}
  </section>
}
