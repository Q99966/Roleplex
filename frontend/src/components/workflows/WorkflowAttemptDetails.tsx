import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { getAuthEpoch, type Message } from '../../api/client'
import { workflows, type WorkflowAttempt } from '../../api/workflows'
import { MessageParts } from '../MessageParts'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { FeedbackReportForm } from './WorkflowFeedback'
import { WorkflowDialog } from './WorkflowDialog'
const button = 'rounded-lg border border-slate-700 bg-panel px-3 py-2 text-xs hover:bg-slate-800 disabled:opacity-40'
const field = 'mt-1 w-full rounded-lg border border-slate-700 bg-panel p-2 text-sm text-slate-200'

/** 完整原消息、文件事实与重试核验按需打开，原始数据不挤占日常节点面板。 */
export function WorkflowAttemptDetails({ attempt, onClose }: { attempt: WorkflowAttempt; onClose: () => void }) {
  const w = useWorkflow()
  const [message, setMessage] = useState<Message | null>(null)
  const [facts, setFacts] = useState('')
  const [error, setError] = useState('')
  const [checked, setChecked] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [downstream, setDownstream] = useState(false)
  useEffect(() => {
    let live = true
    const epoch = getAuthEpoch()
    Promise.allSettled([workflows.message(w.conversation.id, w.run!.id, attempt.id), workflows.facts(w.conversation.id, w.run!.id, attempt.id)])
      .then(([msg, result]) => { if (live && epoch === getAuthEpoch()) {
        if (msg.status === 'fulfilled') setMessage(msg.value)
        if (result.status === 'fulfilled') setFacts(result.value.text)
        else setError('当前文件核对不可用；已有消息与结构化记录仍保留，请检查权限和资源。')
      } })
      .catch(() => { if (live && epoch === getAuthEpoch()) setError('详情或当前事实暂不可用，请核查权限和工作区状态后重试。') })
    return () => { live = false }
  }, [attempt.id])
  const terminal = !w.historyGraph && w.run && (w.run.runtime_version === 2 ? ['completed', 'failed', 'blocked', 'stopped', 'interrupted'].includes(attempt.status) && w.run.status !== 'stopping' : !['queued', 'running', 'waiting', 'stopping'].includes(w.run.status))
  return <WorkflowDialog title="节点尝试详情" onClose={onClose}>
      <div className="flex justify-between gap-3"><h4>尝试 {attempt.number} · {statusLabel(attempt.status)}</h4><button type="button" onClick={onClose} aria-label="关闭尝试详情"><X size={16} /></button></div>
      <p className="my-2 text-xs text-slate-500">{attempt.node_snapshot?.title ?? attempt.node_id} · 图 {attempt.graph_revision == null ? '旧记录' : `版本 ${attempt.graph_revision}`} · {attempt.execution_id ? `执行 ${attempt.execution_id.slice(0, 8)}` : '人工节点，无模型执行'} · {attempt.current ? '当前结果' : '历史结果，不作为当前下游输入'}</p>
      {attempt.result && <details className="my-3 text-xs"><summary>原始结构化结果</summary><pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(attempt.result, null, 2)}</pre></details>}
      {message && <div className="my-3"><MessageParts message={message} isOwner /></div>}
      {attempt.usage && <p className="text-xs">厂商用量：输入 {attempt.usage.input_tokens ?? '未知'} / 输出 {attempt.usage.output_tokens ?? '未知'}</p>}
      {message && <button type="button" className={`${button} my-2`} onClick={() => { w.setOpen(false); window.setTimeout(() => document.querySelector(`[data-reading-anchor="m-${message.id}"]`)?.scrollIntoView({ block: 'center' }), 0) }}>返回对话查看原消息</button>}
      {w.error && <p role="alert" className="text-xs text-red-500">{w.error}</p>}
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
      {facts && <details open className="my-3 text-xs"><summary>服务器核对事实</summary><pre className="mt-2 whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3">{facts}</pre></details>}
      {!w.historyGraph && w.run?.runtime_version === 2 && !attempt.node_id.startsWith('__') && <FeedbackReportForm attempt={attempt} />}
      {terminal && (attempt.current || attempt.selected_in_activation) && <div className="space-y-3 border-t border-slate-700 pt-3 text-xs">
        <label className="block">本次修订要求<textarea className={field} value={instruction} onChange={e => setInstruction(e.target.value)} /></label>
        <label className="flex gap-2"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />我已核对本次结果；未知副作用不作为自动重放依据</label>
        <label className="flex gap-2"><input type="checkbox" checked={downstream} onChange={e => setDownstream(e.target.checked)} />本节点结束后重新执行后续步骤（旧结果保留为历史）</label>
        <button type="button" className={button} disabled={!facts || !checked || w.busy} onClick={() => void w.control({ action: 'retry', attempt_id: attempt.id, instruction, acknowledge_facts: checked, rerun_downstream: downstream })}>创建新尝试</button>
      </div>}
  </WorkflowDialog>
}
