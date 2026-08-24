import { create } from 'zustand'
import { api, type Message, type MessageHistory, type Part, type StreamEvent } from '../api/client'
import { ConversationStream } from '../api/stream'

type ChatState = {
  conversationId: number | null
  messages: Message[]
  loading: boolean
  sending: boolean
  generating: boolean
  connection: 'connecting' | 'open' | 'closed'
  error: string | null
  openConversation: (conversationId: number) => Promise<void>
  closeConversation: () => void
  sendMessage: (text: string) => Promise<void>
  stopGeneration: () => Promise<void>
}

let stream: ConversationStream | null = null
// React StrictMode 会执行一次 mount → cleanup → remount。第一次异步历史请求尚未返回时，
// cleanup 没有 WebSocket 可关闭；复用请求并用序号淘汰过期调用，避免双 GET 和泄漏连接。
let openSequence = 0
const historyLoads = new Map<number, Promise<MessageHistory>>()

/** 复用同一会话正在进行的历史请求，避免 StrictMode 重挂载产生重复 GET。 */
function loadHistory(conversationId: number): Promise<MessageHistory> {
  const pending = historyLoads.get(conversationId)
  if (pending) return pending
  const request = api.messages(conversationId).finally(() => {
    if (historyLoads.get(conversationId) === request) historyLoads.delete(conversationId)
  })
  historyLoads.set(conversationId, request)
  return request
}

/** 读取消息 part 中的纯文本内容，忽略未知 part 类型。 */
function textOf(parts: Part[]): string {
  return parts.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('')
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversationId: null,
  messages: [],
  loading: false,
  sending: false,
  generating: false,
  connection: 'closed',
  error: null,

  // 先用 REST 快照建立历史和事件游标，再用同一游标订阅实时事件。
  openConversation: async (conversationId) => {
    get().closeConversation()
    const sequence = ++openSequence
    set({ conversationId, loading: true, error: null, messages: [] })
    try {
      const history = await loadHistory(conversationId)
      // cleanup、切换会话或 StrictMode 的第二次调用都会推进序号；过期调用不得再建连或改状态。
      if (sequence !== openSequence || get().conversationId !== conversationId) return
      set({
        messages: history.items,
        loading: false,
        generating: history.active_generation_id !== null,
      })
      const nextStream = new ConversationStream(conversationId, {
        onStatusChange: (connection) => set({ connection }),
        onSnapshot: (payload) => {
          set({
            messages: (payload.messages ?? []) as Message[],
            generating: payload.active_generation_id !== null && payload.active_generation_id !== undefined,
          })
        },
        onEvent: (event) => applyEvent(set, get, event),
      })
      stream = nextStream
      nextStream.connect({ eventSeq: history.event_seq, streamEpoch: history.stream_epoch })
    } catch (error) {
      if (sequence !== openSequence) return
      set({ loading: false, error: error instanceof Error ? error.message : '加载消息失败' })
    }
  },

  closeConversation: () => {
    openSequence += 1
    stream?.close()
    stream = null
    set({ conversationId: null, messages: [], generating: false, connection: 'closed' })
  },

  sendMessage: async (text) => {
    const conversationId = get().conversationId
    if (!conversationId || !text.trim() || get().sending) return
    set({ sending: true, error: null })
    try {
      // client_message_id 让网络重试不会产生重复用户消息。
      const result = await api.sendMessage(conversationId, {
        parts: [{ type: 'text', text: text.trim() }],
        client_message_id: crypto.randomUUID(),
      })
      set({ generating: result.generation_id !== null })
      upsert(set, get, result.message)
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '发送失败' })
    } finally {
      set({ sending: false })
    }
  },

  stopGeneration: async () => {
    const conversationId = get().conversationId
    if (!conversationId) return
    // 先让界面立即退出生成状态，最终状态仍以服务端事件为准。
    set({ generating: false })
    try {
      await api.stopGeneration(conversationId)
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '停止生成失败' })
    }
  },
}))

/** 插入或按 id 替换一条消息，保持按消息序号排序。 */
function upsert(set: any, get: () => ChatState, message: Message) {
  const messages = get().messages.filter((item) => item.id !== message.id)
  messages.push(message)
  messages.sort((a, b) => a.id - b.id)
  set({ messages })
}

/** 按事件类型幂等更新本地消息状态；未知事件安全忽略。 */
function applyEvent(set: any, get: () => ChatState, event: StreamEvent) {
  if (event.type === 'message_created') {
    upsert(set, get, event.payload.message as Message)
    if ((event.payload.message as Message)?.sender_type === 'role') set({ generating: true })
    return
  }

  if (event.type === 'message_delta') {
    const messages = get().messages.map((message) => {
      if (message.id !== event.payload.message_id) return message
      // 增量事件只追加文本，完整内容由 message_done 事件校正。
      if (event.revision <= message.revision) return message
      return {
        ...message,
        revision: event.revision,
        parts_json: [{ type: 'text', text: textOf(message.parts_json) + (event.payload.text ?? '') }],
      }
    })
    set({ messages })
    return
  }

  if (event.type === 'message_done') {
    upsert(set, get, event.payload.message as Message)
    set({ generating: false })
    if (event.payload.error_code) set({ error: String(event.payload.error_code) })
  }
}
