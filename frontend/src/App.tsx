import { useEffect, useState } from 'react'
import { PanelLeftOpen } from 'lucide-react'
import { useAppStore } from './store/app'
import { type Role } from './api/client'
import { AuthScreen } from './components/AuthScreen'
import { Sidebar } from './components/Sidebar'
import { EmptyWorkspace } from './components/EmptyWorkspace'
import { ActiveWorkspace } from './components/ActiveWorkspace'
import { SettingsModal, RoleModal, ConversationModal } from './components/Modals'

// 设置网页标题
if (typeof document !== 'undefined') {
  document.title = 'Roleplex'
}

/** 启动已认证的工作台；没有会话时显示空状态。 */
export function App() {
  const { user, loading, bootstrap, activeConversationId } = useAppStore()
  
  const [showSettings, setShowSettings] = useState(false)
  const [showRoleModal, setShowRoleModal] = useState(false)
  const [roleToEdit, setRoleToEdit] = useState<Role | null>(null)
  const [showConvModal, setShowConvModal] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)

  useEffect(() => { 
    void bootstrap() 
  }, [bootstrap])

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

  if (!user) return <AuthScreen />

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
    </main>
  )
}
