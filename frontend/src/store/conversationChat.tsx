import { createContext, useContext, type ReactNode } from 'react'
import { useChatStore, type createChatStore } from './chat'
import { useContextDraft } from './contextDraft'
import { getAuthEpoch } from '../api/client'

type ChatStore = ReturnType<typeof createChatStore>
const ConversationChat = createContext<{ store: ChatStore; draft: string | null }>({ store: useChatStore, draft: null })

/** 复用上下文/用量呈现时显式选定当前视图的会话状态，默认保持主聊天。 */
export function ConversationChatProvider({ store, draft = null, children }: { store: ChatStore; draft?: string | null; children: ReactNode }) {
  return <ConversationChat.Provider value={{ store, draft }}>{children}</ConversationChat.Provider>
}

export function useConversationChat<T>(selector: (state: ReturnType<ChatStore['getState']>) => T) {
  return useContext(ConversationChat).store(selector)
}

export function useConversationDraft(conversationId: number) {
  const provided = useContext(ConversationChat).draft
  const original = useContextDraft(state => state.scope === `${getAuthEpoch()}:${conversationId}` ? state.text : '')
  return provided ?? original
}
