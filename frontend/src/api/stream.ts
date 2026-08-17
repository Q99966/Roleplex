import { getApiUrl, getToken, type StreamEvent } from './client'

type StreamHandlers = {
  onEvent: (event: StreamEvent) => void
  onSnapshot: (payload: Record<string, any>) => void
  onStatusChange?: (status: 'connecting' | 'open' | 'closed') => void
}

/** 把 HTTP 后端地址转换为同源的 WebSocket 地址。 */
function toWebSocketUrl(): string {
  const base = getApiUrl().replace(/^http/, 'ws')
  return `${base}/api/ws`
}

/**
 * 单会话事件流客户端：首帧认证、按游标恢复、断线重连。
 *
 * Token 只出现在首帧消息体中，不放入查询参数；事件按 event_seq 幂等应用，
 * epoch 变化或 backlog 截断时由服务端下发完整快照重建状态。
 */
export class ConversationStream {
  private socket: WebSocket | null = null
  private reconnectTimer: number | null = null
  private closedByUser = false
  private lastEventSeq = 0
  private streamEpoch: string | null = null

  constructor(private conversationId: number, private handlers: StreamHandlers) {}

  /** 使用已知的事件游标建立连接；重连时会复用最新游标。 */
  connect(cursor?: { eventSeq: number; streamEpoch: string | null }) {
    if (cursor) {
      this.lastEventSeq = cursor.eventSeq
      this.streamEpoch = cursor.streamEpoch
    }
    this.closedByUser = false
    this.handlers.onStatusChange?.('connecting')

    const socket = new WebSocket(toWebSocketUrl())
    this.socket = socket

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'auth', token: getToken() }))
    }

    socket.onmessage = (raw) => {
      let frame: any
      try {
        frame = JSON.parse(raw.data as string)
      } catch {
        return
      }

      if (frame.type === 'auth_ok') {
        this.handlers.onStatusChange?.('open')
        socket.send(JSON.stringify({
          type: 'subscribe',
          conversation_id: this.conversationId,
          after_event_seq: this.lastEventSeq,
          stream_epoch: this.streamEpoch,
        }))
        return
      }

      if (frame.type === 'snapshot') {
        this.streamEpoch = frame.stream_epoch
        this.lastEventSeq = frame.payload?.event_seq ?? 0
        this.handlers.onSnapshot(frame.payload ?? {})
        return
      }

      if (frame.type === 'subscribed') {
        this.streamEpoch = frame.stream_epoch
        return
      }

      if (typeof frame.event_seq !== 'number') return
      // 只应用比本地游标更新的事件，重复投递直接丢弃。
      if (frame.event_seq <= this.lastEventSeq) return
      this.lastEventSeq = frame.event_seq
      this.streamEpoch = frame.stream_epoch ?? this.streamEpoch
      this.handlers.onEvent(frame as StreamEvent)
    }

    socket.onclose = () => {
      this.handlers.onStatusChange?.('closed')
      if (this.closedByUser) return
      // 断线后按当前游标重连，由服务端决定回放还是快照。
      this.reconnectTimer = window.setTimeout(() => this.connect(), 1200)
    }

    socket.onerror = () => socket.close()
  }

  /** 主动关闭连接并停止重连。 */
  close() {
    this.closedByUser = true
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
  }
}
