import { useEffect, useRef, useState } from 'react'
import { api, getAuthEpoch, type AgentBudgetConfig } from '../api/client'
import { useAppStore } from '../store/app'

/** Owner 设置当前 World 新任务的共享决策额度；切换账户/World 后不应用迟到响应。 */
export function AgentBudgetSettings() {
  const user = useAppStore(state => state.user)
  const world = useAppStore(state => state.worldName)
  const [config, setConfig] = useState<AgentBudgetConfig | null>(null)
  const [limit, setLimit] = useState('')
  const [unlimited, setUnlimited] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const scope = useRef(0)
  useEffect(() => {
    const id = ++scope.current
    const epoch = getAuthEpoch()
    const controller = new AbortController()
    setConfig(null); setError(''); setSaved(false); setBusy(false)
    if (user?.is_owner) api.agentBudget(controller.signal).then(value => {
      if (id === scope.current && epoch === getAuthEpoch() && !controller.signal.aborted) {
        setConfig(value); setUnlimited(value.effective_limit === null); setLimit(String(value.effective_limit ?? 8))
      }
    }).catch(() => { if (!controller.signal.aborted) setError('无法加载任务预算。') })
    return () => { controller.abort(); scope.current++ }
  }, [user?.id, user?.is_owner, world, reload])

  /** 保存时带读取版本，拒绝静默覆盖其他页面的修改。 */
  async function save() {
    if (!config) return
    const value = Number(limit)
    if (!unlimited && (!Number.isSafeInteger(value) || value < 1)) {
      setError('请输入正整数，且不超过浏览器可精确表示的 9007199254740991。'); return
    }
    const id = scope.current, epoch = getAuthEpoch()
    setBusy(true); setError(''); setSaved(false)
    try {
      const result = await api.setAgentBudget(unlimited ? null : value, config.revision)
      if (id === scope.current && epoch === getAuthEpoch()) { setConfig(result); setSaved(true) }
    } catch {
      if (id === scope.current && epoch === getAuthEpoch()) setError('保存失败；配置可能已变化，请重新加载后确认。')
    } finally {
      if (id === scope.current && epoch === getAuthEpoch()) setBusy(false)
    }
  }
  if (!user?.is_owner) return null
  return <section aria-label="任务决策预算" className="rounded-2xl border border-slate-700 bg-slate-950/60 p-4 space-y-3 text-slate-300">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <label htmlFor="agent-decision-limit" className="text-sm font-semibold text-slate-100">每个任务的决策上限</label>
      <input id="agent-decision-limit" type="number" min={1} max={Number.MAX_SAFE_INTEGER} value={unlimited ? '' : limit} placeholder={unlimited ? '不限次数' : '输入次数'}
        disabled={!config || busy || unlimited} onChange={event => { setLimit(event.target.value); setSaved(false) }}
        className="w-28 rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400" />
    </div>
    <div role="group" aria-label="决策限制模式" className="flex w-fit gap-1 rounded-xl bg-slate-900 p-1">
      {[false,true].map(value=><button key={String(value)} type="button" aria-pressed={unlimited===value}
        disabled={!config || busy} onClick={()=>{setUnlimited(value);setSaved(false);setError('')}}
        className={`rounded-lg px-3 py-2 ${unlimited===value?'bg-panel text-indigo-500 shadow-sm':'text-slate-500'}`}>{value?'不限次数':'自定义次数'}</button>)}
    </div>
    <p className="leading-relaxed">一次用户消息触发一个任务，群聊各角色共用额度。仅新任务生效，排队和执行中的任务保持原预算。</p>
    <div className="flex flex-wrap items-center gap-2">
      {[8, 32, 64, 128, 256].map(value => <button type="button" key={value}
        disabled={!config || busy} onClick={() => { setLimit(String(value)); setUnlimited(false); setSaved(false); setError('') }}
        className={`rounded-lg border px-3 py-1.5 ${!unlimited && Number(limit) === value ? 'border-indigo-400 bg-indigo-900/50 text-indigo-300' : 'border-slate-700 bg-slate-900'}`}>{value} 次</button>)}
      <span className="text-slate-400">{unlimited ? '不限决策次数' : '可输入其他次数'}</span>
    </div>
    <p className="text-slate-400">一次决策可提出多个工具调用。不限次数时仍可随时停止，权限与单次超时保持有效。此设置不代表费用或执行时间上限。</p>
    {error && <p role="alert" className="text-red-300">{error}</p>}
    {saved && <p role="status" className="text-emerald-300">已保存，仅新任务生效。</p>}
    <div className="flex gap-2">
      <button type="button" disabled={!config || busy} onClick={() => void save()} className="rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50">{busy ? '保存中…' : '保存任务预算'}</button>
      <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)} className="rounded-lg border border-slate-700 px-3 py-2">重新加载预算</button>
    </div>
  </section>
}
