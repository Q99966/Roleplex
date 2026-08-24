import { useEffect, useState } from 'react'
import { PanelLeftOpen } from 'lucide-react'
import { useAppStore } from './store/app'
import { type Role } from './api/client'
import { AuthScreen } from './components/AuthScreen'
import { PasswordResetScreen } from './components/PasswordResetScreen'
import { RecycleBinModal } from './components/RecycleBinModal'
import { Sidebar } from './components/Sidebar'
import { EmptyWorkspace } from './components/EmptyWorkspace'
import { ActiveWorkspace } from './components/ActiveWorkspace'
import { SettingsModal, RoleModal, ConversationModal } from './components/Modals'
import { LandingPage } from './components/LandingPage'
import { WebPet } from './components/WebPet/WebPet'

// 设置网页标题
if (typeof document !== 'undefined') {
  document.title = 'Roleplex'
}

/** 启动已认证的工作台；没有会话时显示空状态。 */
export function App() {
  const { user, passwordResetRequired, loading, bootstrap, activeConversationId, setActiveConversation } = useAppStore()
  
  const [view, setView] = useState<'landing' | 'auth'>('landing')
  const [currentHash, setCurrentHash] = useState(typeof window !== 'undefined' ? window.location.hash || '#/' : '#/')
  const [showSettings, setShowSettings] = useState(false)
  const [showRoleModal, setShowRoleModal] = useState(false)
  const [roleToEdit, setRoleToEdit] = useState<Role | null>(null)
  const [showConvModal, setShowConvModal] = useState(false)
  const [showRecycleBin, setShowRecycleBin] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)

  // 页面初始化加载状态
  useEffect(() => { 
    void bootstrap() 
  }, [bootstrap])

  // 监听 Hash 变化并同步更新当前状态
  useEffect(() => {
    const handleHashChange = () => {
      setCurrentHash(window.location.hash || '#/')
    }
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  // 路由跳转与状态双向同步核心控制器
  useEffect(() => {
    if (loading) return

    const path = currentHash.replace(/^#/, '') || '/'

    if (!user) {
      // 未登录用户的路由守卫
      if (path !== '/' && path !== '/auth') {
        window.location.hash = '#/'
      } else {
        setView(path === '/auth' ? 'auth' : 'landing')
      }
    } else {
      // 已登录用户的路由守卫
      setView('auth')
      if (path === '/' || path === '/auth') {
        window.location.hash = '#/workspace'
      } else if (path.startsWith('/workspace/conversation/')) {
        const idStr = path.split('/').pop()
        const convId = idStr ? parseInt(idStr, 10) : NaN
        if (!isNaN(convId)) {
          if (activeConversationId !== convId) {
            setActiveConversation(convId)
          }
        } else {
          window.location.hash = '#/workspace'
        }
      } else if (path === '/workspace') {
        if (activeConversationId !== null) {
          setActiveConversation(null)
        }
      } else {
        window.location.hash = '#/workspace'
      }
    }
  }, [currentHash, user, loading, activeConversationId, setActiveConversation])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-white font-medium text-lg">
        <div className="flex flex-col items-center gap-4">
          <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-b-2 border-indigo-500"></div>
          <span className="text-slate-300 text-sm tracking-wide">正在加载 Roleplex…</span>
        </div>
      </div>
    )
  }

  if (view === 'landing' && !user) {
    return <LandingPage onEnterWorkbench={() => { window.location.hash = '#/auth' }} />
  }

  if (!user) return <AuthScreen />

  // 待改密账号只能看到重置页：服务端已拒绝其余接口，工作台在此也不予渲染。
  if (passwordResetRequired) return <PasswordResetScreen />

  return (
    <main className="flex h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans select-none relative font-sans">
      <Sidebar
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed(true)}
        onOpenSettings={() => setShowSettings(true)}
        onOpenRoleModal={(role?: Role) => {
          setRoleToEdit(role || null)
          setShowRoleModal(true)
        }}
        onOpenConvModal={() => setShowConvModal(true)}
        onOpenRecycleBin={() => setShowRecycleBin(true)}
      />
      {activeConversationId ? (
        <ActiveWorkspace 
          isSidebarCollapsed={isSidebarCollapsed}
          onOpenRoleModal={(role?: Role) => {
            setRoleToEdit(role || null)
            setShowRoleModal(true)
          }}
        />
      ) : (
        <EmptyWorkspace 
          onOpenSettings={() => setShowSettings(true)}
          onOpenRoleModal={() => {
            setRoleToEdit(null)
            setShowRoleModal(true)
          }}
          onOpenConvModal={() => setShowConvModal(true)}
        />
      )}
      
      {/* 展开按钮（当侧边栏收起时在最左侧显示） */}
      {isSidebarCollapsed && (
        <button
          type="button"
          onClick={() => setIsSidebarCollapsed(false)}
          title="展开侧边栏"
          className="absolute top-4 left-4 z-40 rounded-lg p-2 bg-slate-900 border border-slate-800 text-slate-400 hover:text-white hover:bg-slate-800 hover:border-slate-700 transition shadow-lg shadow-black/60"
        >
          <PanelLeftOpen size={18} />
        </button>
      )}
      
      {/* 模态浮层 */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      
      {showRoleModal && (
        <RoleModal 
          role={roleToEdit}
          onOpenSettings={() => setShowSettings(true)}
          onClose={() => {
            setShowRoleModal(false)
            setRoleToEdit(null)
          }}
        />
      )}
      
      {showConvModal && <ConversationModal onClose={() => setShowConvModal(false)} />}

      {showRecycleBin && <RecycleBinModal onClose={() => setShowRecycleBin(false)} />}
      
      {/* 桌面宠物悬浮层 */}
      <WebPet />
    </main>
  )
}

