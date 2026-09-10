import { getApiUrl, getAuthEpoch, getToken, onTokenChange } from '../api/client'
import { useAppStore } from './app'
import { useChatStore } from './chat'

/** 根据认证/World 和导航状态管理连接，不依赖 React 组件的挂载次数。 */
export function startChatSession(): () => void {
  let scope: string | null = null
  let selected: number | null = null

  /** 从唯一登录状态派生连接归属，不把 Token 原文写入状态键。 */
  function reconcile() {
    const app = useAppStore.getState()
    const token = getToken()
    const candidate = app.user && token && !app.passwordResetRequired && !app.switchingWorld
      ? JSON.stringify([getApiUrl(), app.worldName, app.user.id, getAuthEpoch()]) : null
    // 初次登录等待身份加载完成；已有相同身份的连接不因应用加载视图卸载页面而关闭。
    const next = candidate === scope || !app.loading ? candidate : null
    if (scope !== next) {
      scope = next
      selected = null
      useChatStore.getState().endSession()
      if (next && token) useChatStore.getState().startSession(token)
    }
    if (!next) return
    if (selected !== app.activeConversationId) {
      selected = app.activeConversationId
      if (selected === null) useChatStore.getState().closeConversation()
      else void useChatStore.getState().openConversation(selected)
    }
  }

  /** 页面离开或进入后退缓存时收口当前传输，恢复页面后重新按认证状态建立。 */
  function suspend() {
    scope = null
    selected = null
    useChatStore.getState().endSession()
  }

  const unsubscribeApp = useAppStore.subscribe(reconcile)
  const unsubscribeToken = onTokenChange(reconcile)
  window.addEventListener('pagehide', suspend)
  window.addEventListener('pageshow', reconcile)
  reconcile()
  return () => {
    unsubscribeApp()
    unsubscribeToken()
    window.removeEventListener('pagehide', suspend)
    window.removeEventListener('pageshow', reconcile)
    suspend()
  }
}
