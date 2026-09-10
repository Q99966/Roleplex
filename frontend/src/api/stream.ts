import { getApiUrl, type StreamEvent } from './client'

export type ConnectionStatus = 'idle' | 'connecting' | 'authenticating' | 'open' | 'reconnecting' | 'failed'
export type SubscriptionStatus = 'idle' | 'syncing' | 'ready' | 'failed'

type StreamHandlers = {
  onEvent: (event: StreamEvent) => void
  onSnapshot: (payload: Record<string, any>) => void
  onStatusChange: (status: ConnectionStatus) => void
  onSubscriptionChange: (status: SubscriptionStatus) => void
  onError: (code: string) => void
  onAuthFailure: () => void
}
type Selection = { conversationId: number; id: string; eventSeq: number; epoch: string | null }

/** 使用与 HTTP 相同的后端来源，不把凭据放进 URL。 */
function toWebSocketUrl(): string {
  const apiUrl = getApiUrl()
  if (apiUrl) return apiUrl.replace(/^http/, 'ws') + '/api/ws'
  return (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/api/ws'
}

/** 登录会话持有一个传输连接；选择会话仅更换订阅，不由页面卸载关闭。 */
export class SessionStream {
  private socket: WebSocket | null = null
  private selection: Selection | null = null
  private pendingUnsubscribe: string | null = null
  private stopped = false
  private authenticated = false
  private attempts = 0
  private recoveries = 0
  private reconnectTimer: number | null = null
  private handshakeTimer: number | null = null
  private syncTimer: number | null = null
  private heartbeatTimer: number | null = null
  private pongTimer: number | null = null

  /** @param token 当前登录会话凭据，仅驻留内存并用于首帧。
   * @param handlers 当前聊天状态的唯一同步入口。
   */
  constructor(private token: string, private handlers: StreamHandlers) {}

  /** 建立或恢复物理连接；重复调用不会额外握手。 */
  connect() {
    if (this.stopped || this.socket) return
    this.handlers.onStatusChange(this.attempts ? 'reconnecting' : 'connecting')
    let socket: WebSocket
    try { socket = new WebSocket(toWebSocketUrl()) }
    catch {
      this.handlers.onStatusChange('failed')
      this.handlers.onError('WS_CONNECTION_FAILED')
      return
    }
    this.socket = socket
    this.handshakeTimer = window.setTimeout(() => { if (this.current(socket)) socket.close() }, 15_000)
    socket.onopen = () => {
      if (!this.current(socket)) return
      this.handlers.onStatusChange('authenticating')
      socket.send(JSON.stringify({ type: 'auth', token: this.token }))
    }
    socket.onmessage = (event) => {
      if (!this.current(socket)) return
      let frame: any
      try { frame = JSON.parse(event.data) } catch { return }
      this.receive(frame)
    }
    socket.onclose = (event) => {
      if (!this.current(socket)) return
      this.socket = null
      this.authenticated = false
      this.clearTimers()
      if (event.code === 1008) { this.close(); this.handlers.onAuthFailure(); return }
      if (this.selection) this.handlers.onSubscriptionChange('syncing')
      if (++this.attempts > 6) {
        this.handlers.onStatusChange('failed')
        this.handlers.onError('WS_CONNECTION_FAILED')
        return
      }
      this.handlers.onStatusChange('reconnecting')
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null
        if (this.selection) this.selection.id = crypto.randomUUID()
        this.connect()
      }, Math.min(1000 * 2 ** (this.attempts - 1), 10_000))
    }
    socket.onerror = () => { if (this.current(socket)) socket.close() }
  }

  /** @param socket 回调所属连接，用对象身份丢弃旧连接的迟到事件。 */
  private current(socket: WebSocket) { return !this.stopped && socket === this.socket }

  /** @param conversationId 选择的会话。
   * @param cursor 已通过 REST 应用的事件游标。
   */
  subscribe(conversationId: number, cursor: { eventSeq: number; streamEpoch: string | null }) {
    this.selection = { conversationId, id: crypto.randomUUID(), eventSeq: cursor.eventSeq, epoch: cursor.streamEpoch }
    this.pendingUnsubscribe = null
    this.recoveries = 0
    this.handlers.onSubscriptionChange('syncing')
    this.sendSelection()
  }

  /** 取消当前订阅意图及同步等待，保留登录连接。 */
  unsubscribe() {
    const previous = this.selection
    this.selection = null
    if (this.syncTimer !== null) window.clearTimeout(this.syncTimer)
    this.syncTimer = null
    if (previous && this.authenticated && this.socket?.readyState === WebSocket.OPEN) {
      this.pendingUnsubscribe = previous.id
      this.socket.send(JSON.stringify({ type: 'unsubscribe', subscription_id: previous.id }))
    } else {
      this.pendingUnsubscribe = null
      this.handlers.onSubscriptionChange('idle')
    }
  }

  /** 只有已认证的当前连接才能发送最新订阅。 */
  private sendSelection() {
    const selected = this.selection
    if (!selected || !this.authenticated || this.socket?.readyState !== WebSocket.OPEN) return
    if (this.syncTimer !== null) window.clearTimeout(this.syncTimer)
    this.syncTimer = window.setTimeout(() => {
      if (this.selection?.id === selected.id) this.failSubscription('WS_SYNC_TIMEOUT')
    }, 30_000)
    this.socket.send(JSON.stringify({
      type: 'subscribe', conversation_id: selected.conversationId, subscription_id: selected.id,
      after_event_seq: selected.eventSeq, stream_epoch: selected.epoch,
      history_window: 'recent',
    }))
  }

  /** @param frame 当前物理连接收到的控制帧或业务事件。 */
  private receive(frame: any) {
    if (!frame || typeof frame !== 'object' || Array.isArray(frame)) return
    if (frame?.type === 'auth_ok') {
      if (this.authenticated) return
      if (!Array.isArray(frame.capabilities) || !['subscription_control_v1', 'history_window_v1'].every((cap) => frame.capabilities.includes(cap))) {
        this.close()
        this.handlers.onStatusChange('failed')
        this.handlers.onError('WS_PROTOCOL_UNSUPPORTED')
        return
      }
      this.authenticated = true
      this.attempts = 0
      if (this.handshakeTimer !== null) window.clearTimeout(this.handshakeTimer)
      this.handshakeTimer = null
      this.handlers.onStatusChange('open')
      this.sendSelection()
      this.heartbeatTimer = window.setInterval(() => {
        if (this.socket?.readyState !== WebSocket.OPEN || this.pongTimer !== null) return
        this.socket.send(JSON.stringify({ type: 'ping' }))
        this.pongTimer = window.setTimeout(() => this.socket?.close(), 30_000)
      }, 15_000)
      return
    }
    if (frame?.type === 'pong') {
      if (this.pongTimer !== null) window.clearTimeout(this.pongTimer)
      this.pongTimer = null
      return
    }
    if (frame?.type === 'error' && ['AUTH_INVALID', 'AUTH_REVOKED', 'AUTH_REQUIRED'].includes(frame.payload?.code)) {
      this.close()
      this.handlers.onAuthFailure()
      return
    }
    if (frame?.type === 'unsubscribed') {
      if (!this.selection && frame.subscription_id === this.pendingUnsubscribe) {
        this.pendingUnsubscribe = null
        this.handlers.onSubscriptionChange('idle')
      }
      return
    }
    const selected = this.selection
    if (!selected || frame.subscription_id !== selected.id || frame.conversation_id !== selected.conversationId) return
    if (frame.type === 'error') { this.failSubscription(frame.payload?.code ?? 'WS_SYNC_FAILED'); return }
    if (frame.type === 'snapshot') {
      if (!Number.isInteger(frame.payload?.event_seq) || frame.payload.event_seq < 0 || !Array.isArray(frame.payload.messages)) {
        this.failSubscription('WS_SYNC_FAILED')
        return
      }
      this.handlers.onSnapshot({ ...frame.payload, stream_epoch: frame.stream_epoch })
      selected.eventSeq = frame.payload.event_seq
      selected.epoch = frame.stream_epoch
      return
    }
    if (frame.type === 'subscribed') {
      selected.epoch = frame.stream_epoch
      return
    }
    if (frame.type === 'sync_complete') {
      if (frame.stream_epoch !== selected.epoch || !Number.isInteger(frame.through_event_seq)
        || frame.through_event_seq < 0 || frame.through_event_seq > selected.eventSeq) {
        this.recoverGap()
        return
      }
      if (this.syncTimer !== null) window.clearTimeout(this.syncTimer)
      this.syncTimer = null
      this.recoveries = 0
      this.handlers.onSubscriptionChange('ready')
      return
    }
    if (!Number.isInteger(frame.event_seq) || frame.event_seq <= selected.eventSeq) return
    if (frame.stream_epoch !== selected.epoch || frame.event_seq !== selected.eventSeq + 1) {
      this.recoverGap()
      return
    }
    this.handlers.onEvent(frame as StreamEvent)
    selected.eventSeq = frame.event_seq
  }

  /** 缺口通过同一连接重新订阅恢复，不跳过事件或伪造就绪。 */
  private recoverGap() {
    if (!this.selection) return
    if (++this.recoveries > 3) { this.failSubscription('WS_SYNC_FAILED'); return }
    this.selection.id = crypto.randomUUID()
    this.handlers.onSubscriptionChange('syncing')
    this.sendSelection()
  }

  /** @param code 当前订阅的稳定失败原因。 */
  private failSubscription(code: string) {
    this.unsubscribe()
    this.pendingUnsubscribe = null
    this.handlers.onSubscriptionChange('failed')
    this.handlers.onError(code)
  }

  /** 清理当前物理连接的所有等待，不能留下旧计时器干扰新连接。 */
  private clearTimers() {
    for (const timer of [this.handshakeTimer, this.syncTimer, this.pongTimer]) if (timer !== null) window.clearTimeout(timer)
    if (this.heartbeatTimer !== null) window.clearInterval(this.heartbeatTimer)
    this.handshakeTimer = this.syncTimer = this.pongTimer = this.heartbeatTimer = null
  }

  /** 只提供当前订阅已应用的游标，缓存历史不能自行推进它。 */
  getCursor() {
    return this.selection ? { eventSeq: this.selection.eventSeq, streamEpoch: this.selection.epoch } : null
  }

  /** 用户显式重试失败连接，不用于正常会话切换。 */
  restart() {
    this.close()
    this.stopped = false
    this.attempts = 0
    this.connect()
  }

  /** 登录上下文结束时释放连接与所有意图；组件卸载不调用它。 */
  close() {
    this.stopped = true
    this.authenticated = false
    this.selection = null
    this.pendingUnsubscribe = null
    this.clearTimers()
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    const previous = this.socket
    this.socket = null
    previous?.close()
  }
}
