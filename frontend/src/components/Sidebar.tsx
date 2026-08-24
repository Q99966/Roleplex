import { useState, useMemo } from 'react'
import { 
  Bot, MessageCircle, Plus, Search, Users, WandSparkles, 
  Trash2, Pin, Archive, ChevronRight, LogOut, Settings2, PanelLeftClose, Sparkles
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { usePetStore } from '../store/pet'
import { navigateToConversation } from '../router'
import { type Role } from '../api/client'


interface SidebarProps {
  isCollapsed: boolean
  onToggleCollapse: () => void
  onOpenSettings: () => void
  onOpenRoleModal: (role?: Role) => void
  onOpenConvModal: () => void
  onOpenRecycleBin: () => void
}

/** 侧边栏组件。 */
export function Sidebar({ isCollapsed, onToggleCollapse, onOpenSettings, onOpenRoleModal, onOpenConvModal, onOpenRecycleBin }: SidebarProps) {
  const { conversations, activeConversationId, roles, user, logout } = useAppStore()
  const [searchTerm, setSearchTerm] = useState('')
  const [failedAvatars, setFailedAvatars] = useState<number[]>([])
  const updateConversationPreferences = useAppStore((state) => state.updateConversationPreferences)
  const deleteConversation = useAppStore((state) => state.deleteConversation)

  const filteredConversations = useMemo(() => {
    return conversations.filter(c => c.title.toLowerCase().includes(searchTerm.toLowerCase()))
  }, [conversations, searchTerm])

  return (
    <aside className={`flex flex-col border-r border-slate-800 bg-slate-900 text-slate-300 transition-all duration-300 ease-in-out shrink-0 h-full ${
      isCollapsed ? 'w-0 overflow-hidden opacity-0 border-r-0' : 'w-80'
    }`}>
      {/* 头部区 */}
      <div className="flex items-center justify-between border-b border-slate-800/80 px-5 py-5">
        <div 
          onClick={() => { window.location.hash = '#/workspace' }}
          className="flex items-center gap-2.5 cursor-pointer hover:opacity-90 transition-opacity"
          title="返回工作台主页"
        >
          <div className="rounded-lg bg-indigo-600 p-2 text-white shadow-md shadow-indigo-600/30">
            <WandSparkles size={18} />
          </div>
          <span className="font-bold text-white text-lg tracking-wide bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">Roleplex</span>
        </div>
        <div className="flex items-center gap-1">
          <button 
            type="button" 
            onClick={onOpenConvModal}
            title="新建会话"
            aria-label="新建会话" 
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white transition-all border border-transparent hover:border-slate-700"
          >
            <Plus size={18} />
          </button>
          <button 
            type="button" 
            onClick={onToggleCollapse}
            title="收起侧边栏"
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white transition-all border border-transparent hover:border-slate-700"
          >
            <PanelLeftClose size={18} />
          </button>
        </div>
      </div>

      {/* 搜索栏 */}
      <div className="px-4 pt-4">
        <div className="flex items-center gap-2 rounded-xl bg-slate-950/80 px-3.5 py-2.5 text-slate-500 border border-slate-800/80 focus-within:border-indigo-500/50 focus-within:bg-slate-950 transition-all">
          <Search size={16} className="shrink-0" />
          <input 
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            aria-label="搜索会话" 
            className="w-full bg-transparent text-sm text-slate-300 outline-none placeholder:text-slate-600" 
            placeholder="搜索会话" 
          />
        </div>
      </div>

      {/* 会话列表 */}
      <div className="flex items-center justify-between px-5 pb-2 pt-6 text-xs font-semibold uppercase tracking-wider text-slate-500">
        <div className="flex items-center gap-2">
          <MessageCircle size={14} />
          <span>会话列表</span>
        </div>
        <button
          type="button"
          onClick={onOpenRecycleBin}
          title="回收站"
          className="flex items-center gap-1 rounded-lg px-2 py-1 normal-case tracking-normal text-slate-500 transition hover:bg-slate-800/60 hover:text-amber-300"
        >
          <Trash2 size={13} />
          <span>回收站</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 space-y-1">
        {filteredConversations.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-slate-600">
            {searchTerm ? '未找到相关会话' : '还没有会话'}
            <br />
            <span className="text-xs text-slate-700 mt-1 block">创建角色并开启第一次对话</span>
          </div>
        ) : (
          filteredConversations.map((conversation) => {
            const isActive = activeConversationId === conversation.id
            return (
              <div 
                key={conversation.id}
                className={`group relative mb-1 flex w-full items-center justify-between rounded-xl px-3 py-3 transition-all cursor-pointer ${
                  isActive 
                    ? 'bg-indigo-600/15 border border-indigo-500/20 text-white' 
                    : 'hover:bg-slate-800/50 border border-transparent hover:border-slate-800'
                }`}
                onClick={() => navigateToConversation(conversation.id)}
              >
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className={`rounded-lg p-2 shrink-0 ${isActive ? 'bg-indigo-600 text-white' : 'bg-slate-950 border border-slate-800'}`}>
                    <Users size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{conversation.title}</p>
                    <p className="text-xs text-slate-500 flex items-center gap-1.5 mt-0.5">
                      <span>{conversation.type === 'group' ? '群聊' : '单聊'}</span>
                      {conversation.pinned && <span className="w-1 h-1 rounded-full bg-indigo-400"></span>}
                      {conversation.pinned && <span className="text-indigo-400/90 font-medium">已置顶</span>}
                      {conversation.archived && <span className="w-1 h-1 rounded-full bg-amber-400"></span>}
                      {conversation.archived && <span className="text-amber-400/90 font-medium">已归档</span>}
                    </p>
                  </div>
                </div>

                {/* 会话快捷控制操作（悬浮显示） */}
                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-900 shadow-md rounded-lg p-1 border border-slate-800">
                  <button
                    type="button"
                    title={conversation.pinned ? '取消置顶' : '置顶会话'}
                    onClick={(e) => {
                      e.stopPropagation()
                      void updateConversationPreferences(conversation.id, !conversation.pinned, undefined)
                    }}
                    className={`p-1 rounded hover:bg-slate-800 transition ${conversation.pinned ? 'text-indigo-400' : 'text-slate-400'}`}
                  >
                    <Pin size={13} className={conversation.pinned ? 'fill-indigo-400/30' : ''} />
                  </button>
                  <button
                    type="button"
                    title={conversation.archived ? '取消归档' : '归档会话'}
                    onClick={(e) => {
                      e.stopPropagation()
                      void updateConversationPreferences(conversation.id, undefined, !conversation.archived)
                    }}
                    className={`p-1 rounded hover:bg-slate-800 transition ${conversation.archived ? 'text-amber-400' : 'text-slate-400'}`}
                  >
                    <Archive size={13} className={conversation.archived ? 'fill-amber-400/30' : ''} />
                  </button>
                  <button
                    type="button"
                    title="删除会话"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm(`确定要删除会话 "${conversation.title}" 吗？`)) {
                        void deleteConversation(conversation.id)
                      }
                    }}
                    className="p-1 rounded hover:bg-red-950 hover:text-red-400 text-slate-400 transition"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* 我的 Agent 列表区 */}
      <div className="border-t border-slate-800 p-4 bg-slate-950/20">
        <div className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-slate-500">
          <div className="flex items-center gap-2">
            <Bot size={14} />
            <span>我的 Agent ({roles.length})</span>
          </div>
          <button 
            type="button"
            onClick={() => onOpenRoleModal()}
            className="hover:text-white transition p-0.5"
            title="定制 Agent 角色"
          >
            <Plus size={14} />
          </button>
        </div>
        
        <div className="space-y-1 max-h-36 overflow-y-auto pr-1">
          {roles.map((role) => (
            <div
              key={role.id}
              data-testid="sidebar-role"
              onClick={() => onOpenRoleModal(role)}
              className="flex items-center justify-between rounded-lg px-2.5 py-2 text-sm hover:bg-slate-800/60 cursor-pointer border border-transparent hover:border-slate-800/80 group transition"
            >
              <div className="flex items-center gap-2 min-w-0">
                <div className="rounded-full bg-indigo-900/40 p-1.5 text-indigo-400 border border-indigo-500/20">
                  {role.avatar && !failedAvatars.includes(role.id) ? (
                    <img 
                      src={role.avatar} 
                      alt={role.name} 
                      onError={() => setFailedAvatars(prev => [...prev, role.id])}
                      className="w-3.5 h-3.5 rounded-full object-cover" 
                    />
                  ) : (
                    <Bot size={14} />
                  )}
                </div>
                <span className="truncate text-slate-300 group-hover:text-white font-medium">{role.name}</span>
              </div>
              <ChevronRight size={14} className="opacity-0 group-hover:opacity-100 text-slate-500 transition-opacity" />
            </div>
          ))}
          
          {roles.length === 0 && (
            <p className="px-2 text-xs text-slate-600 py-2">还没有创建角色</p>
          )}
        </div>
      </div>

      {/* 用户状态栏 */}
      <div className="border-t border-slate-800 p-4 flex items-center justify-between bg-slate-950/60">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 font-bold shrink-0">
            {user?.nickname?.[0]?.toUpperCase() || 'O'}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-200 truncate">@{user?.username}</p>
            <p className="text-xs text-slate-500 truncate">工作台 Owner</p>
          </div>
        </div>
        
        <div className="flex items-center gap-1.5">
          <button 
            type="button" 
            onClick={() => {
              if (!usePetStore.getState().enabled) usePetStore.getState().setEnabled(true)
              if (usePetStore.getState().isMinimized) usePetStore.getState().setMinimized(false)
              usePetStore.getState().say('主人，我随时陪伴在你身边！✨', 3500)
            }}
            title="召唤/展开桌宠"
            aria-label="召唤/展开桌宠"
            className="p-2 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-amber-300 transition"
          >
            <Sparkles size={16} />
          </button>
          <button 
            type="button" 
            onClick={onOpenSettings}
            title="打开设置"
            aria-label="打开设置"
            className="p-2 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition"
          >
            <Settings2 size={16} />
          </button>
          <button 
            type="button" 
            onClick={logout}
            title="退出登录"
            className="p-2 rounded-lg text-slate-400 hover:bg-red-950 hover:text-red-400 transition"
          >
            <LogOut size={16} />
          </button>
        </div>

      </div>
    </aside>
  )
}
