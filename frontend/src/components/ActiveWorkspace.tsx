import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import {
  Pin, Archive, Trash2, Bot, Send, Square, Loader2, WifiOff, AlertCircle, UsersRound, X
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { useChatStore } from '../store/chat'
import { type Conversation, type Message, type Part, type Role } from '../api/client'

const MarkdownRenderer = lazy(async () => {
  const module = await import('./MarkdownRenderer')
  return { default: module.MarkdownRenderer }
})

interface ActiveWorkspaceProps {
  isSidebarCollapsed: boolean
  onOpenRoleModal: (role?: Role) => void
  onManageMembers: (conversation: Conversation) => void
}

/** 读取消息中的文本内容；未知 part 类型会被渲染为占位提示。 */
function messageText(parts: Part[]): string {
  return parts.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('')
}

/** 列出消息中当前尚未支持渲染的 part 类型。 */
function unknownPartTypes(parts: Part[]): string[] {
  return Array.from(new Set(
    parts.filter((part) => !['text', 'tool_call'].includes(part.type)).map((part) => part.type),
  ))
}

/**
 * 把稳定错误码转换为不泄露 Owner 私有配置的用户提示。
 * @param code 服务端 message_done 返回的稳定错误码或普通错误文本。
 * @param isOwner 当前登录者是否为本世界 Owner。
 */
function chatErrorMessage(code: string, isOwner: boolean): string {
  if (code === 'CONTEXT_BUDGET_EXCEEDED') {
    return isOwner
      ? '当前消息与角色基础配置超过模型上下文上限。请缩短消息，或调整角色提示词、工具配置、上下文窗口或最大输出长度后重试。'
      : '本次请求超过模型可处理的上下文上限。请缩短消息后重试，或联系 Owner 调整角色配置。'
  }
  return code
}

/** 渲染单聊与 M4a 群聊工作台：历史、@ 补全、串行队列、停止和成员面板。 */
export function ActiveWorkspace({ isSidebarCollapsed, onOpenRoleModal, onManageMembers }: ActiveWorkspaceProps) {
  const { conversations, activeConversationId, roles, roleDirectory, user } = useAppStore()
  const updateConversationPreferences = useAppStore((state) => state.updateConversationPreferences)
  const deleteConversation = useAppStore((state) => state.deleteConversation)

  const { messages, loading, sending, generating, activeGenerationIds, connection, error } = useChatStore()
  const openConversation = useChatStore((state) => state.openConversation)
  const closeConversation = useChatStore((state) => state.closeConversation)
  const sendMessage = useChatStore((state) => state.sendMessage)
  const stopGeneration = useChatStore((state) => state.stopGeneration)

  const [draft, setDraft] = useState('')
  const [mentions, setMentions] = useState<Array<number | 'all'>>([])
  const feedRef = useRef<HTMLDivElement | null>(null)

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
  const hasReplyRole = memberRoles.some((role) => role.active && !role.deleted_at)
  const mentionMatch = activeConv?.type === 'group' ? draft.match(/@([^\s@]*)$/) : null
  const mentionQuery = mentionMatch?.[1]?.toLocaleLowerCase() ?? ''
  const mentionSuggestions = activeConv?.type === 'group' && mentionMatch
    ? memberRoles.filter((role) => role.name.toLocaleLowerCase().includes(mentionQuery))
    : []

  const orchestrator = useMemo(() => {
    if (!activeConv?.orchestrator_enabled || !activeConv.orchestrator_role_id) return null
    return roles.find((r) => r.id === activeConv.orchestrator_role_id) || null
  }, [activeConv, roles])

  useEffect(() => {
    if (activeConversationId !== null) void openConversation(activeConversationId)
    return () => closeConversation()
  }, [activeConversationId, openConversation, closeConversation])

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight })
  }, [messages])

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!hasReplyRole) return
    const text = draft
    setDraft('')
    const selectedMentions = mentions
    setMentions([])
    void sendMessage(text, selectedMentions)
  }

  /** @param target 选择的角色 ID，`all` 表示按稳定成员顺序全部回复。 */
  function selectMention(target: number | 'all') {
    if (!mentionMatch || mentionMatch.index === undefined) return
    const label = target === 'all' ? '全部' : memberRoles.find((role) => role.id === target)?.name
    if (!label) return
    setDraft(`${draft.slice(0, mentionMatch.index)}@${label} `)
    setMentions((current) => {
      if (target === 'all') return ['all']
      if (current.includes('all') || current.includes(target)) return current
      return [...current, target]
    })
  }

  /** @param target 从本轮待发送 mentions 中移除的稳定目标。 */
  function removeMention(target: number | 'all') {
    setMentions((current) => current.filter((item) => item !== target))
  }

  if (!activeConv) {
    return <section className="flex flex-1 items-center justify-center text-slate-500 bg-slate-950">未选中任何会话</section>
  }

  return (
    <section className="flex flex-1 h-full overflow-hidden bg-slate-950 text-slate-300">
      <div className="flex-1 flex flex-col h-full min-w-0">
        <div className="h-16 shrink-0 border-b border-slate-800 bg-slate-900 px-6 flex items-center justify-between">
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
              {connection === 'closed' && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-amber-950/60 text-[10px] text-amber-400 border border-amber-900/50">
                  <WifiOff size={10} />连接已断开
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5 truncate">
              会话成员: {memberRoles.map((r) => r.name).join(', ') || '未关联角色'}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              title={activeConv.pinned ? '取消置顶' : '置顶会话'}
              onClick={() => void updateConversationPreferences(activeConv.id, !activeConv.pinned, undefined)}
              className={`p-2 rounded-lg hover:bg-slate-800 transition ${activeConv.pinned ? 'text-indigo-400 bg-indigo-500/10' : 'text-slate-400'}`}
            >
              <Pin size={15} className={activeConv.pinned ? 'fill-indigo-400/20' : ''} />
            </button>
            {activeConv.type === 'group' && user?.is_owner && (
              <button
                type="button"
                title="管理群聊成员"
                aria-label="管理群聊成员"
                onClick={() => onManageMembers(activeConv)}
                className="p-2 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-indigo-300 transition"
              >
                <UsersRound size={15} />
              </button>
            )}
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

        <div ref={feedRef} className="flex-1 overflow-y-auto p-6 space-y-4">
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
            const text = messageText(message.parts_json)
            const toolParts = message.parts_json.filter((part) => part.type === 'tool_call')
            const unknown = unknownPartTypes(message.parts_json)
            return (
              <div key={message.id} data-testid="chat-message" className={`flex gap-3.5 max-w-2xl ${isUser ? 'ml-auto flex-row-reverse' : ''}`}>
                <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-xs border shrink-0 ${
                  isUser ? 'bg-slate-800 text-slate-200 border-slate-700' : 'bg-indigo-900/40 text-indigo-400 border-indigo-500/20'
                }`}>
                  {isUser ? 'ME' : senderName.slice(0, 1)}
                </div>
                <div className="min-w-0">
                  <div className={`flex items-center gap-2 mb-1.5 ${isUser ? 'justify-end' : ''}`}>
                    <span className="text-xs font-semibold text-slate-300">{senderName}</span>
                    {senderDeleted && (
                      <span className="text-[10px] text-slate-500 border border-slate-700 rounded px-1 py-px">已删除</span>
                    )}
                    {message.status === 'generating' && <span className="text-[10px] text-indigo-400">生成中…</span>}
                    {message.status === 'stopped' && <span className="text-[10px] text-amber-400">已停止</span>}
                    {message.status === 'error' && <span className="text-[10px] text-red-400">生成失败</span>}
                  </div>
                  <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed border break-words ${
                    isUser ? 'bg-indigo-600 text-white border-indigo-500 whitespace-pre-wrap' : 'bg-slate-900/60 text-slate-300 border-slate-800'
                  }`}>
                    {isUser ? (
                      text
                    ) : (
                      <Suspense fallback={<span className="whitespace-pre-wrap">{text || '…'}</span>}>
                        <MarkdownRenderer
                          content={text}
                          isGenerating={message.status === 'generating'}
                        />
                      </Suspense>
                    )}
                    {unknown.length > 0 && (
                      <p className="mt-2 text-[10px] text-slate-500">
                        [当前版本暂不支持渲染的内容：{unknown.join(', ')}]
                      </p>
                    )}
                    {toolParts.map((part) => (
                      <div key={part.call_id} className="mt-2 rounded-xl border border-slate-700/70 bg-slate-950/60 px-3 py-2 text-[10px] text-slate-400">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-mono text-indigo-300">{part.tool_name ?? 'tool'}</span>
                          <span className={part.status === 'failed' || part.status === 'rejected' ? 'text-red-400' : 'text-emerald-400'}>
                            {part.status === 'running' ? '执行中' : part.status === 'success' ? '已完成' : part.status === 'rejected' ? '已拒绝' : part.status === 'cancelled' ? '已取消' : '失败'}
                          </span>
                        </div>
                        {typeof part.duration_ms === 'number' && <p className="mt-1 text-slate-600">耗时 {part.duration_ms}ms</p>}
                        {part.command && <p>命令 {part.command}</p>}
                        {typeof part.exit_code === 'number' && <p>退出码 {part.exit_code}</p>}
                        {part.command_status === 'timed_out' && <p className="text-amber-400">命令超时，进程已回收</p>}
                        {part.truncated && <p className="text-amber-400">输出已截断</p>}
                        {part.error_code && <p className="text-red-400">{part.error_code}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )
          })}

          {error && (
            <div className="flex items-start gap-2 rounded-xl bg-red-950/30 border border-red-900/40 px-4 py-3 text-xs text-red-300">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <span>{chatErrorMessage(error, Boolean(user?.is_owner))}</span>
            </div>
          )}
        </div>

        <form onSubmit={submit} className="p-4 border-t border-slate-800 bg-slate-900">
          {!hasReplyRole && (
            <div className="mb-2 flex items-center gap-2 rounded-lg border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
              <AlertCircle size={13} className="shrink-0" />
              <span>角色已删除，当前会话仅可查看历史消息。</span>
            </div>
          )}
          {activeConv.type === 'group' && (
            <div className="mb-2 flex min-h-6 flex-wrap items-center gap-1.5">
              {mentions.map((target) => {
                const label = target === 'all' ? '全部' : memberRoles.find((role) => role.id === target)?.name
                if (!label) return null
                return (
                  <span key={target} className="flex items-center gap-1 rounded-full border border-indigo-500/30 bg-indigo-950/40 px-2 py-1 text-[10px] text-indigo-300">
                    @{label}
                    <button type="button" onClick={() => removeMention(target)} aria-label={`移除 @${label}`}>
                      <X size={10} />
                    </button>
                  </span>
                )
              })}
              {mentions.length === 0 && <span className="text-[10px] text-slate-600">无 @ 时消息只记录，不触发 Agent。</span>}
            </div>
          )}
          <div className="relative flex items-center gap-2 rounded-xl bg-slate-950/80 p-2.5 border border-slate-800 focus-within:border-indigo-500/40 transition-all">
            {activeConv.type === 'group' && mentionMatch && (
              <div className="absolute bottom-full left-0 z-20 mb-2 w-64 overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl" role="listbox" aria-label="@ 角色补全">
                <button type="button" role="option" aria-selected={false} onClick={() => selectMention('all')} className="block w-full px-3 py-2 text-left text-xs text-indigo-300 hover:bg-slate-800">
                  @全部 · 按成员顺序回复
                </button>
                {mentionSuggestions.map((role) => (
                  <button key={role.id} type="button" role="option" aria-selected={mentions.includes(role.id)} onClick={() => selectMention(role.id)} className="block w-full px-3 py-2 text-left text-xs text-slate-300 hover:bg-slate-800">
                    @{role.name}
                  </button>
                ))}
              </div>
            )}
            <input
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                if (!event.target.value.trim()) setMentions([])
              }}
              disabled={!hasReplyRole}
              placeholder={hasReplyRole ? (activeConv.type === 'group' ? '输入 @ 选择回复角色…' : '输入消息，回车发送…') : '角色已删除，无法继续发送'}
              aria-label="消息输入框"
              className="w-full bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-600"
            />
            <div className="flex items-center gap-1 shrink-0">
              {generating ? (
                <button
                  type="button"
                  onClick={() => void stopGeneration()}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 text-slate-200 hover:bg-slate-700 transition text-xs font-medium"
                >
                  <Square size={12} />
                  {activeConv.type === 'group' ? '停止整条链' : '停止生成'}
                  {activeConv.type === 'group' && activeGenerationIds.length > 1 ? ` · ${activeGenerationIds.length}` : ''}
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={sending || !draft.trim() || !hasReplyRole}
                  aria-label="发送消息"
                  className="p-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition ml-1"
                >
                  <Send size={14} />
                </button>
              )}
            </div>
          </div>
        </form>
      </div>

      <div className="w-72 border-l border-slate-800 bg-slate-900 shrink-0 flex flex-col h-full overflow-hidden">
        <div className="h-16 shrink-0 border-b border-slate-800 px-5 flex items-center bg-slate-900/40">
          <span className="font-semibold text-sm text-slate-400 tracking-wide uppercase">会话成员 ({memberRoles.length})</span>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div>
            <span className="text-xs text-slate-600 font-semibold uppercase tracking-wider block mb-2">活跃 Agent 实例</span>
            <div className="space-y-2">
              {memberRoles.map((role) => (
                <div
                  key={role.id}
                  onClick={() => onOpenRoleModal(role)}
                  className="bg-slate-950/60 hover:bg-slate-950 border border-slate-800/80 hover:border-slate-800 rounded-xl p-3.5 transition cursor-pointer group"
                >
                  <div className="flex items-center gap-2">
                    <div className="rounded-full bg-indigo-900/40 p-1.5 text-indigo-400 border border-indigo-500/20 shrink-0">
                      <Bot size={13} />
                    </div>
                    <span className="text-xs font-bold text-slate-200 group-hover:text-white transition truncate">{role.name}</span>
                    {activeConv.orchestrator_enabled && activeConv.orchestrator_role_id === role.id && (
                      <span className="ml-auto text-[9px] bg-indigo-950 text-indigo-400 px-1.5 py-0.5 rounded border border-indigo-900/50 uppercase shrink-0 font-medium">调度者</span>
                    )}
                  </div>
                  <p className="text-[11px] text-slate-500 mt-2 line-clamp-2 leading-relaxed italic">
                    {role.description || '暂无描述。'}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-1">
                    <span className="text-[9px] bg-slate-900 text-slate-400 px-1.5 py-0.5 rounded border border-slate-800 truncate max-w-full">
                      {role.model_name}
                    </span>
                    {role.tags.slice(0, 2).map((tag, i) => (
                      <span key={i} className="text-[9px] bg-slate-900 text-indigo-400 px-1.5 py-0.5 rounded border border-slate-800">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              ))}

              {memberRoles.length === 0 && (
                <div className="text-center py-6 text-xs text-slate-600 bg-slate-950/30 rounded-xl border border-slate-800">
                  会话内暂未绑定任何角色
                </div>
              )}
            </div>
          </div>

          {orchestrator && (
            <div className="rounded-xl bg-slate-950/40 border border-slate-800 p-4">
              <span className="text-[10px] text-slate-500 font-semibold block uppercase">群聊协调策略</span>
              <p className="text-xs font-bold text-slate-200 mt-1.5">调度者：{orchestrator.name}</p>
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                当前使用 M4a 显式 @ 串行调度；Orchestrator 自动编排将在 M4b 接入。
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
