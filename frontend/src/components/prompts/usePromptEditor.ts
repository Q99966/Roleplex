import { useEffect, useRef, useState } from 'react'
import { getAuthEpoch } from '../../api/client'

type Revision = { revision: number }
type Editing<T, D> = { baseline: T; latest: T; draft: D }
// 仅在本次登录中保留面板草稿；不把私有提示词存入通用 localStorage 或跨 World 复用。
const drafts = new Map<string, unknown>()
let draftEpoch = -1
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** 按准确配置对象保留编辑；刷新和 409 更新远端版本，始终由用户选择是否采用。 */
export function usePromptEditor<T extends Revision, D>(key: string, options: {
  read: (signal?: AbortSignal) => Promise<T>; write: (values: D, revision: number) => Promise<T>; values: (record: T) => D;
  onSaved?: () => void
}) {
  const epoch = getAuthEpoch(), settings = useRef(options)
  settings.current = options
  const live = useRef(false), generation = useRef(0), pending = useRef(false)
  const active = useRef<Editing<T, D> | null>(null)
  const [editing, setEditing] = useState<Editing<T, D> | null>(null)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const current = () => live.current && epoch === getAuthEpoch()

  function publish(value: Editing<T, D>) {
    if (!current()) return
    active.current = value; setEditing(value)
    if (draftEpoch !== epoch) { drafts.clear(); draftEpoch = epoch }
    drafts.set(key, value)
  }
  function received(latest: T) {
    const old = active.current, values = settings.current.values
    if (old && !equal(old.draft, values(old.baseline)) && !equal(old.draft, values(latest))) publish({ ...old, latest })
    else publish({ baseline: latest, latest, draft: values(latest) })
  }
  async function refresh() {
    const ticket = ++generation.current
    try {
      const value = await settings.current.read()
      if (current() && ticket === generation.current) { received(value); setError('') }
    } catch { if (current() && ticket === generation.current) setError('配置读取失败，请重试。当前输入仍保留。') }
  }
  useEffect(() => {
    live.current = true
    const cached = draftEpoch === epoch ? drafts.get(key) as Editing<T, D> | undefined : undefined
    if (cached) publish(cached)
    void refresh()
    return () => { live.current = false; generation.current++ }
  }, [key, epoch])

  function change(draft: D) {
    if (!active.current || pending.current) return
    publish({ ...active.current, draft }); setError('')
  }
  async function save() {
    const value = active.current
    if (!value || pending.current) return
    pending.current = true; setBusy(true); setError('')
    generation.current++
    try {
      const result = await settings.current.write(value.draft, value.baseline.revision)
      if (current()) {
        publish({ baseline: result, latest: result, draft: settings.current.values(result) })
        settings.current.onSaved?.()
      }
    } catch (cause) {
      if (current()) {
        const code = (cause as { code?: string }).code
        await refresh()
        if (current()) {
          const observed = active.current
          if (observed && observed.latest.revision > value.baseline.revision && equal(settings.current.values(observed.latest), value.draft)) {
            setError(''); settings.current.onSaved?.()
          } else setError(code === 'PROMPT_REVISION_CONFLICT' ? '配置已更新，当前输入已保留。请核对服务端版本后处理。' : '保存未能确认，请核对刷新结果。当前输入仍保留。')
        }
      }
    } finally { pending.current = false; if (current()) setBusy(false) }
  }
  return { editing, busy, error, change, save, refresh,
    dirty: Boolean(editing && !equal(editing.draft, options.values(editing.baseline))),
    conflict: Boolean(editing && editing.latest.revision !== editing.baseline.revision),
    adopt: () => { if (active.current && !pending.current) { const latest = active.current.latest; publish({ baseline: latest, latest, draft: settings.current.values(latest) }); setError('') } },
  }
}
