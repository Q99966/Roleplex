import { useState, useMemo, useRef, useLayoutEffect, useId } from 'react'
import { 
  Bot, MessageCircle, Plus, Search, Users, WandSparkles, 
  Trash2, Pin, Archive, MoreHorizontal, LogOut, Settings2, PanelLeftClose, Sparkles, Globe2
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { usePetStore } from '../store/pet'
import { navigateToConversation } from '../router'
import { type Role } from '../api/client'


interface SidebarProps {
  isCollapsed: boolean
  onToggleCollapse: () => void
  onOpenSettings: (initialTab?: 'models' | 'worlds' | 'workspaces' | 'account') => void
  onOpenRoleModal: (role?: Role) => void
  onOpenConvModal: () => void
  onOpenRecycleBin: () => void
}

/** 会话与角色共用侧栏区域；切换只影响导航列表，不改变活动会话。
 * @param isCollapsed 侧栏是否收起。
 * @param onToggleCollapse 切换侧栏可见性。
 * @param onOpenSettings 打开设置。
 * @param onOpenRoleModal 创建或编辑角色。
 * @param onOpenConvModal 创建会话。
 * @param onOpenRecycleBin 打开会话回收站。
 */
export function Sidebar({ isCollapsed, onToggleCollapse, onOpenSettings, onOpenRoleModal, onOpenConvModal, onOpenRecycleBin }: SidebarProps) {
  const { conversations, activeConversationId, roles, user, logout, worldName, worldSwitchingSupported } = useAppStore()
  const [tab, setTab] = useState<'conversations' | 'roles'>('conversations')
  const [queries, setQueries] = useState({ conversations: '', roles: '' })
  const [roleMenu, setRoleMenu] = useState<number | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const positions = useRef({ conversations: 0, roles: 0 })
  const tabsId = useId()
  const searchTerm = queries.conversations
  useLayoutEffect(() => {
    if (listRef.current) listRef.current.scrollTop = positions.current[tab]
  }, [tab])

  /** 切换列表前保存滚动位置，不触碰活动会话或地址。
   * @param next 下一导航类别。
   */
  function selectTab(next: 'conversations' | 'roles') {
    if (listRef.current) positions.current[tab] = listRef.current.scrollTop
    setRoleMenu(null)
    setTab(next)
  }
  const [failedAvatars, setFailedAvatars] = useState<number[]>([])
  const updateConversationPreferences = useAppStore((state) => state.updateConversationPreferences)
  const deleteConversation = useAppStore((state) => state.deleteConversation)

  const filteredConversations = useMemo(() => {
    return conversations.filter(c => c.title.toLowerCase().includes(searchTerm.trim().toLowerCase()))
  }, [conversations, searchTerm])

  const filteredRoles = useMemo(() => {
    const query = queries.roles.trim().toLocaleLowerCase()
    return roles.filter(role => [role.name, role.description ?? '', ...role.tags].some(value => value.toLocaleLowerCase().includes(query)))
  }, [roles, queries.roles])

  return (
    <aside aria-label="工作区侧栏" className={`flex flex-col border-r border-slate-800 bg-slate-900 text-slate-300 transition-all duration-300 ease-in-out shrink-0 h-full ${
      isCollapsed ? 'w-0 invisible overflow-hidden opacity-0 border-r-0' : 'w-80'
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
          <span className="font-bold text-white text-lg tracking-wide text-ink">Roleplex</span>
        </div>
        <div className="flex items-center gap-1">
          <button 
            type="button" 
            onClick={() => tab === 'conversations' ? onOpenConvModal() : onOpenRoleModal()}
            title={tab === 'conversations' ? '新建会话' : '定制 Agent 角色'}
            aria-label={tab === 'conversations' ? '新建会话' : '定制 Agent 角色'}
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

      {/* 运行世界状态胶囊（点击打开综合设置中心管理/切换世界） */}
      <div className="border-b border-slate-800/80 px-4 py-3">
        <button
          type="button"
          onClick={() => onOpenSettings('worlds')}
          title="点击管理运行世界与存储"
          aria-label={`管理运行世界与存储，当前世界 ${worldName}`}
          className="group flex w-full items-center justify-between gap-2.5 rounded-2xl border border-slate-800/80 bg-slate-950/60 p-3 text-left shadow-sm transition-all hover:border-indigo-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/70 motion-reduce:transition-none"
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-950/70 border border-indigo-500/20 text-indigo-400 shrink-0 group-hover:scale-105 transition-transform motion-reduce:transform-none motion-reduce:transition-none">
              <Globe2 size={16} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-xs font-bold text-white group-hover:text-indigo-300 transition-colors">
                  {worldName}
                </span>
              </div>
              <span className="text-[10px] text-slate-500 block truncate">运行世界 · 点击管理</span>
            </div>
          </div>
          <div className="shrink-0 flex items-center gap-1">
            {worldSwitchingSupported ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-950/70 border border-emerald-800/50 px-2 py-0.5 text-[9px] font-medium text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" />
                可热切换
              </span>
            ) : (
              <span className="rounded bg-slate-900 border border-slate-800 px-1.5 py-0.5 text-[9px] text-slate-500 font-medium">
                单世界
              </span>
            )}
          </div>
        </button>
      </div>

      <div role="tablist" aria-label="侧栏内容" className="mx-4 mt-4 flex rounded-xl border border-slate-800 bg-slate-950/60 p-1">
        {(['conversations', 'roles'] as const).map(value => <button key={value} type="button" role="tab"
          id={`${tabsId}-${value}`} aria-controls={`${tabsId}-panel`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1}
          aria-label={value === 'conversations' ? '会话' : '角色'}
          onClick={() => selectTab(value)} onKeyDown={event => {
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
              event.preventDefault()
              const next = event.key === 'Home' ? 'conversations' : event.key === 'End' ? 'roles' : value === 'roles' ? 'conversations' : 'roles'
              selectTab(next)
              document.getElementById(`${tabsId}-${next}`)?.focus()
            }
          }}
          className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 ${tab === value ? value === 'roles' ? 'bg-emerald-100 text-emerald-700' : 'bg-indigo-100 text-indigo-700' : 'text-slate-500 hover:text-slate-300'}`}>
          {value === 'conversations' ? <MessageCircle size={14} /> : <Bot size={14} />}
          {value === 'conversations' ? '会话' : '角色'}
          <span className="text-[10px] text-slate-500">{value === 'conversations' ? conversations.length : roles.length}</span>
        </button>)}
      </div>
      <div className="px-4 py-3">
        <div className="flex items-center gap-2 rounded-xl bg-slate-950/80 px-3 py-2 text-slate-500 border border-slate-800/80 focus-within:border-indigo-500/50">
          <Search size={15} className="shrink-0" />
          <input value={queries[tab]} onChange={event => {
            setQueries(current => ({ ...current, [tab]: event.target.value }))
            positions.current[tab] = 0
            if (listRef.current) listRef.current.scrollTop = 0
          }} aria-label={tab === 'conversations' ? '搜索会话' : '搜索角色'}
            className="min-w-0 w-full bg-transparent text-sm text-slate-300 outline-none placeholder:text-slate-600"
            placeholder={tab === 'conversations' ? '搜索会话' : '搜索角色名称、标签或描述'} />
        </div>
      </div>

      <div ref={listRef} role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`} tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto px-3 pb-3 space-y-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
      {tab === 'conversations' ? <>

        {filteredConversations.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-slate-600">
            {searchTerm ? '未找到相关会话' : '还没有会话'}
            <br />
            <span className="text-xs text-slate-700 mt-1 block">创建角色并开启第一次对话</span>
          </div>
        ) : (
          filteredConversations.map((conversation, index) => {
            const isActive = activeConversationId === conversation.id
            return (
              <div key={conversation.id}>
              {(index === 0 || filteredConversations[index - 1].pinned !== conversation.pinned) && <p className="px-3 pt-3 pb-1 text-[10px] text-slate-500">{conversation.pinned ? '置顶' : '最近会话'}</p>}
              <div
                className={`group relative mb-1 flex w-full items-center justify-between rounded-xl px-3 py-2 transition-all cursor-pointer ${
                  isActive 
                    ? 'bg-indigo-600/15 border border-indigo-500/20 text-white' 
                    : 'hover:bg-slate-800/50 border border-transparent hover:border-slate-800'
                }`}
              >
                <button type="button" title={conversation.title} aria-label={`打开会话：${conversation.title}`}
                  onClick={() => navigateToConversation(conversation.id)}
                  className="flex items-center gap-3 min-w-0 flex-1 text-left rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                  <div className={`rounded-lg p-2 shrink-0 ${isActive ? 'bg-indigo-600/20 text-indigo-300' : 'bg-slate-950 border border-slate-800'}`}>
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
                </button>

                {/* 会话快捷控制操作（悬浮显示） */}
                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity bg-slate-900 shadow-md rounded-lg p-1 border border-slate-800">
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
              </div></div>
            )
          })
        )}
      </> : <>
        {filteredRoles.map(role => <div key={role.id} data-testid="sidebar-role" className="group relative rounded-xl border border-transparent hover:border-slate-800 hover:bg-slate-800/40">
          <button type="button" title={role.name} onClick={() => onOpenRoleModal(role)}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 pr-10 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-emerald-500/20 bg-emerald-100 text-emerald-700">
              {role.avatar && !failedAvatars.includes(role.id) ? <img src={role.avatar} alt="" onError={() => setFailedAvatars(current => [...current, role.id])} className="h-7 w-7 rounded-lg object-cover" /> : <Bot size={16} />}
            </span>
            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-200">{role.name}</span>
              <span className="mt-0.5 block truncate text-[11px] text-slate-500">{role.description || role.tags.join(' · ') || '未填写角色描述'}</span>
            </span>
          </button>
          <button type="button" aria-label={`更多角色操作：${role.name}`} aria-expanded={roleMenu === role.id}
            onClick={() => setRoleMenu(current => current === role.id ? null : role.id)}
            className="absolute right-2 top-3 rounded p-1.5 text-slate-500 hover:bg-slate-800 hover:text-white"><MoreHorizontal size={15} /></button>
          {roleMenu === role.id && <div className="mx-3 mb-2 rounded-lg border border-slate-700 bg-slate-950 p-1">
            <button type="button" onClick={() => { setRoleMenu(null); onOpenRoleModal(role) }} className="w-full rounded px-3 py-2 text-left text-xs hover:bg-slate-800">编辑角色</button>
          </div>}
        </div>)}
        {!filteredRoles.length && <p className="px-3 py-10 text-center text-sm text-slate-500">{queries.roles ? '未找到相关角色' : '还没有创建角色'}</p>}
      </>}
      </div>
      <div className="border-t border-slate-800 px-4 py-2">
        <button type="button" onClick={onOpenRecycleBin} title="回收站" className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-xs text-slate-500 hover:bg-slate-800/50 hover:text-slate-300">
          <Trash2 size={14} />回收站
        </button>
      </div>

      {/* 用户状态栏 */}
      <div className="border-t border-slate-800 p-4 flex items-center justify-between bg-slate-950/60">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 font-bold shrink-0">
            {user?.nickname?.[0]?.toUpperCase() || 'O'}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-200 truncate">@{user?.username}</p>
            <p className="text-xs text-slate-500 truncate">{user?.is_owner ? '工作台 Owner' : '工作台 Guest'}</p>
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
            onClick={() => onOpenSettings('models')}
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
