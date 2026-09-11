import { useEffect, useRef, useState } from 'react'
import { api, getAuthEpoch, type RuntimeProcess, type RuntimeLogPage } from '../api/client'
import { useChatStore } from '../store/chat'
import { RuntimeLimit } from './RuntimeLimit'
import { ToolCallCard } from './ToolCallCard'
import { visibleScript } from './ShellApprovals'

const LABELS: Record<string, string> = { pending: '待审批（尚无进程）', starting: '启动中', waiting_ready: '等待 HTTP 就绪',
  ready: '就绪', unhealthy: 'HTTP 不健康', running: '运行中', stopping: '回收中', stopped: '已停止', exited: '已退出',
  failed: '失败', rejected: '已拒绝', expired: '已过期', interrupted: '已中断', cleanup_required: '清理待确认' }
const TERMINAL = ['stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted']

/** /ps 只读取后端登记，不调用模型或系统 ps。
 * @param conversationId 当前会话。
 * @param onClose 关闭面板并释放私有正文。
 */
export function ProcessPanel({ conversationId, onClose }: { conversationId: number; onClose: () => void }) {
  const version = useChatStore((state) => state.runtimeVersion)
  const subscription = useChatStore((state) => state.subscription)
  const [rows, setRows] = useState<RuntimeProcess[]>([])
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [log, setLog] = useState<RuntimeLogPage | null>(null)
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof api.runtimeDetail>> | null>(null)
  const logSequence = useRef(0)
  const logRequest = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    const epoch = getAuthEpoch()
    api.runtimeProcesses(conversationId, controller.signal).then((value) => {
      if (!controller.signal.aborted && epoch === getAuthEpoch()) setRows(value.items)
    }).catch(() => { if (!controller.signal.aborted) setError('进程列表加载失败。') })
    return () => controller.abort()
  }, [conversationId, version, subscription, reload])
  useEffect(() => {
    setSelected(null); setLog(null); setDetail(null)
    return () => { ++logSequence.current; logRequest.current?.abort() }
  }, [conversationId])
  /** @param row 用户选择的登记实例。 */
  async function stop(row: RuntimeProcess) {
    const epoch = getAuthEpoch()
    setError('')
    try { await api.stopRuntime(conversationId, row.id); if (epoch === getAuthEpoch()) setReload((v) => v + 1) }
    catch (err) { if (epoch === getAuthEpoch()) setError(err instanceof Error ? err.message : '停止失败') }
  }
  /** @param row 选择的服务。
   * @param after 已读取的观察序号。
   */
  async function readLog(row: RuntimeProcess, after = 0) {
    const epoch = getAuthEpoch()
    const sequence = ++logSequence.current
    logRequest.current?.abort()
    const controller = new AbortController()
    logRequest.current = controller
    setSelected(row.id)
    setLog(null)
    setDetail(null)
    try {
      const page = await api.runtimeLogs(conversationId, row.id, after, controller.signal)
      if (epoch === getAuthEpoch() && sequence === logSequence.current) setLog(page)
    } catch { if (epoch === getAuthEpoch() && sequence === logSequence.current && !controller.signal.aborted) setError('日志读取失败。') }
  }
  /** @param row 查询原审批或既有命令详情，不把它们复制进共享历史缓存。 */
  async function readDetail(row: RuntimeProcess) {
    const epoch = getAuthEpoch()
    const sequence = ++logSequence.current
    logRequest.current?.abort()
    const controller = new AbortController()
    logRequest.current = controller
    setSelected(row.id); setLog(null); setDetail(null)
    try {
      const result = await api.runtimeDetail(conversationId, row.id, controller.signal)
      if (epoch === getAuthEpoch() && sequence === logSequence.current) setDetail(result)
    } catch { if (epoch === getAuthEpoch() && sequence === logSequence.current && !controller.signal.aborted) setError('详情读取失败。') }
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
    <section role="dialog" aria-modal="true" aria-label="会话进程与详情" className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 text-slate-300">
      <header className="flex justify-between"><h2 className="font-bold text-white">会话进程与详情</h2><button type="button" onClick={onClose}>关闭进程面板</button></header>
      <RuntimeLimit scope="conversation" id={conversationId} />
      <p className="mb-3 text-xs text-slate-400">仅展示 Roleplex 登记的当前会话实例。loopback 地址属于后端主机，不代表远程浏览器可访问。</p>
      <button type="button" onClick={() => setReload((v) => v + 1)} className="mb-3 rounded border border-slate-600 p-2 text-xs">刷新进程</button>
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
      {!rows.length && <p>当前会话没有登记的进程。</p>}
      {rows.map((row) => <article key={row.id} className="my-3 rounded-xl border border-slate-700 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-3"><strong>{row.kind === 'service' ? '后台服务' : '一次性命令'} · {row.id.slice(0, 8)}</strong>
          <span>{LABELS[row.state] ?? row.state}</span>{row.port !== null && <span>端口 {row.port}</span>}{row.pid !== null && <span>根进程 PID {row.pid}</span>}
        </div>
        <p className="mt-2 text-slate-400">工作区 {row.workspace_id} · {row.tool_name} · {row.health_code ? `HTTP ${row.health_code}` : '尚无 HTTP 就绪证据'}</p>
        <p className="mt-1 text-slate-400">来源角色 {row.role_id} · execution {row.execution_id.slice(0, 8)}</p>
        <p className="mt-1 text-slate-400">开始：{row.started_at ? new Date(row.started_at).toLocaleString('zh-CN') : '尚未启动'} · 到期：{row.expires_at ? new Date(row.expires_at).toLocaleString('zh-CN') : '不适用'}</p>
        {row.error_code && <p className="text-red-300">{row.error_code}</p>}
        {row.state === 'cleanup_required' && <p className="mt-2 text-amber-300">缺少完整回收证明，此项继续占用名额。可查看来源与后端日志并重试核查；不能仅凭根 PID 消失认定后代已结束。</p>}
        <div className="mt-3 flex gap-3">
          {!TERMINAL.includes(row.state) && <button type="button" onClick={() => void stop(row)} className="rounded bg-red-950 px-3 py-2 text-red-200">停止 {row.id.slice(0, 8)}</button>}
          <button type="button" onClick={() => void readDetail(row)} className="rounded bg-slate-800 px-3 py-2">查看详情 {row.id.slice(0, 8)}</button>
          {row.kind === 'service' && <button type="button" onClick={() => void readLog(row)} className="rounded bg-slate-800 px-3 py-2">查看日志 {row.id.slice(0, 8)}</button>}
        </div>
        {selected === row.id && detail && <div className="mt-3">
          {detail.request && <><p className="break-all">原执行目录：{detail.request.root_path}</p>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-3">{visibleScript(detail.request.script)}</pre></>}
          {detail.command_detail && <ToolCallCard part={detail.command_detail.part} conversationId={conversationId} messageId={detail.command_detail.message_id} isOwner />}
          {!detail.request && !detail.command_detail && <p>原始详情未记录或已不可用。</p>}
        </div>}
        {selected === row.id && log && <div className="mt-3">
          {log.gap && <p className="text-amber-300">日志存在缺口，较早内容可能已淘汰。</p>}
          {log.availability !== 'available' && <p>日志{log.availability === 'expired' ? '已过期' : log.availability === 'evicted' ? '已因存储预算淘汰' : '未记录或不可用'}；一次性命令也可在聊天工具卡查看执行详情。</p>}
          <div role="region" aria-label="服务日志" className="max-h-64 overflow-auto rounded bg-slate-950 p-3">
            {log.items.map((item) => <pre key={item.seq} className="whitespace-pre-wrap break-all">[{item.stream}] {item.text}</pre>)}
          </div>
          <button type="button" onClick={() => void readLog(row, log.next_seq)} className="mt-2 underline">读取后续日志</button>
        </div>}
      </article>)}
    </section>
  </div>
}
