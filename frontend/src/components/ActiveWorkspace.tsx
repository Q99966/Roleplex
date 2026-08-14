import { useMemo } from 'react'
import { 
  AlertCircle, Pin, Archive, Trash2, Bot, Plus, Smile, Paperclip, Send 
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { type Role } from '../api/client'

interface ActiveWorkspaceProps {
  isSidebarCollapsed: boolean
  onOpenRoleModal: (role?: Role) => void
}

/** 渲染活跃聊天工作台与会话成员面板（M1 阶段：带生动的模拟效果）。 */
export function ActiveWorkspace({ isSidebarCollapsed, onOpenRoleModal }: ActiveWorkspaceProps) {
  const { conversations, activeConversationId, roles } = useAppStore()
  const updateConversationPreferences = useAppStore((state) => state.updateConversationPreferences)
  const deleteConversation = useAppStore((state) => state.deleteConversation)

  const activeConv = useMemo(() => {
    return conversations.find(c => c.id === activeConversationId) || null
  }, [conversations, activeConversationId])

  // 本地组件模拟成员信息
  const memberRoles = useMemo(() => {
    if (!activeConv) return []
    if (activeConv.type === 'single') {
      return roles.slice(0, 1)
    }
    return roles.slice(0, Math.max(roles.length, 3))
  }, [activeConv, roles])

  const orchestrator = useMemo(() => {
    if (!activeConv || !activeConv.orchestrator_enabled || !activeConv.orchestrator_role_id) return null
    return roles.find(r => r.id === activeConv.orchestrator_role_id) || null
  }, [activeConv, roles])

  // 根据当前会话的成员角色信息，动态生成有趣的聊天室对话，使 M1 的模拟聊天具有生动的展示效果
  const mockChatFeed = useMemo(() => {
    if (!activeConv || memberRoles.length === 0) return []
    const feed = [
      {
        id: 1,
        sender: 'User',
        senderType: 'user',
        text: `大家好，欢迎加入 "${activeConv.title}" 群聊！我们的目标是进行多 Agent 的高效协作。大家准备好了吗？`,
        time: '刚刚'
      }
    ]

    memberRoles.forEach((role, idx) => {
      feed.push({
        id: 2 + idx * 2,
        sender: role.name,
        senderType: 'agent',
        text: `你好！我是 Agent **${role.name}**。我已被指派并加入该会话。
我的系统设定是：*"${role.description || role.system_prompt.slice(0, 80) + '...'}"*。
我运行在大模型 \`${role.model_name}\` 上，已准备就绪，随时可以开展工作。`,
        time: '刚刚'
      })

      if (activeConv.orchestrator_enabled && activeConv.orchestrator_role_id === role.id) {
        feed.push({
          id: 3 + idx * 2,
          sender: 'Roleplex 协调系统',
          senderType: 'system',
          text: `🤖 **[发言调度]** 协调者调度机制已开启。作为本群协调者，Agent **${role.name}** 正在规划任务分工...`,
          time: '刚刚'
        })
      }
    })

    return feed
  }, [activeConv, memberRoles])

  if (!activeConv) {
    return <section className="flex flex-1 items-center justify-center text-slate-500 bg-slate-950">未选中任何会话</section>
  }

  return (
    <section className="flex flex-1 h-full overflow-hidden bg-slate-950 text-slate-300">
      {/* 聊天主面板 */}
      <div className="flex-1 flex flex-col h-full min-w-0">
        
        {/* 聊天室头部 */}
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
            </div>
            <p className="text-xs text-slate-500 mt-0.5 truncate">
              会话成员: {memberRoles.map(r => r.name).join(', ') || '未关联角色'}
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

        {/* 聊天信息区 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* M1 阶段提示 */}
          <div className="rounded-2xl bg-indigo-950/20 border border-indigo-900/40 p-4 text-sm text-indigo-300/90 leading-relaxed flex items-start gap-3">
            <AlertCircle size={18} className="shrink-0 text-indigo-400 mt-0.5 animate-pulse" />
            <div>
              <p className="font-semibold text-indigo-300">💡 M1 阶段演示通知</p>
              <p className="text-xs text-indigo-400/80 mt-1">
                当前阶段已打通模型密钥、角色设置、会话管理的完整后端数据库交互存储。
                聊天流式对话、群聊 Orchestrator 智能轮序和 MCP 连接将在 **M2 里程碑**正式接入。
                当前下方展示为您根据本群成员生成的**实时模拟互动效果**。
              </p>
            </div>
          </div>

          {/* 对话消息 */}
          <div className="space-y-4">
            {mockChatFeed.map((msg) => {
              if (msg.senderType === 'system') {
                return (
                  <div key={msg.id} className="flex justify-center">
                    <div className="bg-slate-900 border border-slate-850 px-4 py-2 rounded-xl text-xs text-slate-400 max-w-xl text-center">
                      {msg.text}
                    </div>
                  </div>
                )
              }
              const isUser = msg.senderType === 'user'
              return (
                <div key={msg.id} className={`flex gap-3.5 max-w-2xl ${isUser ? 'ml-auto flex-row-reverse' : ''}`}>
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-xs border shrink-0 ${
                    isUser 
                      ? 'bg-slate-800 text-slate-200 border-slate-700' 
                      : 'bg-indigo-900/40 text-indigo-400 border-indigo-500/20'
                  }`}>
                    {isUser ? 'ME' : msg.sender[0]}
                  </div>
                  <div>
                    <div className={`flex items-center gap-2 mb-1.5 ${isUser ? 'justify-end' : ''}`}>
                      <span className="text-xs font-semibold text-slate-300">{msg.sender}</span>
                      <span className="text-[10px] text-slate-600">{msg.time}</span>
                    </div>
                    <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed border whitespace-pre-line ${
                      isUser 
                        ? 'bg-indigo-600 text-white border-indigo-500' 
                        : 'bg-slate-900/60 text-slate-300 border-slate-800'
                    }`}>
                      {msg.text}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 聊天输入框 (M1 模拟禁用状态) */}
        <div className="p-4 border-t border-slate-800 bg-slate-900">
          <div className="flex items-center gap-2 rounded-xl bg-slate-950/80 p-2.5 border border-slate-800 focus-within:border-indigo-500/40 transition-all opacity-70">
            <button type="button" disabled className="p-1.5 text-slate-650 hover:text-slate-450 rounded transition shrink-0">
              <Plus size={16} />
            </button>
            <input 
              disabled 
              placeholder="消息发送将在 M2 接入..." 
              className="w-full bg-transparent text-sm text-slate-400 outline-none placeholder:text-slate-600 cursor-not-allowed" 
            />
            <div className="flex items-center gap-1 shrink-0">
              <button type="button" disabled className="p-1.5 text-slate-650 hover:text-slate-450 rounded transition">
                <Smile size={16} />
              </button>
              <button type="button" disabled className="p-1.5 text-slate-650 hover:text-slate-450 rounded transition">
                <Paperclip size={16} />
              </button>
              <button type="button" disabled className="p-2 rounded-lg bg-indigo-600/40 text-slate-400 cursor-not-allowed transition ml-1">
                <Send size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 会话属性/成员抽屉 (右侧面板) */}
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
                    <span className="text-[9px] bg-slate-900 text-slate-400 px-1.5 py-0.5 rounded border border-slate-855 truncate max-w-full">
                      {role.model_name}
                    </span>
                    {role.tags.slice(0, 2).map((tag, i) => (
                      <span key={i} className="text-[9px] bg-slate-900 text-indigo-400 px-1.5 py-0.5 rounded border border-slate-855">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              ))}

              {memberRoles.length === 0 && (
                <div className="text-center py-6 text-xs text-slate-600 bg-slate-950/30 rounded-xl border border-slate-855">
                  会话内暂未绑定任何角色
                </div>
              )}
            </div>
          </div>
          
          {orchestrator && (
            <div className="rounded-xl bg-slate-950/40 border border-slate-855 p-4">
              <span className="text-[10px] text-slate-500 font-semibold block uppercase">群聊协调策略</span>
              <p className="text-xs font-bold text-slate-200 mt-1.5">智能发言分流器开启</p>
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                由 Agent **{orchestrator.name}** 接管，通过系统机制智能判断谁该发言，防止抢麦与刷屏。
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
