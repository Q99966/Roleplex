import { useEffect, useRef, useState } from 'react'
import { getAuthEpoch, type Role } from '../../api/client'
import { contextActions, contextError, type MemoryAccess, type MemoryRead, type MemorySource, type SearchResult } from '../../api/contextActions'
import { useAppStore } from '../../store/app'
import { useConversationChat } from '../../store/conversationChat'
import { SummaryBody } from './SummaryBody'

const field = 'mt-2 block w-full rounded-xl border border-slate-700 bg-panel p-2.5 text-xs outline-none focus:border-indigo-400'
const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40 focus-visible:outline focus-visible:outline-indigo-400'
const states: Record<string, string> = { done: '完整消息', stopped: '已停止', error: '失败片段', interrupted: '中断片段' }
type Query = { text: string; scope: string; kind: string }
const drafts = new Map<string, Query>()

/** 人工检索复用角色共享范围；实际工具读取记录与普通查询分开，迟到响应不跨身份展示。 */
export function MemoryPanel({ conversationId, role, scope, onEditRole }: { conversationId: number; role: Role; scope: string; onEditRole: (role: Role) => void }) {
  const directory = useAppStore(state => state.roleDirectory)
  const [query, setQuery] = useState<Query>(() => drafts.get(scope) ?? { text: '', scope: 'current', kind: 'all' })
  const [searched, setSearched] = useState<Query | null>(null), [results, setResults] = useState<SearchResult | null>(null)
  const [reading, setReading] = useState<MemoryRead | null>(null), [error, setError] = useState(''), [readError, setReadError] = useState('')
  const [searching, setSearching] = useState(false), [readBusy, setReadBusy] = useState(false)
  const searchAbort = useRef<AbortController | null>(null), readAbort = useRef<AbortController | null>(null)
  const alive = useRef(true), readTitle = useRef<HTMLHeadingElement>(null)
  const resultTitle = useRef<HTMLHeadingElement>(null), queryInput = useRef<HTMLInputElement>(null)
  const [records, setRecords] = useState<MemoryAccess[] | null>(null), [recordsOpen, setRecordsOpen] = useState(false), [recordsError, setRecordsError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const active = useConversationChat(state => state.conversationId === conversationId && state.activeGenerationIds.length > 0)
  const boundary = useConversationChat(state => state.conversationId === conversationId ? state.messages.slice(-4).map(row => `${row.id}:${row.status}`).join(',') : '')
  useEffect(() => { alive.current = true; return () => { alive.current = false; searchAbort.current?.abort(); readAbort.current?.abort() } }, [])
  useEffect(() => { drafts.set(scope, query) }, [scope, query])
  useEffect(() => {
    if (!recordsOpen) return
    const controller = new AbortController(), epoch = getAuthEpoch()
    let pending = false
    async function load() {
      if (pending) return
      pending = true
      try {
        const result = await contextActions.references(conversationId, role.id, controller.signal)
        if (!controller.signal.aborted && epoch === getAuthEpoch()) { setRecords(result.items); setRecordsError('') }
      } catch { if (!controller.signal.aborted) { setRecords(null); setRecordsError('工具来源记录暂时不可读取。') } }
      finally { pending = false }
    }
    void load()
    const timer = active ? window.setInterval(() => void load(), 2200) : undefined
    return () => { controller.abort(); if (timer) window.clearInterval(timer) }
  }, [conversationId, role.id, recordsOpen, active, boundary, refresh])

  function sender(source: MemorySource) {
    return source.kind === 'summary' ? '已发布摘要' : source.sender_type === 'role' ? directory[source.sender_id ?? 0]?.name ?? `角色 #${source.sender_id}` : `用户 #${source.sender_id}`
  }

  async function search(more = false) {
    const input = more && searched ? searched : query
    if (!input.text.trim()) return
    searchAbort.current?.abort(); readAbort.current?.abort()
    setReadBusy(false)
    const controller = new AbortController(), epoch = getAuthEpoch()
    searchAbort.current = controller
    setSearching(true); setError('')
    if (!more) { setResults(null); setReading(null); setReadError('') }
    try {
      const response = await contextActions.search(conversationId, role.id, input.text, input.scope,
        input.kind === 'all' ? ['message', 'summary'] : [input.kind], more ? results?.next_cursor ?? undefined : undefined, controller.signal)
      if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) {
        setResults(more && results ? { ...response, results: [...results.results, ...response.results] } : response)
        setSearched({ ...input })
      }
    } catch (error) {
      if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) { setResults(null); setReading(null); setError(contextError(error)) }
    } finally {
      if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) {
        setSearching(false)
        window.setTimeout(() => (resultTitle.current ?? queryInput.current)?.focus(), 0)
      }
    }
  }

  async function read(source: MemorySource, offset = 0) {
    readAbort.current?.abort()
    const controller = new AbortController(), epoch = getAuthEpoch()
    readAbort.current = controller
    setReadBusy(true); setReadError('')
    if (!offset) setReading(null)
    try {
      const result = await contextActions.read(conversationId, role.id, source.reference, offset, controller.signal)
      if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) {
        setReading(result)
        window.setTimeout(() => readTitle.current?.focus(), 0)
      }
    } catch (error) {
      if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) { setReading(null); setReadError(contextError(error)); readTitle.current?.focus() }
    } finally { if (!controller.signal.aborted && alive.current && epoch === getAuthEpoch()) setReadBusy(false) }
  }

  const enabled = ['memory_search', 'memory_read'].every(name => role.builtin_tools.includes(name))
  const readerOpen = readBusy || !!reading || !!readError
  return <section aria-label="历史检索与回读" className="space-y-4 text-xs">
    {!readerOpen && <>
    <p className="leading-relaxed text-slate-500">从{role.name}可读取、且允许向本会话全体成员共享的历史中检索。压缩后的原消息仍可回读。</p>
    {!enabled && <div className="rounded-xl border border-slate-800 bg-panel p-3"><p className="leading-relaxed text-slate-500">当前可人工查询。让角色主动检索时，请在角色工具中启用“历史检索”。</p>
      <button type="button" className={`${button} mt-2`} onClick={() => onEditRole(role)}>配置角色检索工具</button></div>}
    <form className="space-y-3" onSubmit={event => { event.preventDefault(); void search() }}>
      <label className="block">搜索关键词<input ref={queryInput} aria-label="历史搜索关键词" className={field} value={query.text} maxLength={512}
        onChange={event => setQuery({ ...query, text: event.target.value })} placeholder="中文词组、英文或代码标识符" /></label>
      <div className="grid grid-cols-2 gap-2">
        <label>搜索范围<select aria-label="历史搜索范围" className={field} value={query.scope} onChange={event => setQuery({ ...query, scope: event.target.value })}><option value="current">当前会话</option><option value="related">可共享的关联会话</option></select></label>
        <label>来源类型<select aria-label="历史来源类型" className={field} value={query.kind} onChange={event => setQuery({ ...query, kind: event.target.value })}><option value="all">消息与摘要</option><option value="message">原消息</option><option value="summary">当前摘要</option></select></label>
      </div>
      <button type="submit" disabled={searching || !query.text.trim()} className="rounded-lg bg-indigo-600 px-3 py-2 text-white disabled:opacity-40">搜索历史</button>
    </form>
    {error && <p role="alert" className="text-red-500">{error}</p>}
    {searching && <p role="status" className="text-slate-500">正在搜索有权共享的来源…</p>}
    {results && <section aria-label="历史搜索结果" className="space-y-3">
      <h4 ref={resultTitle} tabIndex={-1} className="font-medium outline-none">搜索结果</h4>
      <p className="leading-relaxed text-slate-500">{results.notice}</p>
      {!results.results.length && <p>没有匹配结果。可以换关键词或扩大到可共享的关联会话。</p>}
      {results.results.map(source => <article key={`${source.kind}:${source.source_id}`} aria-label={`历史来源 ${source.kind === 'message' ? `消息 #${source.message_id}` : `摘要 ${source.source_id}`}`} className="rounded-xl border border-slate-800 bg-panel p-3">
        <h4 className="break-words font-medium">{source.conversation_title}</h4>
        <p className="mt-1 text-slate-500">{sender(source)} · {source.kind === 'message' ? `${states[source.status] ?? source.status} #${source.message_id}` : '历史摘要'} · v{source.source_revision}</p>
        <p className="mt-3 whitespace-pre-wrap break-words leading-relaxed">{source.snippet}</p>
        <p className="mt-2 break-words text-slate-500">匹配词：{source.matched_terms.join('、')}</p>
        <button type="button" className={`${button} mt-3`} onClick={() => void read(source)}>读取来源原文</button>
      </article>)}
      {results.next_cursor && <button type="button" className={`${button} w-full`} disabled={searching} onClick={() => void search(true)}>更多匹配来源</button>}
    </section>}
    </>}
    {(readBusy || reading || readError) && <section aria-label="历史原文" className="space-y-3 rounded-xl border border-indigo-200 bg-panel p-3">
      <button type="button" className={button} onClick={() => { readAbort.current?.abort(); setReading(null); setReadError(''); setReadBusy(false); window.setTimeout(() => (resultTitle.current ?? queryInput.current)?.focus(), 0) }}>返回搜索结果</button>
      <h4 ref={readTitle} tabIndex={-1} className="font-medium outline-none">来源原文</h4>
      {readBusy && <p role="status">正在核对来源权限与版本…</p>}
      {readError && <p role="alert" className="text-red-500">{readError}</p>}
      {reading && <>
        <p className="break-words text-slate-500">{reading.conversation_title} · {sender(reading)} · {states[reading.status] ?? reading.status} · v{reading.source_revision}</p>
        {reading.kind === 'summary' && !reading.truncated && !reading.offset ? <SummaryBody text={reading.text} /> : <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans leading-relaxed">{reading.text}</pre>}
        <p className="text-slate-500">字符 {reading.offset}–{reading.offset + [...reading.text].length} / {reading.total_characters}</p>
        {reading.next_offset !== null && <button type="button" disabled={readBusy} className={button} onClick={() => void read(reading, reading.next_offset!)}>继续读取原文</button>}
        {!!reading.execution_facts && <details className="text-slate-500"><summary className="cursor-pointer">原消息的执行事实</summary><pre className="mt-2 whitespace-pre-wrap break-words font-sans">{JSON.stringify(reading.execution_facts, null, 2)}</pre></details>}
        {reading.neighbors.length > 0 && <details className="text-slate-500"><summary className="cursor-pointer">前后文片段</summary><div className="mt-2 space-y-3">{reading.neighbors.map(neighbor => <div key={neighbor.source_id}><p>{sender(neighbor)} · 消息 #{neighbor.message_id} · v{neighbor.source_revision}</p><p className="mt-1 whitespace-pre-wrap break-words">{neighbor.text}{neighbor.truncated ? '…' : ''}</p><button type="button" className="mt-1 text-indigo-500" onClick={() => void read(neighbor)}>查看这条原文</button></div>)}</div></details>}
        <p className="leading-relaxed text-slate-500">{reading.notice}</p>
      </>}
    </section>}
    <details hidden={readerOpen} className="border-t border-slate-800 pt-3" onToggle={event => setRecordsOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer font-medium">角色的工具来源记录</summary>
      {recordsOpen && <section aria-label="工具历史来源记录" className="mt-3 space-y-3">
        <p className="leading-relaxed text-slate-500">最近 50 条工具已读取来源；人工查询不写入此处，也不表示模型最终答案已引用。</p>
        <button type="button" className={button} onClick={() => setRefresh(value => value + 1)}>刷新工具来源记录</button>
        {recordsError && <p role="alert" className="text-red-500">{recordsError}</p>}
        {records?.length === 0 && <p className="text-slate-500">角色还没有历史检索记录。</p>}
        {records?.map(record => <article key={record.id} className="rounded-xl border border-slate-800 bg-panel p-3">
          <p>{record.action === 'read' ? '回读原文' : '搜索命中'} · 执行 {record.execution_id.slice(0, 8)}</p>
          {record.available && record.world_source ? <p className="mt-1 text-slate-500">{record.world_source.title} · v{record.world_source.source_revision} · 在{record.world_source.kind === 'world_note' ? '世界记忆' : '世界任务详情'}中核对。</p> : record.available && record.source ? <><p className="mt-1 break-words text-slate-500">{record.source.conversation_title} · {sender(record.source)} · v{record.source.source_revision}</p><button type="button" className={`${button} mt-2`} onClick={() => void read(record.source!)}>核对这条来源</button></> : <p className="mt-2 text-slate-500">来源目前不可读取，保留已发生的工具读取记录。</p>}
        </article>)}
      </section>}
    </details>
  </section>
}
