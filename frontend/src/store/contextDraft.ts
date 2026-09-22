import { create } from 'zustand'
import { getAuthEpoch } from '../api/client'

/** 只在当前页面共享输入框草稿以便预估；不持久化，跨账号/World 无法复用。 */
export const useContextDraft = create<{ scope: string; text: string; set: (conversationId: number, text: string) => void }>(set => ({
  scope: '', text: '', set: (conversationId, text) => set({ scope: `${getAuthEpoch()}:${conversationId}`, text }),
}))
