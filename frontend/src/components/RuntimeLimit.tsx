import { useEffect, useState } from 'react'
import { api, getAuthEpoch, type RuntimeConfig, type RuntimeScope } from '../api/client'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'

/** Owner 配置三层限额，降低上限不隐式回收。
 * @param scope 当前层级。
 * @param id 当前 World 中的资源身份。
 */
export function RuntimeLimit({ scope, id }: { scope: RuntimeScope; id: number }) {
  const user = useAppStore((state) => state.user)
  const world = useAppStore((state) => state.worldName)
  const runtimeVersion = useChatStore((state) => state.runtimeVersion)
  const [config, setConfig] = useState<RuntimeConfig | null>(null)
  const [limit, setLimit] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    setConfig(null); setError('')
    if (!user?.is_owner) return
    const controller = new AbortController()
    const epoch = getAuthEpoch()
    api.runtimeConfig(scope, id, controller.signal).then((value) => {
      if (!controller.signal.aborted && epoch === getAuthEpoch()) { setConfig(value); setLimit(String(value.limit)) }
    }).catch(() => { if (!controller.signal.aborted) setError('无法加载进程配额。') })
    return () => controller.abort()
  }, [scope, id, user?.id, user?.is_owner, world, reload, runtimeVersion])
  /** @param enabled 是否同时改变工作区后台服务开关。 */
  async function save(enabled?: boolean) {
    if (!config) return
    const epoch = getAuthEpoch()
    setBusy(true); setError('')
    try {
      let confirmed = false
      if (enabled === false && config.services_enabled) {
        const preview = await api.cleanupPreview(scope, id)
        confirmed = confirm(`关闭后台服务会逐项停止以下实例：\n${preview.items.map((item) => `会话 ${item.conversation_id} · ${item.tool_name} · ${item.id.slice(0, 8)}`).join('\n') || '当前没有运行实例'}\n继续吗？`)
        if (!confirmed) return
      }
      const value = await api.setRuntimeConfig({ scope, scope_id: id, limit: Number(limit), expected_revision: config.revision,
        services_enabled: enabled, confirm_cleanup: confirmed })
      if (epoch === getAuthEpoch()) { setConfig(value); setReload((v) => v + 1) }
    } catch (err) { if (epoch === getAuthEpoch()) setError(err instanceof Error ? err.message : '保存失败') }
    finally { if (epoch === getAuthEpoch()) setBusy(false) }
  }
  if (!user?.is_owner) return null
  const label = `${scope === 'world' ? 'World' : scope === 'workspace' ? '工作区' : '会话'}进程上限`
  return <section className="my-3 space-y-2 rounded-lg border border-slate-700 p-3 text-xs text-slate-300">
    <label className="flex items-center gap-3">{label}
      <input aria-label={label} type="number" min="1" max="2147483647" value={limit} onChange={(event) => setLimit(event.target.value)} className="w-24 rounded bg-slate-950 p-2" />
      <button type="button" disabled={busy || !config} onClick={() => void save()} className="rounded bg-indigo-600 px-3 py-2 text-white disabled:opacity-40">保存{label}</button>
    </label>
    {config && <p>已用及预留 {config.used}/{config.limit}。三层限制同时生效，降低上限不会自动停止已有进程。</p>}
    {scope === 'workspace' && config && <button type="button" disabled={busy || !config.services_supported}
      onClick={() => void save(!config.services_enabled)} className="rounded border border-amber-700 px-3 py-2 text-amber-300 disabled:opacity-40">
      后台服务 · {!config.services_supported ? '本平台未开放' : config.services_enabled ? '已开启' : '关闭'}
    </button>}
    {error && <p role="alert" className="text-red-300">{error}<button type="button" onClick={() => setReload((v) => v + 1)} className="ml-2 underline">重新加载</button></p>}
  </section>
}
