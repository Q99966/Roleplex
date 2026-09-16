import { useEffect, useRef, useState, type FormEvent } from 'react'
import { getAuthEpoch } from '../api/client'
import { useAppStore } from '../store/app'

const errors: Record<string, string> = {
  WORLD_ALREADY_EXISTS: '这个世界名称已被占用，请换一个名称。',
  WORLD_NAME_INVALID: '世界名称无效，请勿使用路径、控制字符或特殊字符 <>:"/\\|?*。',
  WORLD_OPERATION_REQUIRES_MANAGED: '当前运行模式不支持创建世界，请使用世界包装器启动。',
  WORLD_OPERATION_IN_PROGRESS: '正在切换世界，请完成切换后再创建。',
  WORLD_OPERATION_FAILED: '创建未完成，请检查存储空间与目录权限，刷新世界列表后再试。',
}

/** Owner 创建空世界；保留当前会话，后续切换沿用既有回收确认与重新认证流程。 */
export function WorldCreate() {
  const { user, worldCreationSupported, switchingWorld, createWorld, worldSwitchingSupported } = useAppStore()
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState('')
  const pending = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (pending.current || !name.trim() || switchingWorld || !worldCreationSupported) return
    pending.current = true
    setBusy(true); setError(''); setCreated('')
    const epoch = getAuthEpoch()
    try {
      const world = await createWorld(name.trim())
      if (!mounted.current || epoch !== getAuthEpoch()) return
      setCreated(world.name); setName('')
    } catch (cause) {
      if (!mounted.current || epoch !== getAuthEpoch()) return
      const code = (cause as { code?: string })?.code ?? ''
      setError(errors[code] ?? '未能确认创建结果，请刷新世界列表后检查，再决定是否重试。')
    } finally {
      pending.current = false
      if (mounted.current && epoch === getAuthEpoch()) setBusy(false)
    }
  }

  if (!user?.is_owner) return null
  return <section className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
    <h4 className="mb-1 text-xs font-semibold text-slate-300">创建新世界</h4>
    <p id="world-create-help" className="mb-3 text-[11px] text-slate-500">
      新世界拥有独立的账号、聊天记录和模型配置。创建后可在下方切换，首次进入需注册 Owner。
    </p>
    <form onSubmit={(event) => void submit(event)} className="space-y-2">
      <label className="block text-xs text-slate-300" htmlFor="world-create-name">世界名称</label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input id="world-create-name" value={name} onChange={(event) => setName(event.target.value)}
          required maxLength={64} autoComplete="off" aria-describedby="world-create-help"
          disabled={busy || Boolean(switchingWorld) || !worldCreationSupported}
          placeholder="例如：写作空间" className="min-w-0 flex-1 rounded-xl border border-slate-800 bg-slate-900 px-3 py-2.5 text-slate-100 outline-none focus:border-indigo-500 disabled:opacity-50" />
        <button type="submit" disabled={busy || !name.trim() || Boolean(switchingWorld) || !worldCreationSupported}
          className="rounded-xl bg-indigo-600 px-4 py-2.5 font-semibold text-white hover:bg-indigo-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400 disabled:opacity-50">
          {busy ? '正在创建…' : '创建世界'}
        </button>
      </div>
      {!worldCreationSupported && <p className="text-[11px] text-slate-500">当前运行模式不支持创建世界，请使用世界包装器启动。</p>}
      {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
      {created && <p role="status" className="text-xs text-emerald-400">
        世界“{created}”已创建，当前世界保持不变。{worldSwitchingSupported ? '请在下方选择新世界以切换。' : '请通过世界包装器启动新世界。'}
      </p>}
    </form>
  </section>
}
