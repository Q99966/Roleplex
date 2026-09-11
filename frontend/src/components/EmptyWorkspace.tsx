import { 
  Bot, Settings2, Users, WandSparkles, ArrowRight, Plus 
} from 'lucide-react'
import { useAppStore } from '../store/app'

interface EmptyWorkspaceProps {
  onOpenSettings: (initialTab?: 'models' | 'worlds' | 'account') => void
  onOpenRoleModal: () => void
  onOpenConvModal: () => void
}

/** 渲染用户创建会话前的初始工作台状态（仪表中控版）。 */
export function EmptyWorkspace({ onOpenSettings, onOpenRoleModal, onOpenConvModal }: EmptyWorkspaceProps) {
  const { user, roles, conversations, modelConfigs } = useAppStore()
  const logout = useAppStore((state) => state.logout)

  return (
    <section className="flex flex-1 flex-col items-center justify-center bg-slate-950 px-6 py-12 overflow-y-auto relative">
      {/* 背景微光 */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] rounded-full bg-indigo-500/[0.03] blur-[150px] pointer-events-none"></div>

      <div className="mb-6 rounded-3xl bg-slate-900 border border-slate-800/80 p-6 text-indigo-400 shadow-xl relative z-10 animate-bounce" style={{ animationDuration: '4s' }}>
        <WandSparkles size={36} className="drop-shadow-[0_0_15px_rgba(245,158,11,0.4)]" />
      </div>

      <h2 className="text-3xl font-extrabold text-white tracking-tight relative z-10">欢迎来到 Roleplex</h2>
      <p className="mt-3 max-w-md text-center text-slate-400 text-sm leading-relaxed relative z-10">
        Roleplex 是一款个人多 Agent 群聊协作平台。创建你的专属角色，为其配置模型密钥、提示词，即可开启单聊或拉起多角色群聊。
      </p>

      {/* 快捷引导板块 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl w-full mt-10 relative z-10">
        <div 
          onClick={() => onOpenSettings('models')}
          className="bg-slate-900/50 border border-slate-850 p-5 rounded-2xl cursor-pointer hover:bg-slate-900 hover:border-indigo-500/30 transition-all group"
        >
          <div className="flex items-center justify-between mb-3 text-indigo-400">
            <Settings2 size={20} />
            <span className="text-xs bg-slate-950 border border-slate-800 px-2 py-0.5 rounded-full text-slate-500">第一步</span>
          </div>
          <h3 className="font-semibold text-slate-200 group-hover:text-white transition">大模型密钥配置</h3>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">配置您的 API Key 与端点，以驱动 Agent 思考与调用工具。</p>
          <div className="text-xs text-indigo-400 font-medium flex items-center gap-1 mt-4">
            <span>当前配置数: {modelConfigs?.length || 0}</span>
            <ArrowRight size={12} className="group-hover:translate-x-1 transition-transform" />
          </div>
        </div>

        <div 
          onClick={onOpenRoleModal}
          className="bg-slate-900/50 border border-slate-850 p-5 rounded-2xl cursor-pointer hover:bg-slate-900 hover:border-indigo-500/30 transition-all group"
        >
          <div className="flex items-center justify-between mb-3 text-indigo-400">
            <Bot size={20} />
            <span className="text-xs bg-slate-950 border border-slate-800 px-2 py-0.5 rounded-full text-slate-500">第二步</span>
          </div>
          <h3 className="font-semibold text-slate-200 group-hover:text-white transition">定制 Agent 角色</h3>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">设置名称、头像、系统 Prompt，定制专属大模型代理人角色。</p>
          <div className="text-xs text-indigo-400 font-medium flex items-center gap-1 mt-4">
            <span>已创建角色: {roles?.length || 0}</span>
            <ArrowRight size={12} className="group-hover:translate-x-1 transition-transform" />
          </div>
        </div>

        <div 
          onClick={onOpenConvModal}
          className="bg-slate-900/50 border border-slate-850 p-5 rounded-2xl cursor-pointer hover:bg-slate-900 hover:border-indigo-500/30 transition-all group"
        >
          <div className="flex items-center justify-between mb-3 text-indigo-400">
            <Users size={20} />
            <span className="text-xs bg-slate-950 border border-slate-800 px-2 py-0.5 rounded-full text-slate-500">第三步</span>
          </div>
          <h3 className="font-semibold text-slate-200 group-hover:text-white transition">拉起多角色群聊</h3>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">支持单聊与多角色群聊，可选配 Orchestrator 智能调度发言。</p>
          <div className="text-xs text-indigo-400 font-medium flex items-center gap-1 mt-4">
            <span>当前会话数: {conversations?.length || 0}</span>
            <ArrowRight size={12} className="group-hover:translate-x-1 transition-transform" />
          </div>
        </div>
      </div>

      <div className="mt-10 flex gap-3 relative z-10">
        <button 
          type="button" 
          onClick={onOpenRoleModal}
          className="flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-3 font-semibold text-white hover:bg-indigo-500 transition shadow-lg shadow-indigo-600/20 active:scale-[0.98]"
        >
          <Plus size={17} />
          <span>创建角色</span>
        </button>
        <button 
          type="button" 
          onClick={() => onOpenSettings('models')}
          className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900 px-6 py-3 font-semibold text-slate-300 hover:bg-slate-800 hover:text-white transition active:scale-[0.98]"
        >
          <Settings2 size={17} />
          <span>打开设置</span>
        </button>
      </div>

      <div className="mt-12 flex items-center gap-3 text-sm text-slate-500 relative z-10 border-t border-slate-900 pt-6 w-full max-w-md justify-center">
        <span className="text-slate-400">{user?.nickname}</span>
        <span>·</span>
        <button type="button" onClick={logout} className="hover:text-indigo-400 transition">退出登录</button>
      </div>
    </section>
  )
}
