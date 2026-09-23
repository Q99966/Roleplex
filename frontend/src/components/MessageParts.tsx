import { lazy, Suspense, useState } from 'react'
import { getAuthEpoch, request, type Message } from '../api/client'
import { ToolCallCard } from './ToolCallCard'
import { ExplorationGroup } from './ExplorationGroup'
import { groupMessageParts } from './exploration'
import { WorkflowDispatchCard } from './WorkflowDispatchCard'

const MarkdownRenderer = lazy(async () => ({ default: (await import('./MarkdownRenderer')).MarkdownRenderer }))

/** 按服务端 parts 顺序渲染，旧消息明确标注位置未记录。
 * @param message 带 revision 的完整消息。
 * @param isOwner 是否允许主动加载私有详情。
 */
export function MessageParts({ message, isOwner }: { message: Message; isOwner: boolean }) {
  if (message.communication?.kind === 'workflow_dispatch') return <WorkflowDispatchCard message={message} isOwner={isOwner} />
  if (message.communication?.kind === 'legacy_execution_input') return <LegacyInput message={message} isOwner={isOwner} />
  if (message.sender_type === 'user') return <>{message.parts_json.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('')}</>
  const legacy = !message.timeline_version && message.parts_json.some((part) => part.type === 'tool_call')
  return <>
    {groupMessageParts(message).map((group) => {
      const { index, key } = group
      const part = group.parts[0]
      // 后台执行事实用于恢复与上下文，不作为聊天内容或未知类型占位展示。
      if (part.type === 'execution_summary') return null
      if (group.kind === 'exploration') return <ExplorationGroup key={key} parts={group.parts}
        conversationId={message.conversation_id} messageId={message.id} isOwner={isOwner} />
      if (part.type === 'text') return part.text ? <div key={String(part.part_id ?? `text-${index}`)} data-testid="message-text-part">
        <Suspense fallback={<span className="whitespace-pre-wrap">{part.text}</span>}>
          <MarkdownRenderer content={part.text} isGenerating={message.status === 'generating' && index === message.parts_json.length - 1} />
        </Suspense>
      </div> : null
      if (part.type === 'tool_call') return <div key={key} data-reading-anchor={`m-${message.id}:tool-${part.call_id}`}>
        {legacy && index === message.parts_json.findIndex((item) => item.type === 'tool_call') && <p className="mt-3 text-xs text-slate-500">历史执行记录，位置未记录</p>}
        <ToolCallCard part={part} conversationId={message.conversation_id} messageId={message.id} isOwner={isOwner} />
      </div>
      return <p key={index} className="mt-2 text-xs text-slate-500">[当前版本暂不支持渲染的内容：{part.type}]</p>
    })}
  </>
}

function LegacyInput({ message, isOwner }: { message: Message; isOwner: boolean }) {
  const [text, setText] = useState<string | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function read() {
    const epoch = getAuthEpoch(); setBusy(true); setError('')
    try { const value = await request<{ text: string }>(`/api/conversations/${message.conversation_id}/messages/${message.id}/input`, { cache: 'no-store' }); if (epoch === getAuthEpoch()) setText(value.text) }
    catch { if (epoch === getAuthEpoch()) setError('历史输入暂不可读取。') }
    finally { if (epoch === getAuthEpoch()) setBusy(false) }
  }
  return <div className="space-y-2 text-xs text-slate-500"><p>历史工作流执行输入 · 原文保留，来源已单独标识。</p>
    {isOwner && <details onToggle={event => { if (event.currentTarget.open && text === null && !busy) void read() }}><summary className="cursor-pointer">查看完整历史输入</summary>
      {busy && <p>正在读取…</p>}{error && <p role="alert">{error}</p>}{text !== null && <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words">{text}</pre>}</details>}</div>
}
