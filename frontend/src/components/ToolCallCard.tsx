import { useEffect, useState } from 'react'
import { ChevronDown, Loader2, Terminal } from 'lucide-react'
import { api, type Part, type ToolCapture, type ToolDetails } from '../api/client'

const STATUS: Record<string, string> = {
  running: '执行中', success: '已完成', failed: '失败', rejected: '已拒绝', cancelled: '已取消', interrupted: '已中断',
}

/** 渲染经授权返回的纯文本，禁止 HTML 或终端序列执行。
 * @param title 区域的可访问名称。
 * @param capture 有界输入或输出；null 表示尚未采集。
 */
function CaptureText({ title, capture }: { title: string; capture: ToolCapture | null }) {
  let streams: { stdout: string; stderr: string } | null = null
  if (title === '工具输出' && capture) {
    try {
      const value = JSON.parse(capture.text.replace(/^\[(?:工具执行失败|工具被拒绝)\]\s*/, ''))
      if (typeof value?.stdout === 'string' && typeof value?.stderr === 'string') streams = value
    } catch { /* 截断或非 JSON 输出保持原始文本展示。 */ }
  }
  return <section className="mt-3">
    <h5 className="mb-1 flex items-center justify-between font-medium text-slate-300">
      {title}<span className="text-slate-500">{capture ? `${capture.bytes} 字节${capture.truncated ? ' · 已截断' : ''}` : '未记录'}</span>
    </h5>
    {capture && (streams ? <div role="region" aria-label={title} className="space-y-2">
      {(['stdout', 'stderr'] as const).map((stream) => <div key={stream}>
        <p className="mb-1 text-[10px] text-slate-500">{stream === 'stdout' ? '标准输出' : '标准错误'}</p>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-300">{streams![stream] || '（空内容）'}</pre>
      </div>)}
    </div> : <pre role="region" aria-label={title} className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-300">{capture.text || '（空内容）'}</pre>)}
  </section>
}

/** 默认折叠的工具过程；Owner 主动展开才请求私有详情，关闭即释放正文。
 * @param part 当前调用的安全元数据。
 * @param conversationId 所属会话，参与服务端归属校验。
 * @param messageId 所属消息。
 * @param isOwner 当前账号能否请求 Owner 接口。
 */
export function ToolCallCard({ part, conversationId, messageId, isOwner }: {
  part: Part; conversationId: number; messageId: number; isOwner: boolean
}) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<ToolDetails | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const callId = part.call_id ?? ''
  const controlId = `tool-detail-${messageId}-${callId}`
  useEffect(() => {
    setDetail(null)
    setFailed(false)
    setLoading(false)
    if (!open || !isOwner || !part.detail_available || !callId) return
    const controller = new AbortController()
    let active = true
    setLoading(true)
    api.toolDetails(conversationId, messageId, callId, controller.signal).then((value) => {
      if (active) setDetail(value)
    }).catch(() => { if (active) setFailed(true) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false; controller.abort() }
  }, [open, isOwner, conversationId, messageId, callId, part.status, part.detail_available])
  const status = part.error_code === 'EXECUTION_INTERRUPTED' ? '已中断' : STATUS[part.status ?? ''] ?? '状态未知'
  return <div data-testid="tool-call-card" className="my-3 overflow-hidden rounded-xl border border-slate-700/70 bg-slate-950/60 text-xs">
    <button type="button" aria-expanded={open} aria-controls={controlId}
      aria-label={`执行详情：${part.tool_name ?? '工具'}${part.command ? ` · ${part.command}` : ''}`}
      onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-slate-800/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400">
      {part.status === 'running' ? <Loader2 size={14} className="animate-spin text-indigo-300" /> : <Terminal size={14} className="text-slate-500" />}
      <span className="min-w-0 flex-1 break-all font-mono text-indigo-300">{part.tool_name ?? '工具'}</span>
      <span className={part.status === 'failed' || part.status === 'rejected' ? 'text-red-400' : part.status === 'running' ? 'text-indigo-300' : 'text-slate-400'}>{status}</span>
      <ChevronDown size={14} className={`shrink-0 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
    </button>
    <div className="flex flex-wrap gap-x-3 gap-y-1 px-3 pb-2 text-[10px] text-slate-500">
      {part.command && <span>命令 {part.command}</span>}
      {typeof part.duration_ms === 'number' && <span>耗时 {part.duration_ms}ms</span>}
      {typeof part.exit_code === 'number' && <span>退出码 {part.exit_code}</span>}
      {part.truncated && <span className="text-amber-400">输出已截断</span>}
      {part.command_status === 'timed_out' && <span className="text-amber-400">命令超时，进程已回收</span>}
      {part.error_code && <span className="text-red-400">{part.error_code}</span>}
    </div>
    {open && <div id={controlId} className="border-t border-slate-800 px-3 pb-3 pt-2 text-slate-400">
      {!isOwner ? <p>详细输入和输出仅 Owner 可见。</p> : !part.detail_available ? <p>此调用未记录执行详情。</p> : <>
        {loading && <p role="status">正在加载执行详情…</p>}
        {failed && <p role="alert">无法加载执行详情，请收起后重试。</p>}
        {detail?.availability === 'expired' && <p>执行详情已超过 7 天保留期。</p>}
        {detail?.availability === 'unavailable' && <p>执行详情不可用，无法解密原始记录。</p>}
        {detail?.availability === 'not_recorded' && <p>此调用未记录执行详情。</p>}
        {detail?.availability === 'available' && <>
          <p>开始：{detail.started_at ? new Date(detail.started_at).toLocaleString('zh-CN', { hour12: false }) : '未记录'}</p>
          <p>结束：{detail.ended_at ? new Date(detail.ended_at).toLocaleString('zh-CN', { hour12: false }) : detail.status === 'running' ? '执行中，等待工具结果' : '未记录结束时间'}</p>
          <CaptureText title="工具输入" capture={detail.input} />
          <CaptureText title="工具输出" capture={detail.output} />
        </>}
      </>}
    </div>}
  </div>
}
