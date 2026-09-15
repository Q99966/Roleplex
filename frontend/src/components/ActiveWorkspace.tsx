import { ConversationDetails } from './ConversationDetails'
import { useEffect, useMemo, useState } from 'react'
import {
  Pin, Archive, Trash2, Loader2, WifiOff, AlertCircle, PanelRightOpen, Activity
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'
import { type Conversation, type Message, type Role } from '../api/client'
import { MessageParts } from './MessageParts'
import { ShellApprovals } from './ShellApprovals'
import { ProcessPanel } from './ProcessPanel'
import { ChatComposer } from './ChatComposer'
import { useReadingPosition } from './useReadingPosition'

interface ActiveWorkspaceProps {
  isSidebarCollapsed: boolean
  onOpenRoleModal: (role?: Role) => void
  onManageMembers: (conversation: Conversation) => void
}

/**
 * 把稳定错误码转换为不泄露 Owner 私有配置的用户提示。
 * @param code 服务端 message_done 返回的稳定错误码或普通错误文本。
 * @param isOwner 当前登录者是否为本世界 Owner。
 */
function chatErrorMessage(code: string, isOwner: boolean): string {
  const connectionErrors: Record<string, string> = {
    HISTORY_LOAD_TIMEOUT: '会话历史加载超时，请重试。',
    WS_CONNECTION_FAILED: '实时连接失败，请检查网络或后端后重试。',
    WS_PROTOCOL_UNSUPPORTED: '后端尚不支持当前订阅协议，请更新并重启后端。',
    WS_SYNC_TIMEOUT: '会话同步超时，请重试。',
    WS_SYNC_FAILED: '会话同步未完成，请重试。',
  }
  if (connectionErrors[code]) return connectionErrors[code]
  if (code === 'CONTEXT_BUDGET_EXCEEDED') {
    return isOwner
      ? '当前消息与角色基础配置超过模型上下文上限。请缩短消息，或调整角色提示词、工具配置、上下文窗口或最大输出长度后重试。'
      : '本次请求超过模型可处理的上下文上限。请缩短消息后重试，或联系 Owner 调整角色配置。'
  }
  return code
}

/** 渲染会话消息、进程入口与可切换详情，右栏操作不改变聊天状态。
 * @param isSidebarCollapsed 左侧导航是否收起，用于顶部留白。
 * @param onOpenRoleModal 编辑 Owner 已授权的角色。
 * @param onManageMembers 打开现有群聊成员管理。
 */
export function ActiveWorkspace({ isSidebarCollapsed, onOpenRoleModal, onManageMembers }: ActiveWorkspaceProps) {
  const { conversations, activeConversationId, roles, roleDirectory, user, worldName } = useAppStore()
  const updateConversationPreferences = useAppStore((state) => state.updateConversationPreferences)
  const deleteConversation = useAppStore((state) => state.deleteConversation)

  const { messages, loading, connection, subscription, error,
    nextCursor, loadingOlder, olderError, oversized, loadOlder } = useChatStore()
  const retryConnection = useChatStore((state) => state.retryConnection)
  const [processPanel, setProcessPanel] = useState(false)
  const [wideDetails, setWideDetails] = useState(() => window.matchMedia('(min-width: 1280px)').matches)
  const [detailsOpen, setDetailsOpen] = useState(wideDetails)
  useEffect(() => {
    const media = window.matchMedia('(min-width: 1280px)')
    const resize = () => { setWideDetails(media.matches); if (!media.matches) setDetailsOpen(false) }
    media.addEventListener('change', resize)
    return () => media.removeEventListener('change', resize)
  }, [])
  const [processNotice, setProcessNotice] = useState('')
  const { feedRef, contentRef, onScroll, goBottom, newContent } = useReadingPosition(activeConversationId)

  const activeConv = useMemo(
    () => conversations.find((c) => c.id === activeConversationId) || null,
    [conversations, activeConversationId],
  )

  // 成员来自后端会话绑定的角色，不再由前端推测。
  const memberRoles = useMemo(() => {
    if (!activeConv) return []
    return activeConv.role_ids
      .map((roleId) => roles.find((role) => role.id === roleId))
      .filter((role): role is Role => Boolean(role))
  }, [activeConv, roles])

  if (!activeConv) {
    return <section className="flex flex-1 items-center justify-center text-slate-500 bg-slate-950">未选中任何会话</section>
  }

  return (
    <section className="flex flex-1 min-w-0 h-full overflow-hidden bg-slate-950 text-slate-300">
      <div className="flex-1 flex flex-col h-full min-w-0">
        {processPanel && user?.is_owner && <ProcessPanel key={`process:${worldName}:${user.id}:${activeConv.id}`} conversationId={activeConv.id} onClose={() => setProcessPanel(false)} />}
        <div className="h-16 shrink-0 border-b border-slate-800 bg-slate-900 px-3 sm:px-6 flex items-center justify-between gap-2">
          <div className={`min-w-0 transition-all duration-300 ${isSidebarCollapsed ? 'pl-10' : ''}`}>
            <div className="flex items-center gap-2">
              <h2 className="font-bold text-white text-base truncate">{activeConv.title}</h2>
              <span className="px-2 py-0.5 rounded bg-slate-800 text-[10px] text-slate-400 font-medium">
                {activeConv.type === 'group' ? '群聊' : '单聊'}
              </span>
              {activeConv.orchestrator_enabled && (
                <span className="px-2 py-0.5 rounded bg-indigo-950 text-[10px] text-indigo-400 font-medium border border-indigo-900/50">
                  Orchestrator
                </span>
              )}
              {(connection === 'reconnecting' || connection === 'failed') && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-amber-950/60 text-[10px] text-amber-400 border border-amber-900/50">
                  <WifiOff size={10} />{connection === 'reconnecting' ? '正在重新连接' : '实时连接失败'}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5 truncate">
              {(['connecting', 'authenticating'].includes(connection) || subscription === 'syncing') && connection !== 'reconnecting' && connection !== 'failed' && (
                <span role="status" className="mr-2 inline-flex items-center gap-1 text-[10px] text-slate-400">
                  <Loader2 size={10} className="animate-spin" />
                  {connection === 'connecting' ? '正在连接实时更新' : connection === 'authenticating' ? '正在认证连接' : '正在同步会话'}
                </span>
              )}
              会话成员: {memberRoles.map((r) => r.name).join(', ') || (activeConv.role_ids.length ? `${activeConv.role_ids.length} 位角色` : '未关联角色')}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {user?.is_owner && <button type="button" aria-label="会话进程" title="会话进程" onClick={() => setProcessPanel(true)} className="rounded-lg p-2 text-indigo-600 hover:bg-slate-800"><Activity size={16} /></button>}
            <button type="button" aria-label={detailsOpen ? '关闭会话详情' : '打开会话详情'} title="会话详情" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(value => !value)} className="rounded-lg p-2 text-indigo-600 hover:bg-slate-800"><PanelRightOpen size={16} /></button>
            <button
              type="button"
              title={activeConv.pinned ? '取消置顶' : '置顶会话'}
              onClick={() => void updateConversationPreferences(activeConv.id, !activeConv.pinned, undefined)}
              className={`p-2 rounded-lg hover:bg-slate-800 transition ${activeConv.pinned ? 'text-indigo-400 bg-indigo-500/10' : 'text-slate-400'}`}
            >
              <Pin size={15} className={activeConv.pinned ? 'fill-indigo-400/20' : ''} />
            </button>
            <button
              type="button"
              title={activeConv.archived ? '取消归档' : '归档会话'}
              onClick={() => void updateConversationPreferences(activeConv.id, undefined, !activeConv.archived)}
              className={`p-2 rounded-lg hover:bg-slate-800 transition ${activeConv.archived ? 'text-amber-400 bg-amber-500/10' : 'text-slate-400'}`}
            >
              <Archive size={15} className={activeConv.archived ? 'fill-amber-400/20' : ''} />
            </button>
            <button
              type="button"
              title="删除会话"
              onClick={() => {
                if (confirm(`确定要删除会话 "${activeConv.title}" 吗？`)) {
                  void deleteConversation(activeConv.id)
                }
              }}
              className="p-2 rounded-lg hover:bg-red-950 hover:text-red-400 text-slate-400 transition"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>

        <div ref={feedRef} role="log" aria-label="会话消息" aria-live="off" onScroll={onScroll}
          style={{ overflowAnchor: 'none' }} className="flex-1 overflow-y-auto p-6">
          <div ref={contentRef} className="space-y-4">
          {nextCursor && <div className="text-center text-xs text-slate-400">
            {olderError && <p role="alert" className="mb-2 text-amber-400">{olderError}</p>}
            <button type="button" onClick={() => void loadOlder()} disabled={loadingOlder || subscription !== 'ready'}
              className="rounded-lg border border-slate-700 px-3 py-2 hover:bg-slate-800 disabled:opacity-50">
              {loadingOlder ? '正在加载更早消息…' : olderError ? '重试加载更早消息' : '加载更早消息'}
            </button>
          </div>}
          {oversized && <p className="text-center text-xs text-slate-500">包含超出单页预算的完整消息，加载和显示可能较慢。</p>}
          {loading && (
            <div className="flex items-center justify-center gap-2 text-xs text-slate-500 py-6">
              <Loader2 size={14} className="animate-spin" />正在加载会话历史…
            </div>
          )}

          {!loading && messages.length === 0 && (
            <div className="text-center py-16 text-sm text-slate-600">
              还没有消息，发送第一条消息开始对话。
            </div>
          )}

          {messages.map((message: Message) => {
            const isUser = message.sender_type === 'user'
            // 用含墓碑的查找表：角色被删除后仍要显示原名称，否则历史会变成匿名 Agent。
            const role = message.sender_id === null ? undefined : roleDirectory[message.sender_id]
            const senderName = isUser ? (user?.nickname ?? '我') : (role?.name ?? 'Agent')
            const senderDeleted = !isUser && Boolean(role?.deleted_at)
            const budgetStopped = message.stop_reason === 'graph_budget'
            return (
              <div key={message.id} data-testid="chat-message" data-reading-anchor={`m-${message.id}`} className={`flex gap-3.5 max-w-2xl ${isUser ? 'ml-auto flex-row-reverse' : ''}`}>
                <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-xs border shrink-0 ${
                  isUser ? 'bg-slate-800 text-slate-200 border-slate-700' : 'bg-indigo-900/40 text-indigo-400 border-indigo-500/20'
                }`}>
                  {isUser ? 'ME' : senderName.slice(0, 1)}
                </div>
                <div className="min-w-0">
                  <div className={`flex flex-wrap items-center gap-2 mb-1.5 ${isUser ? 'justify-end' : ''}`}>
                    <span className="text-xs font-semibold text-slate-300">{senderName}</span>
                    {senderDeleted && (
                      <span className="text-[10px] text-slate-500 border border-slate-700 rounded px-1 py-px">已删除</span>
                    )}
                    {message.status === 'generating' && <span className="text-[10px] text-indigo-400">生成中…</span>}
                    {message.status === 'stopped' && <span className="text-[10px] text-amber-400">{budgetStopped ? '达到本轮执行步数上限' : '已停止'}</span>}
                    {message.status === 'error' && <span className="text-[10px] text-red-400">生成失败</span>}
                  </div>
                  <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed border break-words ${
                    isUser ? 'bg-slate-800 text-slate-100 border-slate-700/80 shadow-sm whitespace-pre-wrap' : 'bg-slate-900/60 text-slate-300 border-slate-800'
                  }`}>
                    <MessageParts key={`${worldName}:${user?.id}:${message.id}`} message={message} isOwner={Boolean(user?.is_owner)} />
                  </div>
                </div>
              </div>
            )
          })}

          {error && (
            <div className="flex items-start gap-2 rounded-xl bg-red-950/30 border border-red-900/40 px-4 py-3 text-xs text-red-300">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <span>{chatErrorMessage(error, Boolean(user?.is_owner))}</span>
              {(subscription === 'failed' || connection === 'failed') && <button type="button" onClick={retryConnection} className="ml-auto shrink-0 underline">重试连接与加载</button>}
            </div>
          )}
        </div>

        </div>
        {newContent && <button type="button" onClick={goBottom} className="self-center my-2 rounded-full bg-indigo-600 px-4 py-2 text-xs text-white">有新内容，回到底部</button>}
        <ShellApprovals key={`${worldName}:${user?.id}:${activeConv.id}`} conversationId={activeConv.id} />
        {processNotice && <p role="alert" className="px-4 text-xs text-amber-300">{processNotice}</p>}
        <ChatComposer conversationId={activeConv.id} group={activeConv.type === 'group'} memberRoles={memberRoles}
          // Guest 无权读取 Owner 角色配置；公开成员 ID 决定入口，最终可回复性仍由后端复核。
          hasReplyRole={user?.is_owner ? memberRoles.some((role) => role.active && !role.deleted_at) : activeConv.role_ids.length > 0}
          onProcessCommand={() => {
            if (user?.is_owner) setProcessPanel(true)
            else setProcessNotice('仅 Owner 可查看和管理会话进程。')
          }} />
      </div>

      <ConversationDetails conversation={activeConv} open={detailsOpen} drawer={!wideDetails} onClose={() => setDetailsOpen(false)}
        onEditRole={onOpenRoleModal} onManageMembers={onManageMembers} />
    </section>
  )
}
