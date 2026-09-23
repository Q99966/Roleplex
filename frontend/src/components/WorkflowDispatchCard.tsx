import { useEffect, useState } from 'react'
import { getAuthEpoch, request, type CommunicationActor, type Message } from '../api/client'
import { useConversationChat } from '../store/conversationChat'

type Item = { node_id: string; title: string; role: CommunicationActor; status: string; attempt_id: string | null; execution_id: string | null; error_code: string | null }
type Card = { id: string; message_id: number; run_id: string; revision: number; status: string; items: Item[] }
const labels: Record<string, string> = { pending: '等待中', queued: '排队中', running: '执行中', active: '执行中', completed: '已完成', failed: '失败', blocked: '受阻', stopped: '已停止', interrupted: '已中断', skipped: '已跳过', superseded: '已被调整替代', waiting_feedback: '等待反馈处理' }
const terminal = new Set(['completed','failed','blocked','stopped','interrupted','skipped','superseded'])
const button = 'rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs disabled:opacity-40'

/** 一份公开广播投影多个准确尝试；加载详情与 @ 展示都没有派发副作用。 */
export function WorkflowDispatchCard({ message, isOwner }: { message: Message; isOwner: boolean }) {
  const [data, setData] = useState<Card | null>(null), [error, setError] = useState(''), [refresh, setRefresh] = useState(0)
  const [input, setInput] = useState<{ id: string; text: string | null } | null>(null), [inputError, setInputError] = useState('')
  const boundary = useConversationChat(state => `${state.runtimeVersion}:${state.messages.at(-1)?.id}:${state.messages.at(-1)?.status}`)
  const epoch = getAuthEpoch()
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function load() {
      try {
        const value = await request<Card>(`/api/conversations/${message.conversation_id}/messages/${message.id}/dispatch`, { signal: controller.signal, cache: 'no-store' })
        if (controller.signal.aborted || epoch !== getAuthEpoch()) return
        setData(value); setError('')
        if (value.items.some(item => !terminal.has(item.status))) timer = setTimeout(() => void load(), 1400)
      } catch { if (!controller.signal.aborted && epoch === getAuthEpoch()) setError('进度暂不可读取，原任务仍可在流程中查看。') }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [message.id, message.conversation_id, epoch, refresh, boundary])
  async function readInput(item: Item) {
    if (!data || !item.attempt_id) return
    setInputError('')
    try {
      const value = await request<{ text: string | null }>(`/api/conversations/${message.conversation_id}/workflows/runs/${data.run_id}/attempts/${item.attempt_id}/input`, { cache: 'no-store' })
      if (epoch === getAuthEpoch()) setInput({ id: item.attempt_id, text: value.text })
    } catch { if (epoch === getAuthEpoch()) setInputError('该执行输入目前不可读取，请核对权限与来源。') }
  }
  const runId = data?.run_id ?? message.communication?.source.run_id
  return <section aria-label="工作流任务派发" className="min-w-[min(320px,65vw)] space-y-3">
    <p className="font-medium">{message.parts_json.filter(part => part.type === 'text').map(part => part.text).join('')}</p>
    {!data && !error && <p className="text-xs text-slate-500">正在读取任务进度…</p>}
    {data?.items.map(item => <div key={item.node_id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-700/50 bg-panel px-3 py-2 text-xs">
      <div><p className="font-medium">{item.role.name}</p><p className="mt-1 text-slate-500">{item.title}</p></div><span>{labels[item.status] ?? item.status}</span>
    </div>)}
    {error && <p role="alert" className="text-xs text-amber-700">{error}</p>}
    <div className="flex flex-wrap gap-2">{isOwner && typeof runId === 'string' && <a className={button} href={`#/workspace/conversation/${message.conversation_id}?workflow_run=${encodeURIComponent(runId)}`}>打开对应流程</a>}
      <button className={button} onClick={() => setRefresh(value => value + 1)}>刷新进度</button></div>
    {isOwner && <details className="text-xs"><summary className="cursor-pointer text-slate-500">分工与完整执行输入</summary><div className="mt-2 flex flex-wrap gap-2">
      {data?.items.map(item => <button key={item.node_id} className={button} disabled={!item.attempt_id} onClick={() => void readInput(item)}>查看 {item.role.name} 的输入</button>)}
    </div>{inputError && <p role="alert" className="mt-2 text-amber-700">{inputError}</p>}{input && <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-canvas p-3">{input.text ?? '该节点尚无模型执行输入。'}</pre>}</details>}
  </section>
}
