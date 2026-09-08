import { create } from 'zustand'
import { api, type Message, type MessageHistory, type Part, type StreamEvent } from '../api/client'
import { ConversationStream } from '../api/stream'
import { useAppStore } from './app'

type ChatState = {
  conversationId: number | null
  messages: Message[]
  loading: boolean
  sending: boolean
  generating: boolean
  activeGenerationIds: number[]
  connection: 'connecting' | 'open' | 'closed'
  error: string | null
  openConversation: (conversationId: number) => Promise<void>
  closeConversation: () => void
  sendMessage: (text: string, mentions?: Array<number | 'all'>) => Promise<void>
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
  activeGenerationIds: [],
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
        activeGenerationIds: history.active_generation_ids ?? (
          history.active_generation_id === null ? [] : [history.active_generation_id]
        ),
      })
      const nextStream = new ConversationStream(conversationId, {
        onStatusChange: (connection) => set({ connection }),
        onSnapshot: (payload) => {
          set({
            messages: (payload.messages ?? []) as Message[],
            generating: payload.active_generation_id !== null && payload.active_generation_id !== undefined,
            activeGenerationIds: (payload.active_generation_ids ?? (
              payload.active_generation_id == null ? [] : [payload.active_generation_id]
            )) as number[],
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
    set({ conversationId: null, messages: [], generating: false, activeGenerationIds: [], connection: 'closed' })
  },

  sendMessage: async (text, mentions = []) => {
    const conversationId = get().conversationId
    if (!conversationId || !text.trim() || get().sending) return
    set({ sending: true, error: null })
    try {
      // client_message_id 让网络重试不会产生重复用户消息。
      const result = await api.sendMessage(conversationId, {
        parts: [{ type: 'text', text: text.trim() }],
        mentions,
        client_message_id: crypto.randomUUID(),
      })
      set({
        generating: result.generation_ids.length > 0,
        activeGenerationIds: result.generation_ids,
      })
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
    set({ generating: false, activeGenerationIds: [] })
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
    if ((event.payload.message as Message)?.sender_type === 'role') {
      const generationIds = event.generation_id === null
        ? get().activeGenerationIds
        : Array.from(new Set([...get().activeGenerationIds, event.generation_id]))
      set({ generating: generationIds.length > 0, activeGenerationIds: generationIds })
    }
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
    const generationIds = event.generation_id === null
      ? get().activeGenerationIds
      : get().activeGenerationIds.filter((id) => id !== event.generation_id)
    set({ generating: generationIds.length > 0, activeGenerationIds: generationIds })
    if (event.payload.error_code) set({ error: String(event.payload.error_code) })
    return
  }

  if (event.type === 'message_part_update') {
    const incoming = event.payload.message as Message
    const current = get().messages.find((message) => message.id === incoming.id)
    // 工具事件从数据库带回的文本可能落后于内存流；保留客户端已经应用的文本增量，
    // 只用服务端完整消息校正非文本 part，最终 message_done 仍会统一收口。
    const merged = current ? {
      ...incoming,
      parts_json: [
        { type: 'text', text: textOf(current.parts_json) },
        ...incoming.parts_json.filter((part) => part.type !== 'text'),
      ],
    } : incoming
    upsert(set, get, merged)
    return
  }

  if (event.type === 'member_updated' || event.type === 'conversation_updated') {
    // 其他浏览器或窗口调整成员时刷新共享会话；服务端 revision 决定最终状态。
    void useAppStore.getState().loadWorkspace()
  }
}
