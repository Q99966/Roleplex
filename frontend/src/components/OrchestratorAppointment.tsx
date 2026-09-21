import { useState } from 'react'
import { api, getAuthEpoch, type Conversation } from '../api/client'
import { useAppStore } from '../store/app'

/** 任命只属于当前群；更换会封闭旧协调执行，不修改角色全局身份。 */
export function OrchestratorAppointment({ conversation }: { conversation: Conversation }) {
  const { user, roles } = useAppStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!user?.is_owner || conversation.type !== 'group') return null
  async function change(role: string) {
    const epoch = getAuthEpoch()
    setBusy(true); setError('')
    try {
      const updated = await api.appointOrchestrator(conversation.id, role ? Number(role) : null, conversation.revision)
      if (epoch !== getAuthEpoch()) return
      useAppStore.setState(state => ({ conversations: state.conversations.map(c => c.id === updated.id && c.revision <= updated.revision ? updated : c) }))
    } catch (cause) {
      if (epoch === getAuthEpoch()) setError((cause as { code?: string }).code === 'CONVERSATION_REVISION_CONFLICT' ? '群成员或配置已变化，请刷新后重新选择。' : '任命失败，请检查角色是否仍为有效群成员。')
    } finally { if (epoch === getAuthEpoch()) setBusy(false) }
  }
  return <section className="space-y-2 rounded-xl border border-slate-700 bg-panel p-3 text-xs">
    <label className="block">群协调者<select aria-label="任命群协调者" disabled={busy}
      value={conversation.orchestrator_enabled && (conversation.orchestrator_revision ?? 0) > 0 ? conversation.orchestrator_role_id ?? '' : ''}
      onChange={e => void change(e.target.value)} className="mt-2 w-full rounded-lg border border-slate-700 bg-panel p-2">
      <option value="">不任命 / 取消任命</option>{roles.filter(r => r.active && conversation.role_ids.includes(r.id)).map(r => <option value={r.id} key={r.id}>{r.name}</option>)}
    </select></label>
    <p className="text-slate-500">仅“协调执行”使用此角色；普通 @ 不变。更换或取消后，旧协调运行会收口。</p>
    {error && <p role="alert" className="text-red-500">{error}</p>}
  </section>
}
