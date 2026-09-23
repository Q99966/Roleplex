import { useEffect, useRef, useState } from 'react'
import type { Conversation, Role } from '../../api/client'
import { getAuthEpoch } from '../../api/client'
import { conversationContext, type ContextSummary } from '../../api/context'
import { contextActions, contextError, type Compressions, type CompressionInput } from '../../api/contextActions'
import { useAppStore } from '../../store/app'
import { SummaryBody } from './SummaryBody'
import { ContextPolicySettings } from './ContextPolicySettings'

const field = 'mt-2 block w-full rounded-xl border border-slate-700 bg-panel p-2.5 text-xs outline-none focus:border-indigo-400'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-indigo-400'
const states: Record<string, string> = { queued: '排队中', running: '正在压缩', stopping: '正在停止', completed: '已采用摘要', unchanged: '保持原版本', cancelled: '已取消', interrupted: '执行中断', stale: '来源已变化', failed: '未完成' }
const running = (status: string) => ['queued', 'running', 'stopping'].includes(status)
const stateLabel = (job: { scope?: string; status: string }) => job.scope === 'execution' && job.status === 'completed' ? '私有摘要已生成' : states[job.status] ?? job.status
const number = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString()
type Draft = { instructions: string; keep_recent: number; target_tokens: number; modelRoleId: number | null }
const drafts = new Map<string, Draft>()

/** 会话级维护；模型配置选择独立于查看角色，草稿只按会话保留。 */
export function CompressionPanel({ conversation, roles, scope }: { conversation: Conversation; roles: Role[]; scope: string }) {
  const conversationId = conversation.id
  const { user, worldName } = useAppStore()
  const storageKey = `roleplex:compression:${worldName}:${user?.id}:${conversationId}`
  const [draft, setDraft] = useState<Draft>(() => drafts.get(scope) ?? { instructions: '', keep_recent: 6, target_tokens: 1024,
    modelRoleId: conversation.role_ids.find(id => roles.some(role => role.id === id)) ?? null })
  // 原配置失效时要求重新选择，不能静默换一个可能收费的模型。
  const role = roles.find(role => role.id === draft.modelRoleId)
  const [value, setValue] = useState<{ context: ContextSummary; history: Compressions } | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [focusJob, setFocusJob] = useState<string | null>(null)
  const pending = useRef<CompressionInput | null>(null), alive = useRef(true), active = useRef(false)
  const generation = useRef(0), status = useRef<HTMLParagraphElement>(null)
  const retainedKey = useRef<string | null>(null)
  useEffect(() => {
    alive.current = true
    try { retainedKey.current = sessionStorage.getItem(storageKey) } catch { /* 隐私模式下仍保留内存幂等身份。 */ }
    return () => { alive.current = false }
  }, [storageKey])
  useEffect(() => { drafts.set(scope, draft) }, [scope, draft])
  useEffect(() => {
    const controller = new AbortController(), epoch = getAuthEpoch(), ticket = ++generation.current
    let loading = false
    async function load() {
      if (loading) return
      loading = true
      try {
        const [context, history] = await Promise.all([conversationContext.read(conversationId, controller.signal), contextActions.list(conversationId, controller.signal)])
        if (controller.signal.aborted || epoch !== getAuthEpoch() || generation.current !== ticket) return
        setValue({ context, history }); active.current = history.jobs.some(job => running(job.status))
        if (retainedKey.current) {
          const known = history.jobs.find(job => job.request_key === retainedKey.current)
          if (known) { setFocusJob(known.id); clearPending(); setNotice('已核对上次提交的任务。') }
          else {
            const found = await contextActions.find(conversationId, retainedKey.current, controller.signal)
            if (!controller.signal.aborted && generation.current === ticket && found.jobs[0]) {
              setFocusJob(found.jobs[0].id); clearPending(); setNotice('已核对上次提交的任务。')
            }
          }
        }
      } catch {
        if (!controller.signal.aborted && epoch === getAuthEpoch()) { setValue(null); setError('压缩状态读取失败，请刷新任务。') }
      } finally { loading = false }
    }
    void load()
    const timer = window.setInterval(() => { if (active.current) void load() }, 1800)
    const updated = (event: Event) => { if ((event as CustomEvent).detail === conversationId) void load() }
    window.addEventListener('roleplex:context-updated', updated)
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener('roleplex:context-updated', updated) }
  }, [conversationId, refresh])

  function clearPending() {
    pending.current = null; retainedKey.current = null
    try { sessionStorage.removeItem(storageKey) } catch { /* 不影响服务端幂等。 */ }
  }

  async function submit() {
    if (!role || !value || busy) return
    const epoch = getAuthEpoch()
    const body = pending.current ?? { instructions: draft.instructions, keep_recent: draft.keep_recent, target_tokens: draft.target_tokens,
      request_key: retainedKey.current ?? crypto.randomUUID(), role_id: role.id, expected_revision: value.context.revision }
    pending.current = body; retainedKey.current = body.request_key
    try { sessionStorage.setItem(storageKey, body.request_key) } catch { /* 仅缓存非敏感请求身份。 */ }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await contextActions.start(conversationId, body)
      if (alive.current && epoch === getAuthEpoch()) { clearPending(); setFocusJob(result.id); setNotice('压缩任务已提交。'); setRefresh(value => value + 1); status.current?.focus() }
    } catch (error) {
      if (!alive.current || epoch !== getAuthEpoch()) return
      // 不自动再发 POST；服务端若已接受，找回同一任务并继续查看。
      try {
        const known = await contextActions.find(conversationId, body.request_key)
        if (alive.current && epoch === getAuthEpoch() && known.jobs[0]) {
          clearPending(); setFocusJob(known.jobs[0].id); setNotice('提交已生效，已找回原任务。'); setRefresh(value => value + 1)
          return
        }
      } catch { /* 保留原请求，后续明确重试仍使用同一个身份。 */ }
      if (!alive.current || epoch !== getAuthEpoch()) return
      const statusCode = (error as { status?: number })?.status
      if (statusCode && statusCode >= 400 && statusCode < 500) clearPending()
      setError(statusCode ? contextError(error) : '提交结果尚未确认。请刷新任务；重试会先核对原请求。')
      setRefresh(value => value + 1)
    } finally { if (alive.current && epoch === getAuthEpoch()) setBusy(false) }
  }

  async function stop(id: string) {
    const epoch = getAuthEpoch(); setBusy(true); setError('')
    try { await contextActions.cancel(conversationId, id); if (alive.current && epoch === getAuthEpoch()) { setNotice('停止请求已提交，已采用的版本保留。'); setRefresh(value => value + 1) } }
    catch (error) { if (alive.current && epoch === getAuthEpoch()) { setError(contextError(error)); setRefresh(value => value + 1) } }
    finally { if (alive.current && epoch === getAuthEpoch()) { setBusy(false); status.current?.focus() } }
  }

  async function restore(id: string | null) {
    if (!value || busy) return
    const epoch = getAuthEpoch(); setBusy(true); setError('')
    try {
      await contextActions.restore(conversationId, value.context.revision, id)
      if (alive.current && epoch === getAuthEpoch()) { setNotice(id ? '已采用所选摘要，后来消息保留。' : '已恢复使用原文，全部消息保留。'); setRefresh(value => value + 1); status.current?.focus() }
    } catch (error) { if (alive.current && epoch === getAuthEpoch()) { setError(contextError(error)); setRefresh(value => value + 1) } }
    finally { if (alive.current && epoch === getAuthEpoch()) setBusy(false) }
  }

  const job = value?.history.jobs.find(job => job.id === focusJob) ?? value?.history.jobs[0]
  const inFlight = value?.history.jobs.find(job => running(job.status))
  return <section aria-label="主动压缩上下文" className="space-y-4 text-xs">
    <section aria-label="压缩对象" className="space-y-2 rounded-xl border border-indigo-200 bg-panel p-3">
      <p className="text-slate-500">压缩当前{conversation.type === 'group' ? '群聊' : '单聊'}</p>
      <h4 className="break-words font-medium">{conversation.title}</h4>
      <p className="leading-relaxed text-slate-500">摘要保存在本会话，供所有角色后续组装输入时共用。原消息保留。</p>
    </section>
    <details className="rounded-xl border border-slate-800 bg-panel p-3"><summary className="cursor-pointer font-medium">自动压缩设置</summary>
      <div className="mt-4"><ContextPolicySettings conversationId={conversationId} roles={roles} /></div></details>
    <form className="space-y-3" onSubmit={event => { event.preventDefault(); void submit() }}>
      <fieldset disabled={busy || !!inFlight} className="space-y-3">
        <label className="block">生成摘要的模型<select aria-label="生成摘要的模型" className={field} value={draft.modelRoleId ?? ''}
          onChange={event => setDraft({ ...draft, modelRoleId: event.target.value ? Number(event.target.value) : null })}>
          {!role && <option value={draft.modelRoleId ?? ''} disabled>{draft.modelRoleId === null ? '请选择模型配置' : '原模型配置不可用，请重新选择'}</option>}
          {roles.map(item => <option key={item.id} value={item.id}>{item.model_name} · 配置来自{item.name}</option>)}
        </select></label>
        <p className="leading-relaxed text-slate-500">借用角色已配置的模型生成摘要，计入现有决策预算；压缩范围始终是当前会话。</p>
        <label className="block">最近保留消息数<input type="number" aria-label="最近保留消息数" min={0} max={200} value={draft.keep_recent} className={field}
          onChange={event => setDraft({ ...draft, keep_recent: Number(event.target.value) })} /></label>
        <p className="text-slate-500">保留最近尚未压缩的完整消息；置顶内容始终保留原文。</p>
        <label className="block">目标摘要长度<input type="number" aria-label="目标摘要长度" min={128} max={Math.min(100000, (role?.context_window_tokens ?? 200000) - 1)} value={draft.target_tokens} className={field}
          onChange={event => setDraft({ ...draft, target_tokens: Number(event.target.value) })} /><span className="mt-1 block text-slate-500">Token 保守估算上限；必须保留的执行事实也会计入。</span></label>
        <label className="block">本次保留重点<textarea aria-label="压缩保留重点" rows={4} maxLength={10000} className={field} value={draft.instructions}
          onChange={event => setDraft({ ...draft, instructions: event.target.value })} placeholder="例如：保留关键决定、当前目标和未完成事项" /></label>
        <p className="text-slate-500">保留重点仅用于本次会话压缩。</p>
      </fieldset>
      <div className="flex flex-wrap gap-2"><button type="submit" disabled={busy || !!inFlight || !role || !value} className="rounded-lg bg-indigo-600 px-3 py-2 text-white disabled:opacity-40">开始压缩</button>
        <button type="button" className={button} onClick={() => { setError(''); setRefresh(value => value + 1) }}>刷新压缩任务</button></div>
    </form>
    {error && <p role="alert" className="text-red-500">{error}</p>}
    <p ref={status} tabIndex={-1} role="status" className="text-slate-500 outline-none">{busy ? '正在提交…' : notice || (!value ? '正在读取任务…' : '')}</p>
    {inFlight && <div className="rounded-xl border border-indigo-200 bg-panel p-3"><p className="font-medium">{stateLabel(inFlight)}</p>
      <p className="mt-2 text-slate-500">已记录模型调用 {inFlight.usage?.recorded_calls ?? inFlight.completed_calls} 次 · 来源 {inFlight.source_count} 条</p>
      <button type="button" className={`${button} mt-3`} disabled={busy || inFlight.cancel_requested} onClick={() => void stop(inFlight.id)}>停止压缩任务</button></div>}
    {job && <section aria-label="压缩执行记录" className="space-y-2 rounded-xl border border-slate-800 bg-panel p-3">
      <div className="flex justify-between gap-2"><h4 className="font-medium">最近维护记录</h4><span>{stateLabel(job)}</span></div>
      <p className="break-all text-slate-500">{job.model_name} · {new Date(job.created_at).toLocaleString()}</p>
      <p className="text-slate-500">{job.trigger === 'automatic' ? '自动触发 · 共用原任务预算' : '主动压缩'} · {job.scope === 'execution' ? '仅用于本次执行，不发布会话摘要' : '会话共享上下文'}</p>
      <p>材料估算 {number(job.input_tokens_estimate)} → {number(job.output_tokens_estimate)} Token</p>
      {job.outcome && <p className={job.outcome.target_reached ? 'text-slate-500' : 'text-amber-700'}>保留尾部后 {number(job.outcome.material_tokens)} / 目标 {number(job.outcome.target_tokens)} Token{job.outcome.target_reached ? ' · 已达到目标' : ' · 最近消息或必要事实使材料仍高于目标'}</p>}
      <p className="text-slate-500">来源 {job.source_count} 条 · 模型调用 {job.usage?.recorded_calls ?? job.completed_calls} 次</p>
      <p className="text-slate-500">厂商累计输入 {number(job.usage?.metrics.input_tokens.total)} · 输出 {number(job.usage?.metrics.output_tokens.total)} Token</p>
      {job.error_code && <p className="leading-relaxed text-amber-700">{contextError(job.error_code)}</p>}
      <details><summary className="cursor-pointer text-slate-500">本次范围与要求</summary><p className="mt-2">{job.scope === 'execution' ? '本次执行的完整工具轮或精确上游正文' : `来源至消息 #${job.through_message_id} · 材料 v${job.source_revision}`}</p><p className="mt-2 whitespace-pre-wrap break-words">{job.instructions || '使用默认保留要求'}</p></details>
    </section>}
    {value && <section aria-label="摘要版本" className="space-y-3 border-t border-slate-800 pt-3">
      <h4 className="font-medium">摘要版本</h4>
      <button type="button" className={button} disabled={busy || !value.context.active_summary && !value.context.summary_unavailable} onClick={() => void restore(null)}>恢复为原文</button>
      {value.context.summary_unavailable && <p className="text-amber-700">旧摘要的来源已变化，后续输入已回到原文。</p>}
      {!value.history.versions.length && <p className="text-slate-500">还没有已发布的摘要版本。</p>}
      {value.history.versions.map((version, index) => <details key={version.id} className="rounded-xl border border-slate-800 bg-panel p-3"><summary className="cursor-pointer">{index === 0 ? '最新摘要' : `历史摘要 ${value.history.versions.length - index}`} · {version.source_count} 条来源 · {version.active ? '当前指针' : '历史'}{!version.valid ? ' · 来源失效' : ''}</summary>
        {version.text && <div className="mt-3"><SummaryBody text={version.text} /></div>}
        {!version.valid && <p className="mt-2 text-slate-500">来源修订或删除后，旧摘要不能重新采用。</p>}
        <button type="button" className={`${button} mt-3`} disabled={busy || version.active || !version.valid} onClick={() => void restore(version.id)}>采用此摘要版本</button>
      </details>)}
      {value.history.jobs.length > 1 && <details className="text-slate-500"><summary className="cursor-pointer">更早的维护记录</summary><div className="mt-2 space-y-1">{value.history.jobs.slice(1).map(job => <button type="button" key={job.id} className="block w-full rounded-lg border border-slate-800 p-2 text-left" onClick={() => setFocusJob(job.id)}>{new Date(job.created_at).toLocaleString()} · {stateLabel(job)}</button>)}</div></details>}
    </section>}
  </section>
}
