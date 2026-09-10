import type { Message } from '../api/client'

export type ReadingPosition = { anchor: string | null; offset: number; bottom: boolean }
export type CachedHistory = {
  messages: Message[]; nextCursor: string | null; eventSeq: number; streamEpoch: string;
  activeGenerationIds: number[]; position: ReadingPosition; oversized: boolean
}

/** 仅存当前登录上下文的非活动会话；活跃视图不复制进缓存，无持久存储或私有详情。 */
export class HistoryCache {
  private entries = new Map<number, { value: CachedHistory; bytes: number }>()
  private bytes = 0

  /** @param maxBytes 非活动窗口序列化字节总上限。
   * @param maxConversations 非活动会话数量上限。
   */
  constructor(private maxBytes = 4 * 1024 * 1024, private maxConversations = 8) {}

  /** @param id 会话身份。
   * @param value 不包含 Token 或 Owner 详情的完整窗口。
   */
  put(id: number, value: CachedHistory) {
    this.take(id)
    const bytes = new TextEncoder().encode(JSON.stringify(value)).length
    if (bytes > this.maxBytes) return
    this.entries.set(id, { value, bytes })
    this.bytes += bytes
    while (this.bytes > this.maxBytes || this.entries.size > this.maxConversations) this.take(this.entries.keys().next().value!)
  }

  /** @param id 转为活动视图的会话；取走而非复制，保持 LRU 和总预算准确。 */
  take(id: number): CachedHistory | undefined {
    const entry = this.entries.get(id)
    if (!entry) return
    this.entries.delete(id)
    this.bytes -= entry.bytes
    return entry.value
  }

  /** 认证或 World 结束时释放所有共享历史。 */
  clear() { this.entries.clear(); this.bytes = 0 }
}
