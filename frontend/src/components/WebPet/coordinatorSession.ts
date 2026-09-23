import { createChatStore } from '../../store/chat'
import type { Viewport } from '@xyflow/react'

export type Detail = 'context' | 'usage' | 'task' | 'feedback' | 'memory' | null
export type SendMode = 'chat' | 'execute' | 'continue'
type Session = { scope: string; store: ReturnType<typeof createChatStore>; draft: string; detail: Detail;
  view: 'chat' | 'flow'; taskId: string | null; selected: string | null; mode: SendMode;
  scroll: number | null; viewports: Record<string, Viewport> }
let current: Session | null = null

/** 仅保留当前身份/World 的界面位置和未确认请求；不落浏览器持久存储。 */
export function clearCoordinatorSession() { current?.store.getState().endSession(); current = null }
export function coordinatorSession(scope: string): Session {
  if (current?.scope !== scope) {
    clearCoordinatorSession()
    current = { scope, store: createChatStore({ preservePendingSend: true }), draft: '', detail: null, view: 'chat',
      taskId: null, selected: null, mode: 'chat', scroll: null, viewports: {} }
  }
  return current!
}
