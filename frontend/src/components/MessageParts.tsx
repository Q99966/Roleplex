import { lazy, Suspense } from 'react'
import type { Message } from '../api/client'
import { ToolCallCard } from './ToolCallCard'

const MarkdownRenderer = lazy(async () => ({ default: (await import('./MarkdownRenderer')).MarkdownRenderer }))

/** 按服务端 parts 顺序渲染，旧消息明确标注位置未记录。
 * @param message 带 revision 的完整消息。
 * @param isOwner 是否允许主动加载私有详情。
 */
export function MessageParts({ message, isOwner }: { message: Message; isOwner: boolean }) {
  if (message.sender_type === 'user') return <>{message.parts_json.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('')}</>
  const legacy = !message.timeline_version && message.parts_json.some((part) => part.type === 'tool_call')
  return <>
    {message.parts_json.map((part, index) => {
      if (part.type === 'text') return part.text ? <div key={String(part.part_id ?? `text-${index}`)} data-testid="message-text-part">
        <Suspense fallback={<span className="whitespace-pre-wrap">{part.text}</span>}>
          <MarkdownRenderer content={part.text} isGenerating={message.status === 'generating' && index === message.parts_json.length - 1} />
        </Suspense>
      </div> : null
      if (part.type === 'tool_call') return <div key={part.call_id}>
        {legacy && index === message.parts_json.findIndex((item) => item.type === 'tool_call') && <p className="mt-3 text-xs text-slate-500">历史执行记录，位置未记录</p>}
        <ToolCallCard part={part} conversationId={message.conversation_id} messageId={message.id} isOwner={isOwner} />
      </div>
      return <p key={index} className="mt-2 text-xs text-slate-500">[当前版本暂不支持渲染的内容：{part.type}]</p>
    })}
  </>
}
