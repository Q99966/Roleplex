import { type FormEvent, useEffect, useMemo, useState } from 'react'
import {
  Settings2, X, Shield, Cpu, Trash2, Plus, Check, AlertCircle, Bot, Users, Globe2, ChevronDown, Database, Server, FolderKanban,
} from 'lucide-react'
import { useAppStore } from '../store/app'
import { type Conversation, type Role } from '../api/client'
import { WorkspaceSettingsPanel } from './WorkspaceSettingsPanel'

export interface ModalProps {
  onClose: () => void
}

export interface SettingsModalProps {
  onClose: () => void
  initialTab?: 'models' | 'worlds' | 'workspaces' | 'account'
}

/** 综合系统与环境配置管理弹窗 (SettingsModal)。 */
export function SettingsModal({ onClose, initialTab = 'models' }: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<'models' | 'worlds' | 'workspaces' | 'account'>(initialTab)
  const { 
    modelConfigs, createModelConfig, deleteModelConfig,
    worldName, worlds, worldSwitchingSupported, switchingWorld, switchWorld,
    user, logout
  } = useAppStore()

  // 大模型表单状态
  const [form, setForm] = useState({ name: '', provider_type: 'openai_compatible' as const, base_url: '', api_key: '' })
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrorMsg(null)
    try {
      await createModelConfig({
        name: form.name,
        provider_type: form.provider_type,
        base_url: form.base_url || null,
        api_key: form.api_key
      })
      setForm({ name: '', provider_type: 'openai_compatible', base_url: '', api_key: '' })
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '密钥配置添加失败')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('确定要删除大模型密钥配置吗？如果有关联角色使用此配置，将会删除失败。')) return
    setErrorMsg(null)
    try {
      await deleteModelConfig(id)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '删除失败，配置可能正在被其他角色引用')
    }
  }

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-3xl overflow-hidden flex flex-col max-h-[88vh] shadow-2xl"
      >
        {/* 标题栏 */}
        <div className="px-6 py-4 border-b border-slate-800 bg-slate-900/80 flex items-center justify-between">
          <div className="flex items-center gap-2 text-indigo-400">
            <Settings2 size={18} />
            <h3 id="settings-title" className="font-bold text-white text-base">系统与环境设置</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭系统与环境设置"
            className="p-1 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition"
          >
            <X size={18} />
          </button>
        </div>

        {/* 选项卡导航 */}
        <div
          role="tablist"
          aria-label="设置分类"
          className="flex shrink-0 gap-2 overflow-x-auto border-b border-slate-800 bg-slate-950/40 px-6 pt-2"
        >
          <button
            type="button"
            id="settings-tab-models"
            role="tab"
            aria-selected={activeTab === 'models'}
            aria-controls="settings-panel-models"
            onClick={() => setActiveTab('models')}
            className={`flex shrink-0 items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all ${
              activeTab === 'models'
                ? 'border-indigo-500 text-indigo-400 bg-slate-900/80 rounded-t-xl'
                : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900/40 rounded-t-xl'
            }`}
          >
            <Cpu size={14} />
            <span>大模型密钥</span>
            <span className="rounded-full bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">{modelConfigs.length}</span>
          </button>

          <button
            type="button"
            id="settings-tab-worlds"
            role="tab"
            aria-selected={activeTab === 'worlds'}
            aria-controls="settings-panel-worlds"
            onClick={() => setActiveTab('worlds')}
            className={`flex shrink-0 items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all ${
              activeTab === 'worlds'
                ? 'border-indigo-500 text-indigo-400 bg-slate-900/80 rounded-t-xl'
                : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900/40 rounded-t-xl'
            }`}
          >
            <Globe2 size={14} />
            <span>运行世界与存储</span>
            {worldSwitchingSupported ? (
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" />
            ) : (
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">单世界</span>
            )}
          </button>

          {user?.is_owner && (
            <button
              type="button"
              id="settings-tab-workspaces"
              role="tab"
              aria-selected={activeTab === 'workspaces'}
              aria-controls="settings-panel-workspaces"
              onClick={() => setActiveTab('workspaces')}
              className={`flex shrink-0 items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all ${
                activeTab === 'workspaces'
                  ? 'border-indigo-500 text-indigo-400 bg-slate-900/80 rounded-t-xl'
                  : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900/40 rounded-t-xl'
              }`}
            >
              <FolderKanban size={14} />
              <span>工作区</span>
            </button>
          )}

          <button
            type="button"
            id="settings-tab-account"
            role="tab"
            aria-selected={activeTab === 'account'}
            aria-controls="settings-panel-account"
            onClick={() => setActiveTab('account')}
            className={`flex shrink-0 items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all ${
              activeTab === 'account'
                ? 'border-indigo-500 text-indigo-400 bg-slate-900/80 rounded-t-xl'
                : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900/40 rounded-t-xl'
            }`}
          >
            <Shield size={14} />
            <span>账号与安全</span>
            <span className="rounded bg-indigo-950/80 border border-indigo-800/40 px-1.5 py-0.5 text-[10px] text-indigo-300">
              {user?.is_owner ? 'Owner' : 'Guest'}
            </span>
          </button>
        </div>

        {/* 选项卡内容区 */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === 'models' && (
            <div
              id="settings-panel-models"
              role="tabpanel"
              aria-labelledby="settings-tab-models"
              className="grid grid-cols-1 md:grid-cols-2 gap-6"
            >
              {/* 左侧：已保存密钥 */}
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3 flex items-center gap-1.5">
                  <Shield size={14} />
                  <span>当前已配置密钥 ({modelConfigs.length})</span>
                </h4>
                
                <div className="space-y-2.5 max-h-[48vh] overflow-y-auto pr-1">
                  {modelConfigs.map((config) => (
                    <div key={config.id} className="bg-slate-950/60 border border-slate-800 rounded-xl p-3.5 flex items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <Cpu size={13} className="text-indigo-400" />
                          <span className="text-xs font-bold text-slate-200 truncate">{config.name}</span>
                        </div>
                        <p className="text-[10px] text-indigo-400 mt-1">{config.provider_type === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容'}</p>
                        <p className="text-[10px] text-slate-500 truncate mt-1">API Endpoint: {config.base_url || '默认端点'}</p>
                        <p className="text-[10px] text-slate-500 mt-1">
                          API Key: <code className="bg-slate-900 px-1 py-0.5 rounded border border-slate-800">{config.api_key_hint}</code>
                        </p>
                      </div>
                      
                      <button 
                        onClick={() => void handleDelete(config.id)}
                        className="p-1.5 text-slate-500 hover:text-red-400 hover:bg-red-950/40 rounded transition shrink-0 ml-2"
                        title="删除配置"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                  
                  {modelConfigs.length === 0 && (
                    <div className="text-center py-8 border border-dashed border-slate-800 rounded-2xl text-xs text-slate-600 bg-slate-950/10">
                      暂无已保存密钥，请使用右侧表单添加。
                    </div>
                  )}
                </div>
              </div>

              {/* 右侧：添加密钥表单 */}
              <div className="border-t md:border-t-0 md:border-l border-slate-800 pt-6 md:pt-0 md:pl-6">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3 flex items-center gap-1.5">
                  <Plus size={14} />
                  <span>添加大模型厂商密钥</span>
                </h4>
                
                <form onSubmit={handleSubmit} className="space-y-3.5 text-xs">
                  <label className="block">
                    <span className="text-slate-400 font-medium">配置别名 (用于标识)</span>
                    <input 
                      required 
                      value={form.name} 
                      onChange={e => setForm({...form, name: e.target.value})}
                      placeholder="例如: DeepSeek / Claude-API" 
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
                    />
                  </label>

                  <label className="block">
                    <span className="text-slate-400 font-medium">厂商类型</span>
                    <select 
                      value={form.provider_type}
                      onChange={e => setForm({...form, provider_type: e.target.value as any})}
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none"
                    >
                      <option value="openai_compatible">OpenAI 兼容接口 (Deepseek, Qwen 等)</option>
                      <option value="anthropic">Anthropic Claude</option>
                    </select>
                  </label>

                  <label className="block">
                    <span className="text-slate-400 font-medium">API 代理端点 Base URL (可选)</span>
                    <input 
                      value={form.base_url} 
                      onChange={e => setForm({...form, base_url: e.target.value})}
                      placeholder="例如: https://api.deepseek.com/v1" 
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
                    />
                  </label>

                  <label className="block">
                    <span className="text-slate-400 font-medium">API 密钥 (API Key)</span>
                    <input 
                      required
                      type="password"
                      value={form.api_key} 
                      onChange={e => setForm({...form, api_key: e.target.value})}
                      placeholder="sk-••••••••••••••••••••••••" 
                      className="mt-1 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
                    />
                  </label>

                  {errorMsg && (
                    <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl text-[11px] flex items-start gap-1.5">
                      <AlertCircle size={14} className="shrink-0 mt-0.5" />
                      <span>{errorMsg}</span>
                    </div>
                  )}

                  <button 
                    type="submit" 
                    disabled={busy}
                    className="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold flex items-center justify-center gap-1.5 shadow-lg shadow-indigo-600/20 active:scale-[0.98] transition-all disabled:opacity-50"
                  >
                    <Check size={14} />
                    {busy ? '正在校验并保存...' : '添加配置并加密存储'}
                  </button>
                </form>
              </div>
            </div>
          )}

          {activeTab === 'worlds' && (
            <div
              id="settings-panel-worlds"
              role="tabpanel"
              aria-labelledby="settings-tab-worlds"
              className="space-y-6 text-xs"
            >
              {/* 当前运行状态卡片 */}
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-950/80 border border-indigo-500/30 text-indigo-400">
                      <Globe2 size={20} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-base font-bold text-white tracking-tight">{worldName}</span>
                        <span className="rounded-full bg-indigo-950 border border-indigo-800/40 px-2 py-0.5 text-[10px] font-medium text-indigo-300">
                          当前物理世界
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-500 mt-0.5">
                        独立的 SQLite 数据存储、JWT 鉴权密钥与加密 API Key 存储目录
                      </p>
                    </div>
                  </div>

                  <div>
                    {worldSwitchingSupported ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-950/80 border border-emerald-800/60 px-3 py-1 text-[11px] font-medium text-emerald-400">
                        <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse motion-reduce:animate-none" />
                        包装器已接管 · 支持热切换
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-900 border border-slate-800 px-3 py-1 text-[11px] font-medium text-slate-400">
                        单世界模式 · 未使用包装器
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* 切换世界操作区 */}
              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
                <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-1 flex items-center gap-1.5">
                  <Server size={14} className="text-indigo-400" />
                  <span>切换运行世界</span>
                </h4>
                <p className="text-[11px] text-slate-500 mb-3">
                  切换目标世界将由世界包装器平滑重启后端服务；由于每个世界具有完全独立的用户与凭据数据库，切换后需重新认证登录。
                </p>

                <div className="relative max-w-md">
                  <select
                    aria-label="切换世界"
                    value={worldName}
                    disabled={!user?.is_owner || !worldSwitchingSupported || Boolean(switchingWorld)}
                    onChange={(event) => {
                      const target = event.target.value
                      if (target !== worldName && confirm(`切换到世界“${target}”并重新登录吗？`)) {
                        void switchWorld(target)
                      }
                    }}
                    className="w-full appearance-none rounded-xl border border-slate-800 bg-slate-900 py-2.5 pl-3.5 pr-10 text-xs font-semibold text-slate-100 shadow-sm transition-all hover:border-slate-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 outline-none cursor-pointer disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {!worlds.some((world) => world.name === worldName) && (
                      <option value={worldName} className="bg-slate-900 py-1.5 text-slate-200">
                        {worldName} (当前)
                      </option>
                    )}
                    {worlds.map((world) => (
                      <option key={world.name} value={world.name} className="bg-slate-900 py-1.5 text-slate-200">
                        {world.name} {world.name === worldName ? '· 当前运行' : ''}
                      </option>
                    ))}
                  </select>
                  <div className="pointer-events-none absolute right-3 top-3 flex items-center text-slate-400">
                    <ChevronDown size={14} />
                  </div>
                </div>

                {!worldSwitchingSupported && (
                  <p className="mt-2 text-[11px] text-amber-400/90 flex items-center gap-1">
                    <AlertCircle size={13} className="shrink-0" />
                    <span>需使用世界包装器启动以启用切换功能（运行 python scripts/run_world_server.py）</span>
                  </p>
                )}
              </div>

              {/* 已发现物理世界列表 */}
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2 flex items-center gap-1.5">
                  <Database size={14} />
                  <span>已发现的物理世界存档 ({worlds.length})</span>
                </h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {worlds.map((world) => {
                    const isCurrent = world.name === worldName
                    return (
                      <div
                        key={world.name}
                        className={`rounded-xl border p-3 flex items-center justify-between transition-all ${
                          isCurrent
                            ? 'border-indigo-500/50 bg-indigo-950/20 shadow-sm'
                            : 'border-slate-850 bg-slate-950/60'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Globe2 size={15} className={isCurrent ? 'text-indigo-400' : 'text-slate-500'} />
                          <span className={`text-xs font-bold truncate ${isCurrent ? 'text-white' : 'text-slate-300'}`}>
                            {world.name}
                          </span>
                        </div>
                        {isCurrent ? (
                          <span className="rounded-full bg-indigo-600/30 border border-indigo-500/40 px-2 py-0.5 text-[9px] font-semibold text-indigo-300">
                            当前运行中
                          </span>
                        ) : (
                          <span className="text-[10px] text-slate-500">可切换</span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'account' && (
            <div
              id="settings-panel-account"
              role="tabpanel"
              aria-labelledby="settings-tab-account"
              className="space-y-6 text-xs"
            >
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-indigo-600 text-white font-bold text-base shadow-lg shadow-indigo-600/30">
                    {user?.nickname?.[0]?.toUpperCase() || user?.username?.[0]?.toUpperCase() || 'U'}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-white">{user?.nickname || user?.username}</span>
                      <span className="text-xs text-slate-500">@{user?.username}</span>
                    </div>
                    <p className="text-[11px] text-indigo-400 mt-0.5">
                      {user?.is_owner ? '系统超级管理员（Owner）' : '受限访客用户（Guest）'}
                    </p>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => { onClose(); logout(); }}
                  className="rounded-xl border border-red-900/40 bg-red-950/40 px-3 py-2 text-xs font-medium text-red-400 hover:bg-red-900/60 hover:text-white transition"
                >
                  退出登录
                </button>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 space-y-2.5">
                <h4 className="text-xs font-bold text-slate-300 flex items-center gap-1.5">
                  <Shield size={14} className="text-indigo-400" />
                  <span>权限与安全边界说明</span>
                </h4>
                <ul className="text-slate-400 text-[11px] space-y-1.5 list-disc list-inside">
                  <li><strong>Owner 专属权限</strong>：只有世界 Owner 能够配置大模型 API 密钥、创建与修改 Agent 角色、切换物理世界。</li>
                  <li><strong>密钥隔离机制</strong>：API Key 在落库时使用当前世界独立的加密密钥对称加密，前端仅能读取脱敏标识（如 <code>sk-...ab12</code>）。</li>
                  <li><strong>单世界数据隔离</strong>：所有会话、角色与模型配置均只保存在当前运行的物理世界数据库中。</li>
                </ul>
              </div>
            </div>
          )}

          {activeTab === 'workspaces' && user?.is_owner && (
            <div
              id="settings-panel-workspaces"
              role="tabpanel"
              aria-labelledby="settings-tab-workspaces"
            >
              <WorkspaceSettingsPanel />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

interface RoleModalProps {
  role?: Role | null
  onClose: () => void
  onOpenSettings?: () => void
}

/** 创建/编辑 Agent 角色对话框 (RoleModal)。 */
export function RoleModal({ role, onClose, onOpenSettings }: RoleModalProps) {
  const { modelConfigs, createRole, updateRole, deleteRole } = useAppStore()
  
  const [form, setForm] = useState({
    name: '',
    avatar: '',
    description: '',
    tags: '',
    system_prompt: '',
    model_config_id: 0,
    model_name: '',
    context_window_tokens: 200_000,
    params: '{}',
    builtin_tools: [] as string[]
  })
  
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const maxOutputTokens = useMemo(() => {
    try {
      const value = (JSON.parse(form.params || '{}') as { max_tokens?: unknown }).max_tokens
      return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 1024
    } catch {
      return 1024
    }
  }, [form.params])
  const contextCeiling = role?.context_window_ceiling_tokens ?? 2_000_000
  const effectiveContextWindow = Math.min(form.context_window_tokens, contextCeiling)

  useEffect(() => {
    if (role) {
      setForm({
        name: role.name,
        avatar: role.avatar || '',
        description: role.description || '',
        tags: role.tags.join(', '),
        system_prompt: role.system_prompt,
        // 墓碑角色不会进入编辑弹窗（列表已过滤），这里回退到 0 只是为了让
        // 表单状态保持非空数字，提交前仍会校验必须选中一个模型配置。
        model_config_id: role.model_config_id ?? 0,
        model_name: role.model_name,
        context_window_tokens: role.context_window_tokens,
        params: JSON.stringify(role.params || {}, null, 2),
        builtin_tools: role.builtin_tools || []
      })
    } else if (modelConfigs.length > 0) {
      setForm(f => ({ ...f, model_config_id: modelConfigs[0].id }))
    }
  }, [role, modelConfigs])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrorMsg(null)

    const parsedTags = form.tags
      ? form.tags.split(/[,，]/).map(t => t.trim()).filter(Boolean)
      : []

    let parsedParams = {}
    try {
      parsedParams = JSON.parse(form.params || '{}')
    } catch {
      setErrorMsg('运行参数配置格式无效，必须为合法的 JSON 对象')
      setBusy(false)
      return
    }

    if (!form.model_config_id) {
      setErrorMsg('请先选择一个关联的模型密钥配置。如果没有，请先去设置页面新建')
      setBusy(false)
      return
    }

    const submittedMaxOutput = (parsedParams as { max_tokens?: unknown }).max_tokens
    if (typeof submittedMaxOutput === 'number' && submittedMaxOutput >= form.context_window_tokens) {
      setErrorMsg('模型 max_tokens 必须小于上下文窗口')
      setBusy(false)
      return
    }

    if (form.avatar) {
      const trimmed = form.avatar.trim()
      const isLocalPath = /^[a-zA-Z]:\\/i.test(trimmed) || 
                          trimmed.startsWith('/') || 
                          trimmed.includes('Desktop') || 
                          trimmed.includes('Users') ||
                          (/\.(png|jpg|jpeg|gif|webp|svg)$/i.test(trimmed) && !/^https?:\/\//i.test(trimmed))
      if (isLocalPath) {
        setErrorMsg('暂不支持使用本地磁盘路径作为头像，请使用以 http:// 或 https:// 开头的网络图片链接，或留空使用默认图标。')
        setBusy(false)
        return
      }
    }

    const payload = {
      name: form.name,
      avatar: form.avatar || null,
      description: form.description || null,
      tags: parsedTags,
      system_prompt: form.system_prompt,
      model_config_id: form.model_config_id,
      model_name: form.model_name || 'gpt-4o',
      context_window_tokens: form.context_window_tokens,
      params: parsedParams,
      skills: [],
      builtin_tools: form.builtin_tools,
      mcp_servers: []
    }

    try {
      if (role) {
        await updateRole(role.id, payload)
      } else {
        await createRole(payload)
      }
      onClose()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '角色存储失败，请检查参数')
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    if (!role) return
    if (!confirm(`确定要彻底删除角色 Agent "${role.name}" 吗？此操作无法撤销。`)) return
    setBusy(true)
    setErrorMsg(null)
    try {
      await deleteRole(role.id)
      onClose()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '删除角色失败')
      setBusy(false)
    }
  }

  const toggleTool = (tool: string) => {
    setForm(f => {
      const isExist = f.builtin_tools.includes(tool)
      const nextTools = isExist ? f.builtin_tools.filter(t => t !== tool) : [...f.builtin_tools, tool]
      return { ...f, builtin_tools: nextTools }
    })
  }

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh] shadow-2xl relative z-10">
        
        {/* 头部 */}
        <div className="px-6 py-5 border-b border-slate-800 flex items-center justify-between bg-slate-900/60">
          <div className="flex items-center gap-2 text-indigo-400">
            <Bot size={18} />
            <h3 className="font-bold text-white text-base">
              {role ? `定制 Agent 属性 · ${role.name}` : '创建并定制 Agent 角色'}
            </h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition">
            <X size={18} />
          </button>
        </div>

        {/* 表单体 */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
          
          {modelConfigs.length === 0 && (
            <div className="p-3.5 bg-red-500/10 border border-red-500/20 text-red-400 rounded-2xl flex flex-col gap-2">
              <div className="flex items-center gap-1.5">
                <AlertCircle size={16} />
                <span className="font-semibold">⚠️ 缺少可用的大模型密钥配置</span>
              </div>
              <p className="text-[11px] text-red-400/80 leading-relaxed">
                创建 Agent 必须绑定一个模型配置以获取大模型访问权。请先在工作台主页或打开设置添加您的 API Key 之后再来定制角色。
                {onOpenSettings && (
                  <button 
                    type="button" 
                    onClick={() => { onClose(); onOpenSettings() }} 
                    className="text-indigo-400 font-bold ml-1 hover:underline"
                  >
                    去添加配置 &raquo;
                  </button>
                )}
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label className="block">
              <span className="text-slate-400 font-medium">Agent 角色名称 (必填)</span>
              <input 
                required 
                value={form.name} 
                onChange={e => setForm({...form, name: e.target.value})}
                placeholder="例如: CodeHelper / 翻译翻译" 
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
              />
            </label>

            <label className="block">
              <span className="text-slate-400 font-medium">头像图片 URL (可选)</span>
              <input 
                value={form.avatar} 
                onChange={e => setForm({...form, avatar: e.target.value})}
                placeholder="https://example.com/avatar.png" 
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
              />
            </label>
          </div>

          <label className="block">
            <span className="text-slate-400 font-medium">职责简述 / 一句话描述</span>
            <input 
              value={form.description} 
              onChange={e => setForm({...form, description: e.target.value})}
              placeholder="简要说明此角色的定位与分工 (100字内)" 
              className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
            />
          </label>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <label className="block">
              <span className="text-slate-400 font-medium">选择关联密钥 (必填)</span>
              <select 
                value={form.model_config_id}
                onChange={e => setForm({...form, model_config_id: Number(e.target.value)})}
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none"
              >
                <option value={0} disabled>-- 请选择密钥配置 --</option>
                {modelConfigs.map(c => (
                  <option key={c.id} value={c.id}>{c.name} ({c.provider_type === 'anthropic' ? 'Anthropic' : 'OpenAI'})</option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="text-slate-400 font-medium">绑定模型标识 Model ID (必填)</span>
              <input 
                required 
                value={form.model_name} 
                onChange={e => setForm({...form, model_name: e.target.value})}
                placeholder="如: claude-3-5-sonnet-latest / gpt-4o" 
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
              />
            </label>

            <label className="block">
              <span className="text-slate-400 font-medium">角色 Tags (逗号分隔)</span>
              <input 
                value={form.tags} 
                onChange={e => setForm({...form, tags: e.target.value})}
                placeholder="例如: 代码开发, 翻译, 规划" 
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
              />
            </label>
          </div>

          <label className="block">
            <span className="text-slate-400 font-medium">核心系统指令 System Prompt (必填)</span>
            <textarea 
              required
              rows={5}
              value={form.system_prompt} 
              onChange={e => setForm({...form, system_prompt: e.target.value})}
              placeholder="输入大模型的最核心提示词指令，定义其逻辑、口吻和角色细节..." 
              className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none font-mono text-[11px] leading-relaxed" 
            />
          </label>

          <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-slate-300 font-medium">上下文窗口（tokens）</p>
                <p className="text-[10px] text-slate-500 mt-1">
                  填写模型 API 实际支持的输入与输出总窗口；设置过大可能被厂商拒绝。
                </p>
              </div>
              <input
                type="number"
                aria-label="上下文窗口 tokens"
                min={4096}
                max={2_000_000}
                step={1024}
                value={form.context_window_tokens}
                onChange={e => setForm({...form, context_window_tokens: Number(e.target.value)})}
                className="w-36 bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none font-mono"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              {[128_000, 200_000, 1_000_000].map(value => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setForm({...form, context_window_tokens: value})}
                  className={`px-3 py-1.5 rounded-lg border text-[10px] transition ${
                    form.context_window_tokens === value
                      ? 'bg-indigo-600 border-indigo-500 text-white'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {value === 1_000_000 ? '1M' : `${Math.round(value / 1000)}K`}
                </button>
              ))}
              <span className="self-center text-[10px] text-slate-500">
                服务上限 {contextCeiling.toLocaleString()} · 有效 {effectiveContextWindow.toLocaleString()} ·
                输出预留 {maxOutputTokens.toLocaleString()} · 可用输入约 {Math.max(0, effectiveContextWindow - maxOutputTokens).toLocaleString()}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="block">
              <span className="text-slate-400 font-medium">绑定内置运行工具 (Builtin Tools)</span>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => toggleTool('web_search')}
                  className={`px-3 py-1.5 rounded-lg border text-[10px] font-medium transition ${
                    form.builtin_tools.includes('web_search')
                      ? 'bg-indigo-600 border-indigo-500 text-white'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  联网搜索 (web_search)
                </button>
                <button
                  type="button"
                  onClick={() => toggleTool('fetch_url')}
                  className={`px-3 py-1.5 rounded-lg border text-[10px] font-medium transition ${
                    form.builtin_tools.includes('fetch_url')
                      ? 'bg-indigo-600 border-indigo-500 text-white'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  抓取 URL (fetch_url)
                </button>
                {([
                  ['workspace_list', '列出工作区'],
                  ['workspace_read', '读取工作区文件'],
                  ['workspace_write', '写入工作区文件'],
                  ['workspace_run_command', '工作区结构化命令'],
                  ['workspace_run_shell', '工作区 Shell（Owner 逐次审批）'],
                ] as const).map(([tool, label]) => (
                  <button
                    key={tool}
                    type="button"
                    aria-pressed={form.builtin_tools.includes(tool)}
                    onClick={() => toggleTool(tool)}
                    className={`px-3 py-1.5 rounded-lg border text-[10px] font-medium transition ${
                      form.builtin_tools.includes(tool)
                        ? 'bg-indigo-600 border-indigo-500 text-white'
                        : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {label} ({tool})
                  </button>
                ))}
              </div>
            </div>

            <label className="block">
              <span className="text-slate-400 font-medium">模型调用参数 (Params JSON 对象)</span>
              <textarea 
                rows={2}
                value={form.params} 
                onChange={e => setForm({...form, params: e.target.value})}
                placeholder='例如: {"temperature": 0.5}' 
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none font-mono" 
              />
            </label>
          </div>

          {errorMsg && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl flex items-start gap-1.5">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <span>{errorMsg}</span>
            </div>
          )}

          {/* 按钮控制 */}
          <div className="flex items-center justify-between border-t border-slate-800 pt-4 mt-2">
            <div>
              {role && (
                <button 
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={busy}
                  className="px-4 py-2.5 bg-red-950 border border-red-900/50 hover:bg-red-900 text-red-400 hover:text-white rounded-xl font-semibold flex items-center gap-1.5 active:scale-[0.98] transition disabled:opacity-50"
                >
                  <Trash2 size={14} />
                  <span>删除角色</span>
                </button>
              )}
            </div>
            
            <div className="flex gap-2">
              <button 
                type="button" 
                onClick={onClose}
                className="px-4 py-2.5 border border-slate-800 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded-xl font-semibold active:scale-[0.98] transition"
              >
                取消
              </button>
              <button 
                type="submit"
                disabled={busy || modelConfigs.length === 0}
                className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold flex items-center gap-1.5 shadow-lg shadow-indigo-600/20 active:scale-[0.98] transition-all disabled:opacity-50"
              >
                <Check size={14} />
                <span>{role ? '保存修改' : '定制并创建角色'}</span>
              </button>
            </div>
          </div>

        </form>
      </div>
    </div>
  )
}

/** 新建会话弹窗 (ConversationModal)。 */
export function ConversationModal({ onClose }: ModalProps) {
  const { roles, workspaceBindings, createConversation } = useAppStore()
  
  const [form, setForm] = useState({
    title: '',
    type: 'single' as 'single' | 'group',
    selected_role_ids: [] as number[],
    orchestrator_enabled: false,
    orchestrator_role_id: 0,
    workspace_binding_id: 0,
  })
  
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  function handleTypeChange(nextType: 'single' | 'group') {
    setForm(f => ({ 
      ...f, 
      type: nextType, 
      selected_role_ids: [], 
      orchestrator_enabled: false, 
      orchestrator_role_id: 0,
      workspace_binding_id: 0,
    }))
  }

  function handleRoleToggle(id: number) {
    setForm(f => {
      let nextIds = [...f.selected_role_ids]
      if (f.type === 'single') {
        nextIds = [id]
      } else {
        const isExist = f.selected_role_ids.includes(id)
        nextIds = isExist ? f.selected_role_ids.filter(item => item !== id) : [...f.selected_role_ids, id]
      }
      return { 
        ...f, 
        selected_role_ids: nextIds,
        orchestrator_role_id: nextIds.includes(f.orchestrator_role_id) ? f.orchestrator_role_id : (nextIds[0] || 0)
      }
    })
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrorMsg(null)

    if (form.selected_role_ids.length === 0) {
      setErrorMsg('请至少选择一个 Agent 角色加入会话。')
      setBusy(false)
      return
    }

    if (form.type === 'single' && form.selected_role_ids.length !== 1) {
      setErrorMsg('单聊会话中只能邀请且必须邀请一位 Agent。')
      setBusy(false)
      return
    }
    if (form.type === 'group' && form.selected_role_ids.length < 2) {
      setErrorMsg('群聊会话至少需要邀请两位 Agent。')
      setBusy(false)
      return
    }

    const payload = {
      type: form.type,
      title: form.title,
      role_ids: form.selected_role_ids,
      orchestrator_enabled: form.type === 'group' ? form.orchestrator_enabled : false,
      orchestrator_role_id: (form.type === 'group' && form.orchestrator_enabled) ? form.orchestrator_role_id || null : null,
      workspace_binding_id: form.type === 'single' ? form.workspace_binding_id || null : null,
    }

    try {
      await createConversation(payload)
      onClose()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建会话失败，请核对角色是否有效。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-xl overflow-hidden flex flex-col max-h-[80vh] shadow-2xl relative z-10">
        
        {/* 头部 */}
        <div className="px-6 py-5 border-b border-slate-800 flex items-center justify-between bg-slate-900/60">
          <div className="flex items-center gap-2 text-indigo-400">
            <Users size={18} />
            <h3 className="font-bold text-white text-base">新建 Agent 协作会话</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white transition">
            <X size={18} />
          </button>
        </div>

        {/* 表单 */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
          
          {roles.length === 0 && (
            <div className="p-3.5 bg-red-500/10 border border-red-500/20 text-red-400 rounded-2xl flex flex-col gap-2">
              <div className="flex items-center gap-1.5">
                <AlertCircle size={16} />
                <span className="font-semibold">⚠️ 暂无可加入会话的角色</span>
              </div>
              <p className="text-[11px] text-red-400/80 leading-relaxed">
                创建会话必须邀请至少一个 Agent 角色。请先去定制创建您的首个 Agent 之后再来发起会话。
              </p>
            </div>
          )}

          {/* 会话类型选择 */}
          <div className="block">
            <span className="text-slate-400 font-medium">会话模式</span>
            <div className="mt-1.5 flex rounded-xl bg-slate-950 p-1 border border-slate-800/80">
              <button 
                type="button" 
                onClick={() => handleTypeChange('single')} 
                className={`flex-1 rounded-lg py-2 transition-all ${form.type === 'single' ? 'bg-indigo-600 text-white font-semibold shadow-md' : 'text-slate-400 hover:text-slate-200'}`}
              >
                单聊 (与单个 Agent 对话)
              </button>
              <button 
                type="button" 
                onClick={() => handleTypeChange('group')} 
                className={`flex-1 rounded-lg py-2 transition-all ${form.type === 'group' ? 'bg-indigo-600 text-white font-semibold shadow-md' : 'text-slate-400 hover:text-slate-200'}`}
              >
                群聊 (协同多个 Agent 角色)
              </button>
            </div>
          </div>

          <label className="block">
            <span className="text-slate-400 font-medium">会话名称 (必填)</span>
            <input 
              required 
              value={form.title} 
              onChange={e => setForm({...form, title: e.target.value})}
              placeholder="例如: 极速网页重构 / 架构评审组" 
              className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2.5 text-slate-200 focus:border-indigo-500 outline-none" 
            />
          </label>

          {/* 选择成员 Agent 列表 */}
          <div className="block">
            <span className="text-slate-400 font-medium">
              {form.type === 'single' ? '邀请 Agent 角色 (单选)' : '选择群聊成员 (支持多选)'}
            </span>
            
            <div className="mt-2.5 bg-slate-950/60 border border-slate-800 rounded-xl p-3.5 max-h-48 overflow-y-auto space-y-2">
              {roles.map((role) => {
                const isChecked = form.selected_role_ids.includes(role.id)
                return (
                  <button
                    type="button"
                    key={role.id}
                    onClick={() => handleRoleToggle(role.id)}
                    aria-pressed={isChecked}
                    className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-slate-900 border cursor-pointer transition ${
                      isChecked 
                        ? 'border-indigo-500/30 bg-indigo-950/20 text-white' 
                        : 'border-transparent text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <div className={`w-3.5 h-3.5 rounded flex items-center justify-center border shrink-0 ${isChecked ? 'bg-indigo-600 border-indigo-500 text-white' : 'border-slate-700 bg-transparent'}`}>
                      {isChecked && <Check size={10} strokeWidth={3} />}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-bold text-slate-200 truncate">{role.name}</p>
                      <p className="text-[10px] text-slate-500 truncate mt-0.5">Model: {role.model_name}</p>
                    </div>
                  </button>
                )
              })}

              {roles.length === 0 && (
                <div className="text-center py-6 text-xs text-slate-700">暂无可用角色数据</div>
              )}
            </div>
          </div>

          {/* M4a 只实现显式 @ 串行调度，Orchestrator 留到 M4b。 */}
          {form.type === 'group' && form.selected_role_ids.length > 1 && (
            <div className="bg-slate-950/40 border border-slate-800 rounded-xl p-4">
              <p className="text-xs font-medium text-slate-300">M4a 显式 @ 串行模式</p>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                群聊只有明确 @ 的角色会依次回复；没有 @ 时只记录消息。Orchestrator 将在 M4b 开放。
              </p>
            </div>
          )}

          {form.type === 'single' && (
            <label className="block">
              <span className="text-slate-400 font-medium">工作区（可选）</span>
              <select
                aria-label="会话工作区"
                value={form.workspace_binding_id}
                onChange={(event) => setForm({ ...form, workspace_binding_id: Number(event.target.value) })}
                className="mt-1.5 w-full rounded-xl border border-slate-800 bg-slate-950 px-3 py-2.5 text-slate-200 outline-none focus:border-indigo-500"
              >
                <option value={0}>不绑定工作区</option>
                {workspaceBindings
                  .filter((workspace) => workspace.active && workspace.availability === 'available')
                  .map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.display_name} · {workspace.root_path}
                    </option>
                  ))}
              </select>
              <span className="mt-1 block text-[10px] leading-relaxed text-slate-500">
                仅当角色和工作区都开启原生文件能力时，Owner 消息才会向模型暴露文件工具。
              </span>
            </label>
          )}

          {errorMsg && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl flex items-start gap-1.5">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <span>{errorMsg}</span>
            </div>
          )}

          {/* 表单按钮 */}
          <div className="flex items-center justify-end gap-2 border-t border-slate-800 pt-4 mt-2">
            <button 
              type="button" 
              onClick={onClose}
              className="px-4 py-2.5 border border-slate-800 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded-xl font-semibold active:scale-[0.98] transition"
            >
              取消
            </button>
            <button 
              type="submit"
              disabled={busy || roles.length === 0}
              className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold flex items-center gap-1.5 shadow-lg shadow-indigo-600/20 active:scale-[0.98] transition-all disabled:opacity-50"
            >
              <Check size={14} />
              <span>确认开启会话</span>
            </button>
          </div>

        </form>
      </div>
    </div>
  )
}

interface ConversationMembersModalProps extends ModalProps {
  conversation: Conversation
}

/** Owner 调整群聊角色成员与稳定 all 展开顺序。 */
export function ConversationMembersModal({ conversation, onClose }: ConversationMembersModalProps) {
  const roles = useAppStore((state) => state.roles)
  const updateMembers = useAppStore((state) => state.updateConversationMembers)
  const [selected, setSelected] = useState<number[]>(conversation.role_ids)
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  /** @param roleId 需要加入或移出群聊的角色 ID。 */
  function toggle(roleId: number) {
    setSelected((current) => (
      current.includes(roleId) ? current.filter((id) => id !== roleId) : [...current, roleId]
    ))
  }

  /** 保存成员顺序；revision 冲突要求关闭后刷新再重试。 */
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (selected.length < 2) {
      setErrorMsg('群聊至少需要两位 Agent 角色。')
      return
    }
    setBusy(true)
    setErrorMsg(null)
    try {
      await updateMembers(conversation.id, selected, conversation.revision)
      onClose()
    } catch (error) {
      setErrorMsg(error instanceof Error ? error.message : '更新群聊成员失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-3xl border border-slate-800 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-6 py-5">
          <div className="flex items-center gap-2 text-indigo-400">
            <Users size={18} />
            <h3 className="text-base font-bold text-white">管理群聊成员</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭成员管理" className="text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={submit} className="flex-1 space-y-4 overflow-y-auto p-6">
          <p className="text-xs leading-relaxed text-slate-500">
            选择顺序决定 @全部 的回复顺序。至少保留两位角色，历史消息不会随成员移出而删除。
          </p>
          <div className="space-y-2" aria-label="群聊角色成员">
            {roles.map((role) => {
              const checked = selected.includes(role.id)
              return (
                <button
                  key={role.id}
                  type="button"
                  onClick={() => toggle(role.id)}
                  aria-pressed={checked}
                  className={`flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition ${
                    checked ? 'border-indigo-500/40 bg-indigo-950/30' : 'border-slate-800 bg-slate-950/50'
                  }`}
                >
                  <span className={`flex h-4 w-4 items-center justify-center rounded border ${checked ? 'border-indigo-500 bg-indigo-600' : 'border-slate-700'}`}>
                    {checked && <Check size={11} />}
                  </span>
                  <span className="text-xs font-semibold text-slate-200">{role.name}</span>
                </button>
              )
            })}
          </div>
          {errorMsg && <p className="text-xs text-red-400">{errorMsg}</p>}
          <div className="flex justify-end gap-2 border-t border-slate-800 pt-4">
            <button type="button" onClick={onClose} className="rounded-xl border border-slate-800 px-4 py-2 text-xs text-slate-300">取消</button>
            <button type="submit" disabled={busy} className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">
              {busy ? '保存中…' : '保存成员'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
