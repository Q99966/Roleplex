import { create } from 'zustand'
import { api, type Message, type MessageCreate, type Part, type StreamEvent } from '../api/client'
import { SessionStream, type ConnectionStatus, type SubscriptionStatus } from '../api/stream'
import { useAppStore } from './app'
import { HistoryCache, type ReadingPosition } from './history-cache'

type ChatState = {
  runtimeVersion: number
  approvalVersion: number
  conversationId: number | null
  messages: Message[]
  nextCursor: string | null
  historyEpoch: string | null
  loadingOlder: boolean
  olderError: string | null
  oversized: boolean
  position: ReadingPosition
  loadOlder: () => Promise<void>
  setPosition: (conversationId: number, position: ReadingPosition) => void
  loading: boolean
  sending: boolean
  generating: boolean
  activeGenerationIds: number[]
  connection: ConnectionStatus
  connectionError: string | null
  subscription: SubscriptionStatus
  error: string | null
  startSession: (token: string) => void
  endSession: () => void
  openConversation: (conversationId: number) => Promise<void>
  closeConversation: () => void
  retryConnection: () => void
  sendMessage: (text: string, mentions?: Array<number | 'all'>, options?: Pick<MessageCreate, 'world_task_mode' | 'world_task_id' | 'expected_task_revision' | 'expected_appointment_revision'>) => Promise<boolean>
  stopGeneration: () => Promise<void>
}

const bottomPosition: ReadingPosition = { anchor: null, offset: 0, bottom: true }

/** 读取消息正文，仅用于旧格式事件兼容。
 * @param parts 旧消息的内容。
 */
function textOf(parts: Part[]): string {
  return parts.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('')
}

/** 认证失效时清除登录上下文，停止旧连接及请求。 */
function invalidateSession() {
  useAppStore.getState().logout()
  useAppStore.setState({ error: 'AUTH_INVALID' })
  window.location.hash = '#/auth'
}

/** 每个视图拥有自己的订阅、历史缓存和请求生命周期；共用领域 reducer。 */
export function createChatStore({ preservePendingSend = false } = {}) {
let stream: SessionStream | null = null
let openSequence = 0
let historyController: AbortController | null = null
let opening: Promise<void> | null = null
let olderController: AbortController | null = null
let windowSequence = 0
const cache = new HistoryCache()
const unseenRevisions = new Map<number, number>()
let pendingSend: { signature: string; body: MessageCreate } | null = null
return create<ChatState>((set, get) => ({
  runtimeVersion: 0,
  approvalVersion: 0,
  conversationId: null, messages: [], loading: false, sending: false, generating: false,
  activeGenerationIds: [], connection: 'idle', connectionError: null, subscription: 'idle', error: null,
  nextCursor: null, historyEpoch: null, loadingOlder: false, olderError: null, oversized: false, position: bottomPosition,

  /** @param conversationId 滚动事件所属会话，旧组件不能改写新视图。
   * @param position 当前消息/工具锚点与视口相对偏移。
   */
  setPosition: (conversationId, position) => { if (get().conversationId === conversationId) set({ position }) },

  /** 为当前已验证的登录上下文建立唯一连接。
   * @param token 当前身份的访问令牌，仅传给首帧认证。
   */
  startSession: (token) => {
    get().endSession()
    const owned = new SessionStream(token, {
      onStatusChange: (connection) => { if (stream === owned) set({ connection, ...(connection === 'open' ? { connectionError: null } : {}) }) },
      onSubscriptionChange: (subscription) => {
        if (stream === owned) set({ subscription, ...(subscription === 'ready' ? { error: null } : {}) })
      },
      onSnapshot: (payload) => {
        if (stream !== owned || get().conversationId !== payload.conversation_id) return
        set({ approvalVersion: get().approvalVersion + 1 })
        ++windowSequence
        olderController?.abort()
        olderController = null
        cache.clear()
        unseenRevisions.clear()
        const ids = (payload.active_generation_ids ?? (payload.active_generation_id == null ? [] : [payload.active_generation_id])) as number[]
        const messages = payload.messages as Message[]
        const position = get().position
        const anchorExists = messages.some((message) => position.anchor === `m-${message.id}` || position.anchor?.startsWith(`m-${message.id}:`))
        set({ messages, generating: ids.length > 0, activeGenerationIds: ids,
          nextCursor: payload.next_cursor ?? null, historyEpoch: payload.stream_epoch,
          loadingOlder: false, olderError: null, oversized: Boolean(payload.oversized),
          position: anchorExists ? position : bottomPosition })
      },
      onEvent: (event) => {
        if (stream !== owned || get().conversationId !== event.conversation_id) return
        if (event.type === 'communication_updated') {
          const cid = event.conversation_id
          const position = get().position
          get().closeConversation(); cache.clear()
          void get().openConversation(cid).then(() => {
            if (stream === owned && get().conversationId === cid && get().messages.some(message =>
              position.anchor === `m-${message.id}` || position.anchor?.startsWith(`m-${message.id}:`))) get().setPosition(cid, position)
          })
          return
        }
        applyEvent(set, get, event, unseenRevisions)
      },
      onError: (error) => {
        if (stream !== owned) return
        set({ error, subscription: 'failed',
          ...(['WS_CONNECTION_FAILED', 'WS_PROTOCOL_UNSUPPORTED'].includes(error) ? { connectionError: error } : {}),
          ...(error === 'CONVERSATION_NOT_FOUND' ? { messages: [], generating: false, activeGenerationIds: [], nextCursor: null, historyEpoch: null } : {}) })
        if (error === 'CONVERSATION_NOT_FOUND') { ++windowSequence; olderController?.abort(); set({ loadingOlder: false }) }
      },
      onAuthFailure: () => {
        if (stream !== owned) return
        invalidateSession()
      },
    })
    stream = owned
    owned.connect()
  },

  /** 认证/World 上下文结束时释放连接、请求和当前消息。 */
  endSession: () => {
    if (!preservePendingSend) pendingSend = null
    cache.clear()
    unseenRevisions.clear()
    ++windowSequence
    olderController?.abort()
    olderController = null
    ++openSequence
    historyController?.abort()
    historyController = null
    opening = null
    const previous = stream
    stream = null
    previous?.close()
    set({ conversationId: null, messages: [], loading: false, sending: false, generating: false,
      activeGenerationIds: [], connection: 'idle', connectionError: null, subscription: 'idle', error: null,
      nextCursor: null, historyEpoch: null, loadingOlder: false, olderError: null, oversized: false, position: bottomPosition })
  },

  /** 显示缓存窗口或读取最近页，再校验实时水位；缓存本身不代表就绪。
   * @param conversationId 当前导航选中的会话 ID。
   */
  openConversation: (conversationId) => {
    if (get().conversationId === conversationId && !get().error) {
      if (opening) return opening
      if (get().subscription === 'ready' || get().subscription === 'syncing') return Promise.resolve()
    }
    get().closeConversation()
    const sequence = ++openSequence
    const owned = stream
    const cached = cache.take(conversationId)
    if (cached) {
      set({ conversationId, messages: cached.messages, loading: false, error: get().connectionError,
        nextCursor: cached.nextCursor, historyEpoch: cached.streamEpoch, position: cached.position,
        oversized: cached.oversized, activeGenerationIds: cached.activeGenerationIds, generating: cached.activeGenerationIds.length > 0 })
      if (get().connection !== 'failed') owned?.subscribe(conversationId, { eventSeq: cached.eventSeq, streamEpoch: cached.streamEpoch })
      return Promise.resolve()
    }
    const controller = new AbortController()
    historyController = controller
    set({ conversationId, loading: true, error: get().connectionError, messages: [] })
    let timedOut = false
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort() }, 30_000)
    const operation = (async () => {
      try {
        const history = await api.messages(conversationId, controller.signal)
        if (sequence !== openSequence || owned !== stream || get().conversationId !== conversationId) return
        const ids = history.active_generation_ids ?? (history.active_generation_id === null ? [] : [history.active_generation_id])
        set({ messages: history.items, loading: false, generating: ids.length > 0, activeGenerationIds: ids,
          nextCursor: history.next_cursor, historyEpoch: history.stream_epoch, oversized: history.oversized })
        if (get().connection === 'failed') return
        owned?.subscribe(conversationId, { eventSeq: history.event_seq, streamEpoch: history.stream_epoch })
      } catch (error) {
        if (sequence !== openSequence) return
        if (['AUTH_REQUIRED', 'AUTH_INVALID', 'AUTH_REVOKED'].includes((error as { code?: string }).code ?? '')) {
          invalidateSession()
          return
        }
        set({ loading: false, subscription: 'failed',
          error: timedOut ? 'HISTORY_LOAD_TIMEOUT' : error instanceof Error ? error.message : '加载消息失败' })
      } finally {
        window.clearTimeout(timeout)
        if (sequence === openSequence) { historyController = null; opening = null }
      }
    })()
    opening = operation
    return operation
  },

  /** 取消当前会话选择；由导航服务调用，组件卸载不释放传输连接。 */
  closeConversation: () => {
    const state = get()
    const cursor = stream?.getCursor()
    if (state.conversationId !== null && state.historyEpoch && cursor?.streamEpoch === state.historyEpoch) {
      cache.put(state.conversationId, { messages: state.messages, nextCursor: state.nextCursor,
        eventSeq: cursor.eventSeq, streamEpoch: state.historyEpoch, activeGenerationIds: state.activeGenerationIds,
        position: state.position, oversized: state.oversized })
    }
    ++windowSequence
    olderController?.abort()
    olderController = null
    unseenRevisions.clear()
    ++openSequence
    historyController?.abort()
    historyController = null
    opening = null
    stream?.unsubscribe()
    set({ conversationId: null, messages: [], loading: false, sending: false, generating: false,
      activeGenerationIds: [], subscription: get().connectionError ? 'failed' : 'idle', error: get().connectionError,
      nextCursor: null, historyEpoch: null, loadingOlder: false, olderError: null, oversized: false, position: bottomPosition })
  },

  /** 向上加载连续完整消息，不改订阅游标；失败仅影响这一页。 */
  loadOlder: async () => {
    const { conversationId, nextCursor, historyEpoch, loadingOlder, subscription } = get()
    if (conversationId === null || !nextCursor || loadingOlder || subscription !== 'ready') return
    const sequence = windowSequence
    const controller = new AbortController()
    olderController = controller
    set({ loadingOlder: true, olderError: null })
    const timeout = window.setTimeout(() => controller.abort(), 30_000)
    try {
      // 窗口外的增量不构造残缺消息；若与页读取交错，只重读该页获取完整新版。
      for (let attempt = 0; attempt < 3; attempt++) {
        const page = await api.messages(conversationId, controller.signal, nextCursor)
        if (sequence !== windowSequence) return
        if (page.stream_epoch !== historyEpoch) throw Object.assign(new Error('历史窗口已失效'), { code: 'HISTORY_CURSOR_EXPIRED' })
        if (page.items.some((item) => (unseenRevisions.get(item.id) ?? -1) > item.revision)) continue
        const merged = new Map(get().messages.map((item) => [item.id, item]))
        for (const item of page.items) {
          if (!merged.has(item.id) || merged.get(item.id)!.revision < item.revision) merged.set(item.id, item)
          unseenRevisions.delete(item.id)
        }
        set({ messages: [...merged.values()].sort((a, b) => a.id - b.id), nextCursor: page.next_cursor,
          oversized: get().oversized || page.oversized })
        return
      }
      throw new Error('历史正在更新，请重试加载。')
    } catch (error) {
      if (sequence !== windowSequence) return
      const code = (error as { code?: string }).code
      if (['AUTH_REQUIRED', 'AUTH_INVALID', 'AUTH_REVOKED'].includes(code ?? '')) { invalidateSession(); return }
      if (code === 'HISTORY_CURSOR_EXPIRED') {
        cache.clear()
        // 旧 epoch 的窗口不能继续拼接；重新请求最近页，保留物理连接。
        set({ historyEpoch: null })
        get().closeConversation()
        await get().openConversation(conversationId)
        return
      }
      if (code === 'CONVERSATION_NOT_FOUND') {
        stream?.unsubscribe()
        set({ messages: [], nextCursor: null, historyEpoch: null, error: code, subscription: 'failed', generating: false, activeGenerationIds: [] })
      } else set({ olderError: '更早消息加载失败，请重试。' })
    } finally {
      window.clearTimeout(timeout)
      if (sequence === windowSequence) { olderController = null; set({ loadingOlder: false }) }
    }
  },

  /** 用户主动重试失败连接或历史请求，不用于正常切换。 */
  retryConnection: () => {
    const id = get().conversationId
    if (get().connection === 'failed') stream?.restart()
    if (id !== null) { get().closeConversation(); void get().openConversation(id) }
  },

  /** 发送结果仅回写仍然有效的会话操作。
   * @param text 用户提交的正文。
   * @param mentions 群聊显式指定的角色或 all。
   * @returns 当前会话已获服务端接收确认才返回 true，供输入框安全清理本次草稿。
   */
  sendMessage: async (text, mentions = [], options = {}) => {
    const conversationId = get().conversationId
    if (!conversationId || !text.trim() || get().loading || get().sending || get().subscription !== 'ready') return false
    const sequence = openSequence
    set({ sending: true, error: null })
    try {
      const signature = JSON.stringify([conversationId, text, mentions, options.world_task_mode, options.world_task_id])
      if (!pendingSend || pendingSend.signature !== signature) pendingSend = { signature, body: {
        parts: [{ type: 'text', text }], mentions, client_message_id: crypto.randomUUID(), ...options,
      } }
      const result = await api.sendMessage(conversationId, pendingSend.body)
      if (sequence !== openSequence) return false
      pendingSend = null
      // 重发核对只确认原消息，不用已结束请求的旧 generation_ids 覆盖实时状态。
      if (!result.duplicate) set({ generating: result.generation_ids.length > 0, activeGenerationIds: result.generation_ids })
      upsert(set, get, result.message)
      return true
    } catch (error) {
      if (sequence === openSequence && (error as { status?: number }).status && (error as { status: number }).status < 500) pendingSend = null
      if (sequence === openSequence) set({ error: error instanceof Error ? error.message : '发送结果未确认，重试会核对同一请求' })
      return false
    } finally {
      if (sequence === openSequence) set({ sending: false })
    }
  },

  /** 停止当前会话生成，旧请求错误不能污染后续选择。 */
  stopGeneration: async () => {
    const conversationId = get().conversationId
    if (!conversationId) return
    const sequence = openSequence
    set({ generating: false, activeGenerationIds: [] })
    try { await api.stopGeneration(conversationId) }
    catch (error) { if (sequence === openSequence) set({ error: error instanceof Error ? error.message : '停止生成失败' }) }
  },
}))
}

export const useChatStore = createChatStore()

/** 插入或按 id 替换一条消息，保持按消息序号排序。 */
function upsert(set: any, get: () => ChatState, message: Message) {
  const current = get().messages.find((item) => item.id === message.id)
  if (current && current.revision >= message.revision) return
  const messages = get().messages.filter((item) => item.id !== message.id)
  messages.push(message)
  messages.sort((a, b) => a.id - b.id)
  set({ messages })
}

/** 按事件类型幂等更新窗口；未加载区域只记录版本，未知事件安全忽略。
 * @param set 当前状态的唯一写入口。
 * @param get 读取当前已选择窗口。
 * @param event 已经传输层验证归属和顺序的领域事件。
 */
export function applyEvent(set: any, get: () => ChatState, event: StreamEvent, unseenRevisions = new Map<number, number>()) {
  if (event.type === 'runtime_changed') { set({ runtimeVersion: get().runtimeVersion + 1 }); return }
  if (event.type === 'approval_changed') {
    set({ approvalVersion: get().approvalVersion + 1 })
    return
  }
  const messageId = event.payload.message?.id ?? event.payload.message_id
  const outside = typeof messageId === 'number' && get().nextCursor !== null && get().messages.length > 0
    && messageId < get().messages[0].id
  if (outside) {
    unseenRevisions.set(messageId, Math.max(unseenRevisions.get(messageId) ?? -1, event.revision))
    if (event.type === 'message_created' && event.payload.message?.sender_type === 'role' && event.generation_id !== null) {
      const ids = [...new Set([...get().activeGenerationIds, event.generation_id])]
      set({ activeGenerationIds: ids, generating: true })
    }
    if (event.type === 'message_done' && event.generation_id !== null) {
      const ids = get().activeGenerationIds.filter((id) => id !== event.generation_id)
      set({ activeGenerationIds: ids, generating: ids.length > 0 })
    }
    return
  }
  if (event.type === 'message_created') {
    const incoming = event.payload.message as Message
    const current = get().messages.find((message) => message.id === incoming.id)
    if (current && current.revision >= incoming.revision) return
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
        parts_json: appendTextDelta(message.parts_json, event.payload),
      }
    })
    set({ messages })
    return
  }

  if (event.type === 'message_done') {
    const current = get().messages.find((message) => message.id === (event.payload.message as Message).id)
    if (current && current.revision > (event.payload.message as Message).revision) return
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
    const merged = current && !incoming.timeline_version ? {
      ...incoming,
      parts_json: [
        { type: 'text', text: textOf(current.parts_json) },
        ...incoming.parts_json.filter((part) => part.type !== 'text'),
      ],
    } : incoming
    upsert(set, get, merged)
    return
  }

  if (event.type === 'context_updated') {
    window.dispatchEvent(new CustomEvent('roleplex:context-updated', { detail: event.conversation_id }))
    return
  }

  if (['workflow_updated', 'workflow_graph_updated', 'workflow_coordination_updated'].includes(event.type)) {
    window.dispatchEvent(new CustomEvent('roleplex:workflow', { detail: event.conversation_id }))
    return
  }

  if (event.type === 'member_updated' || event.type === 'conversation_updated') {
    // 其他浏览器或窗口调整成员时刷新共享会话；服务端 revision 决定最终状态。
    void useAppStore.getState().loadWorkspace()
  }
}

/** 按稳定文本段身份追加增量，保留工具及前置文本。
 * @param parts 当前有序消息内容。
 * @param payload 服务端已排序的增量负载；旧服务端可能没有分段身份。
 */
export function appendTextDelta(parts: Part[], payload: Record<string, any>): Part[] {
  const updated = parts.map((part) => ({ ...part }))
  if (typeof payload.part_id === 'string' && Number.isInteger(payload.part_index)) {
    const index = updated.findIndex((part) => part.part_id === payload.part_id && part.type === 'text')
    if (index >= 0) updated[index].text = (updated[index].text ?? '') + (payload.text ?? '')
    else if (payload.part_index === updated.length - 1 && updated.at(-1)?.type === 'execution_summary') {
      updated.splice(payload.part_index, 0, { type: 'text', part_id: payload.part_id, text: payload.text ?? '' })
    }
    else if (payload.part_index === updated.length) updated.push({ type: 'text', part_id: payload.part_id, text: payload.text ?? '' })
    return updated
  }
  // 兼容旧事件也必须保留工具卡，不能让一次增量清空其他 part。
  const index = updated.findIndex((part) => part.type === 'text')
  if (index >= 0) updated[index].text = (updated[index].text ?? '') + (payload.text ?? '')
  else updated.push({ type: 'text', text: payload.text ?? '' })
  return updated
}
