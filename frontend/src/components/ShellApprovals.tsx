import { useEffect, useRef, useState } from 'react'
import { api, getAuthEpoch, type ShellApproval } from '../api/client'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'

/** 将不可见控制符显示为转义，避免脚本审批界面被双向文本或退格欺骗。
 * @param script Owner 有权查看的原始脚本；只改变显示，不改变摘要或执行内容。
 */
export function visibleScript(script: string) {
  return script.replace(/[\u0000-\u0008\u000b-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
}

/** 独立的 Owner 审批视图，脚本不进入共享消息或跨会话缓存。
 * @param conversationId 当前会话 ID，切换时取消旧读取。
 */
export function ShellApprovals({ conversationId }: { conversationId: number }) {
  const user = useAppStore((state) => state.user)
  const world = useAppStore((state) => state.worldName)
  const version = useChatStore((state) => state.approvalVersion)
  const subscription = useChatStore((state) => state.subscription)
  const [rows, setRows] = useState<ShellApproval[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [reload, setReload] = useState(0)
  const operation = useRef(0)
  useEffect(() => {
    const sequence = ++operation.current
    const epoch = getAuthEpoch()
    setRows([])
    setError(null)
    setBusy(null)
    if (!user?.is_owner || subscription !== 'ready') return
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 30_000)
    api.shellApprovals(conversationId, controller.signal).then((items) => {
      if (sequence === operation.current && epoch === getAuthEpoch()) setRows(items)
    }).catch(() => {
      if (sequence === operation.current && epoch === getAuthEpoch()) setError('审批列表加载失败，请重试。')
    }).finally(() => window.clearTimeout(timeout))
    return () => { ++operation.current; controller.abort(); window.clearTimeout(timeout) }
  }, [conversationId, user?.id, user?.is_owner, world, version, subscription, reload])

  /** 决定只绑定到当前所见摘要，迟到响应不得写回另一身份/会话。
   * @param row 当前展示的审批记录。
   * @param decision 用户显式点击的决定。
   */
  async function decide(row: ShellApproval, decision: 'approve' | 'reject') {
    const sequence = operation.current
    const epoch = getAuthEpoch()
    setBusy(row.id)
    setError(null)
    try {
      await api.decideShell(conversationId, row.id, decision, row.request_digest)
      if (sequence === operation.current && epoch === getAuthEpoch()) setReload((value) => value + 1)
    } catch {
      if (sequence === operation.current && epoch === getAuthEpoch()) setError('审批未成功，请刷新审批列表后重试。')
    } finally {
      if (sequence === operation.current) setBusy(null)
    }
  }
  if (!user?.is_owner || (!rows.length && !error)) return null
  return <aside aria-label="Shell 审批" className="max-h-80 shrink-0 overflow-y-auto border-t border-amber-800/60 bg-amber-950/20 px-4 py-3">
    {error && <p role="alert" className="text-xs text-amber-300">{error}
      <button type="button" className="ml-3 underline" onClick={() => setReload((value) => value + 1)}>刷新审批列表</button>
    </p>}
    {rows.map((row) => <section key={row.id} className="space-y-2 text-xs text-slate-300">
      <h3 className="font-semibold text-amber-300">等待 Owner 批准{row.runtime_id ? '后台服务启动' : '本次 Shell'}</h3>
      <p>世界：{row.world_name} · 工作区：{row.workspace_name} · {row.shell_kind}</p>
      <p className="break-all">执行目录：{row.root_path}</p>
      {row.runtime_id && <p>后台服务 · 端口 {row.port} · 探针 {row.health_path} · 就绪等待 {row.ready_timeout_seconds} 秒 · 最长运行 {row.lifetime_seconds} 秒；回答结束后继续运行。</p>}
      {row.runtime_id && <p className="text-amber-300">后台脚本可能创建或修改文件；启动失败或停止成功不代表这些副作用已回滚。</p>}
      <p className="text-amber-300">审批不是系统沙箱：脚本可以访问宿主其他路径和网络。请完整检查后再批准。</p>
      {!row.runtime_id && <p role="note" className="text-amber-300">
        {(row.active_service_count ?? 0) > 0 ? `读取审批时，该工作区有 ${row.active_service_count} 个未结束的服务实例（含待审批或回收中）。` : ''}
        本次 Shell 可能修改服务正在使用的文件或影响服务运行，并非只读操作；服务数量可能变化，请确认影响后批准。
      </p>}
      <pre aria-label="待审批脚本" dir="ltr" className="max-h-44 overflow-auto rounded-lg bg-slate-950 p-3 whitespace-pre-wrap break-all">{visibleScript(row.script)}</pre>
      <p>{row.runtime_id ? '后台日志采用有界环形保留' : `超时上限 ${row.timeout_seconds} 秒 · 输出保留 ${row.output_bytes} 字节`} · 审批有效至 {new Date(row.expires_at).toLocaleTimeString('zh-CN', { hour12: false })}。控制字符以转义显示。</p>
      <div className="flex gap-3">
        <button type="button" disabled={busy !== null} onClick={() => void decide(row, 'reject')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-50">拒绝{row.runtime_id ? '后台服务启动' : '本次 Shell'}</button>
        <button type="button" disabled={busy !== null} onClick={() => void decide(row, 'approve')} className="rounded bg-amber-700 px-3 py-2 text-white disabled:opacity-50">批准{row.runtime_id ? '后台服务启动' : '本次 Shell'}</button>
      </div>
    </section>)}
  </aside>
}
