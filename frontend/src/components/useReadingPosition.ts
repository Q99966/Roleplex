import { useLayoutEffect, useRef, useState } from 'react'
import { useChatStore } from '../store/chat'

/** 用消息/工具身份固定阅读位置，布局变化不按消息数量推测滚动距离。
 * @param conversationId 当前组件所属会话，禁止迟到滚动写入其他会话。
 */
export function useReadingPosition(conversationId: number | null) {
  const feedRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const messages = useChatStore((state) => state.messages)
  const loading = useChatStore((state) => state.loading)
  const loadingOlder = useChatStore((state) => state.loadingOlder)
  const [newContent, setNewContent] = useState(false)
  const restoredTop = useRef<number | null>(null)
  const lastTail = useRef<string | null>(null)

  /** 回到底部并恢复实时跟随，由用户显式触发。 */
  function goBottom() {
    if (conversationId === null) return
    useChatStore.getState().setPosition(conversationId, { anchor: null, offset: 0, bottom: true })
    const feed = feedRef.current
    if (feed) { feed.scrollTop = feed.scrollHeight; restoredTop.current = feed.scrollTop }
    setNewContent(false)
  }

  /** 从实际可见元素捕获位置；原生滚动锚定关闭，避免双重补偿。 */
  function onScroll() {
    const feed = feedRef.current
    if (!feed || conversationId === null || useChatStore.getState().conversationId !== conversationId) return
    if (restoredTop.current !== null && Math.abs(feed.scrollTop - restoredTop.current) < 1) return
    restoredTop.current = null
    const top = feed.getBoundingClientRect().top
    const anchors = [...feed.querySelectorAll<HTMLElement>('[data-reading-anchor]')]
    const visible = anchors.filter((item) => item.getBoundingClientRect().bottom > top && item.getBoundingClientRect().top < top + feed.clientHeight)
    const anchor = visible.filter((item) => item.getBoundingClientRect().top <= top).at(-1) ?? visible[0]
    const bottom = feed.scrollHeight - feed.clientHeight - feed.scrollTop <= 32
    useChatStore.getState().setPosition(conversationId, {
      anchor: anchor?.dataset.readingAnchor ?? null, offset: anchor ? anchor.getBoundingClientRect().top - top : 0, bottom,
    })
    if (bottom) setNewContent(false)
    if (!bottom && feed.scrollTop <= 80 && !useChatStore.getState().olderError) void useChatStore.getState().loadOlder()
  }

  useLayoutEffect(() => {
    const feed = feedRef.current
    const content = contentRef.current
    if (!feed || !content || loading) return
    /** 按当前锚点修正前插、Markdown 和工具详情展开造成的布局变化。 */
    function restore() {
      if (!feed || conversationId === null || useChatStore.getState().conversationId !== conversationId) return
      const position = useChatStore.getState().position
      if (position.bottom) feed.scrollTop = feed.scrollHeight
      else {
        let anchor = [...feed.querySelectorAll<HTMLElement>('[data-reading-anchor]')]
          .find((item) => item.dataset.readingAnchor === position.anchor)
        let offset = position.offset
        // 折叠探索组后，原子项仍挂载但没有布局；零矩形不是它的真实阅读位置。
        if (anchor && !anchor.getClientRects().length) {
          anchor = anchor.parentElement?.closest<HTMLElement>('[data-reading-anchor]') ?? undefined
          offset = 0
          if (anchor) useChatStore.getState().setPosition(conversationId, {
            anchor: anchor.dataset.readingAnchor ?? null, offset, bottom: false,
          })
        }
        if (anchor) feed.scrollTop += anchor.getBoundingClientRect().top - feed.getBoundingClientRect().top - offset
      }
      restoredTop.current = feed.scrollTop
    }
    restore()
    const observer = new ResizeObserver(restore)
    observer.observe(content)
    observer.observe(feed)
    return () => observer.disconnect()
  }, [conversationId, messages, loading, loadingOlder])

  useLayoutEffect(() => {
    const tail = messages.at(-1)
    const signature = tail ? `${tail.id}:${tail.revision}` : null
    if (lastTail.current && signature !== lastTail.current && !useChatStore.getState().position.bottom) setNewContent(true)
    lastTail.current = signature
  }, [messages])

  return { feedRef, contentRef, onScroll, goBottom, newContent }
}
