import { useEffect, useRef, useState } from 'react'
import { getAuthEpoch } from '../../api/client'
import { worldOrchestrator, type WorldMemory } from '../../api/worldOrchestrator'

const button = 'rounded-lg border border-slate-700 px-3 py-2 text-xs disabled:opacity-40'
const field = 'w-full rounded-xl border border-slate-700 bg-canvas p-2.5 text-xs outline-none focus:border-indigo-400'

/** 世界岗位约定可查来源、修正和停用；不把模型归纳显示成已核实的平台规则。 */
export function WorldMemories() {
  const [items, setItems] = useState<WorldMemory[]>([]), [query, setQuery] = useState(''), [text, setText] = useState('')
  const [category, setCategory] = useState('note'), [editing, setEditing] = useState<WorldMemory | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0)
  const [versions, setVersions] = useState<Record<string, WorldMemory[]>>({})
  const pendingKey = useRef<string | null>(null), epoch = getAuthEpoch()
  useEffect(() => {
    const controller = new AbortController()
    void worldOrchestrator.memories(query, controller.signal).then(value => { if (!controller.signal.aborted && epoch === getAuthEpoch()) setItems(value.items) }).catch(() => { if (!controller.signal.aborted) setError('记忆读取失败，请重试。') })
    return () => controller.abort()
  }, [query, refresh, epoch])
  async function save() {
    if (!text.trim() || busy) return
    setBusy(true); setError('')
    try {
      if (editing) await worldOrchestrator.editMemory(editing.id, editing.revision, text, 'active')
      else { pendingKey.current ??= crypto.randomUUID(); await worldOrchestrator.saveMemory(text, category, pendingKey.current) }
      if (epoch === getAuthEpoch()) { setText(''); setEditing(null); pendingKey.current = null; setRefresh(v => v + 1) }
    } catch { if (epoch === getAuthEpoch()) setError('保存未确认或版本已变化，当前输入保留，请刷新核对。') }
    finally { if (epoch === getAuthEpoch()) setBusy(false) }
  }
  async function select(item: WorldMemory) {
    try { const row = await worldOrchestrator.readMemory(item.id, item.revision); if (epoch === getAuthEpoch()) { setEditing(row); setText(row.text ?? '') } }
    catch { setError('来源或版本已变化，请重新读取。'); setRefresh(v => v + 1) }
  }
  async function disable(item: WorldMemory) {
    setBusy(true)
    try {
      const row = await worldOrchestrator.readMemory(item.id, item.revision)
      await worldOrchestrator.editMemory(row.id, row.revision, row.text ?? '', 'disabled')
      if (epoch === getAuthEpoch()) setRefresh(v => v + 1)
    } catch { setError('停用未完成，请刷新核对版本。') }
    finally { if (epoch === getAuthEpoch()) setBusy(false) }
  }
  async function history(id: string) {
    try { const value = await worldOrchestrator.memoryVersions(id); if (epoch === getAuthEpoch()) setVersions(rows => ({ ...rows, [id]: value.items })) }
    catch { if (epoch === getAuthEpoch()) setError('历史版本读取失败，请重试。') }
  }
  return <section aria-label="世界记忆" className="space-y-4 text-xs">
    <h3 className="font-semibold">世界约定与记忆</h3><p className="leading-relaxed text-slate-500">保存在当前世界，协调角色更换后仍可交接；模型整理保留来源与推断身份。</p>
    <input aria-label="搜索世界记忆" className={field} value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索约定或决定" />
    <form onSubmit={event => { event.preventDefault(); void save() }} className="space-y-2">
      <select aria-label="世界记忆类别" className={field} value={category} disabled={!!editing} onChange={event => setCategory(event.target.value)}>
        {[['note', '事项'], ['constraint', '约定'], ['decision', '决定'], ['preference', '偏好'], ['inference', '推断']].map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select><textarea aria-label="世界记忆内容" rows={4} maxLength={20000} className={field} value={text} onChange={event => { setText(event.target.value); pendingKey.current = null }} />
      <button className={button} disabled={busy || !text.trim()}>{editing ? '更新世界记忆' : '保存世界记忆'}</button>
      {editing && <button type="button" className={`${button} ml-2`} onClick={() => { setEditing(null); setText('') }}>取消编辑</button>}
    </form>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    <button className={button} onClick={() => { setError(''); setRefresh(v => v + 1) }}>刷新记忆</button>
    {items.map(item => <article key={item.id} className="space-y-2 rounded-xl border border-slate-800 p-3">
      <p className="leading-relaxed whitespace-pre-wrap">{item.text ?? (item.status === 'disabled' ? '此记忆已停用' : '来源失效，内容不再提供')}</p>
      <p className="text-slate-500">{item.origin === 'owner' ? 'Owner 记录' : '模型整理'} · v{item.revision}{item.source_message_id ? ` · 来源 #${item.source_message_id}` : ''}</p>
      {item.text !== null && <div className="flex gap-2"><button className={button} onClick={() => void select(item)}>{item.status === 'disabled' ? '查看并恢复' : '查看与编辑'}</button>{item.available && <button className={button} disabled={busy} onClick={() => void disable(item)}>停用</button>}</div>}
      <details onToggle={event => { if (event.currentTarget.open) void history(item.id) }}><summary>来源与历史版本</summary>
        <p className="my-2 text-slate-500">{item.notice}</p>{versions[item.id]?.map(row => <div key={row.revision} className="my-2 border-l border-slate-700 pl-2"><p>v{row.revision} · {row.status} · {row.source_message_id ? `消息 #${row.source_message_id} / v${row.source_revision}` : 'Owner 手动记录'}</p><p className="mt-1 whitespace-pre-wrap">{row.text ?? '原来源已失效，内容不再提供。'}</p></div>)}</details>
    </article>)}
  </section>
}
