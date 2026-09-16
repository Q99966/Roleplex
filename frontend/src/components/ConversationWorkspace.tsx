import { useEffect, useRef, useState, type FormEvent } from 'react'
import { FolderKanban } from 'lucide-react'
import { api, getAuthEpoch, type Conversation } from '../api/client'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'

const errors: Record<string, string> = {
  WORKSPACE_BUSY: '工作区或会话正在执行任务，请先停止或等待完成，再重新选择。',
  WORKSPACE_UNAVAILABLE: '工作区已停用或目录不可用，请检查工作区设置。',
  WORKSPACE_NOT_FOUND: '工作区不存在或无权访问，请重新选择。',
  CONVERSATION_REVISION_CONFLICT: '会话已在其他位置更新，请关闭后重新选择工作区。',
  RUNTIME_CLEANUP_CONFIRM_REQUIRED: '会话新增了运行实例，请重新保存并确认回收。',
  RUNTIME_CLEANUP_UNCONFIRMED: '尚未确认全部进程已回收，请在会话进程中检查后重试。',
}

/** 会话级绑定入口；显式保存、版本检测和进程回收确认，迟到响应不能更新其他身份的会话。 */
export function ConversationWorkspace({ conversation }: { conversation: Conversation }) {
  const { user, workspaceBindings, loadWorkspaceBindings } = useAppStore()
  const active = useChatStore((state) => state.activeGenerationIds.length > 0)
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState('')
  const [revision, setRevision] = useState(conversation.revision)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const alive = useRef(true)
  const pending = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const binding = workspaceBindings.find((item) => item.id === conversation.workspace_binding_id)
  const label = conversation.workspace_binding_id === null ? '未绑定工作区' : binding?.display_name ?? '已绑定工作区（状态待刷新）'

  async function expand() {
    setOpen(true); setError(''); setTarget(String(conversation.workspace_binding_id ?? ''))
    setRevision(conversation.revision); setLoading(true)
    const epoch = getAuthEpoch()
    try { await loadWorkspaceBindings() }
    catch { if (alive.current && epoch === getAuthEpoch()) setError('工作区列表加载失败，请关闭后重试。') }
    finally { if (alive.current && epoch === getAuthEpoch()) setLoading(false) }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (pending.current || active || loading) return
    const epoch = getAuthEpoch()
    pending.current = true; setBusy(true); setError('')
    try {
      const preview = await api.cleanupPreview('conversation', conversation.id)
      if (!alive.current || epoch !== getAuthEpoch()) return
      const confirmed = preview.items.length > 0
      if (confirmed && !confirm(`更换或解绑工作区前将回收 ${preview.items.length} 个会话实例，服务不会自动重启。\n${preview.items.map((item) => `${item.tool_name} · ${item.id.slice(0, 8)}`).join('\n')}\n继续吗？`)) return
      const updated = await api.updateConversationWorkspace(conversation.id, {
        workspace_binding_id: target ? Number(target) : null, expected_revision: revision, confirm_cleanup: confirmed,
      })
      if (!alive.current || epoch !== getAuthEpoch()) return
      useAppStore.setState((state) => ({ conversations: state.conversations.map((item) =>
        item.id === updated.id && item.revision <= updated.revision ? updated : item) }))
      setOpen(false)
    } catch (cause) {
      if (!alive.current || epoch !== getAuthEpoch()) return
      const code = (cause as { code?: string })?.code ?? ''
      setError(errors[code] ?? '工作区更新失败，请刷新后重试。')
    } finally {
      pending.current = false
      if (alive.current && epoch === getAuthEpoch()) setBusy(false)
    }
  }

  // Guest 不能枚举 Owner 的目录与配置；仅显示会话公开的绑定状态。
  if (!user?.is_owner) return <div className="rounded-2xl border border-slate-800 bg-panel p-4 text-xs text-slate-500">
    {conversation.workspace_binding_id === null ? '未绑定工作区' : '已绑定工作区 · 由 Owner 管理'}
  </div>
  return <section aria-label="会话工作区管理" className="rounded-2xl border border-slate-800 bg-panel p-4 text-xs">
    <button type="button" aria-expanded={open} onClick={() => open ? setOpen(false) : void expand()}
      disabled={busy} className="flex max-w-full items-center gap-2 rounded text-slate-400 hover:text-indigo-400 focus-visible:outline focus-visible:outline-indigo-500">
      <FolderKanban size={14} className="shrink-0" /><span className="truncate">工作区：{label}</span><span className="shrink-0">{open ? '收起' : '管理'}</span>
    </button>
    {open && <form onSubmit={(event) => void save(event)} className="mt-3 space-y-2 pb-1">
      <label htmlFor="conversation-workspace" className="block text-slate-400">会话工作区</label>
      <div className="flex flex-wrap gap-2">
        <select id="conversation-workspace" value={target} onChange={(event) => setTarget(event.target.value)}
          disabled={busy || loading || active} className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 p-2 text-slate-200 focus:border-indigo-500 disabled:opacity-50">
          <option value="">不绑定工作区</option>
          {target && !workspaceBindings.some((item) => String(item.id) === target) && <option value={target} disabled>当前工作区不可用</option>}
          {workspaceBindings.map((item) => <option key={item.id} value={item.id} disabled={item.availability !== 'available'}>
            {item.display_name}{item.availability !== 'available' ? '（忙碌或不可用）' : ''}
          </option>)}
        </select>
        <button type="submit" disabled={busy || loading || active || target === String(conversation.workspace_binding_id ?? '')}
          className="rounded-lg bg-indigo-600 px-3 py-2 text-white hover:bg-indigo-500 disabled:opacity-50">{busy ? '正在保存…' : '保存工作区'}</button>
      </div>
      {binding && <p className="break-all text-slate-500">当前目录：{binding.root_path}</p>}
      <p className="text-slate-500">{conversation.type === 'group' ? '群聊按 @ 顺序串行使用文件工具，每个角色需分别开启权限。' : '角色与工作区均需开启对应工具权限。'}</p>
      {active && <p className="text-amber-400">请先停止或等待当前消息链完成，再更换工作区。</p>}
      {loading && <p role="status">正在加载工作区…</p>}
      {error && <p role="alert" className="text-red-400">{error}</p>}
    </form>}
  </section>
}
