/**
 * 会话导航的唯一写入入口。
 *
 * 当前会话以 URL Hash 为唯一事实来源：任何"选中/取消选中会话"的动作都只写 Hash，
 * 再由路由监听器同步到状态。若同时存在"状态写 Hash"和"Hash 写状态"两条回路，
 * 两者会在同一次提交中互相覆盖，导致点击会话后立刻被重置回空态。
 */

/** 返回指定会话对应的 Hash；传入 null 表示工作台空态。 */
export function conversationHash(id: number | null): string {
  return id === null ? '#/workspace' : `#/workspace/conversation/${id}`
}

/**
 * 跳转到指定会话。
 * @param id 目标会话 ID，null 表示回到工作台空态。
 */
export function navigateToConversation(id: number | null): void {
  const next = conversationHash(id)
  if (window.location.hash !== next) window.location.hash = next
}
