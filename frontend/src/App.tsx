import { type FormEvent, useEffect, useState } from 'react'
import { Bot, LogIn, MessageCircle, Plus, Search, Settings2, Users, WandSparkles } from 'lucide-react'
import { useAppStore } from './store/app'

/** 渲染登录和首个 Owner 注册流程。 */
function AuthScreen() {
  const authenticate = useAppStore((state) => state.authenticate)
  const error = useAppStore((state) => state.error)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [form, setForm] = useState({ username: '', password: '', nickname: '' })
  const [passwordConfirmation, setPasswordConfirmation] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /**
   * 校验并提交认证表单。
   * @param event 认证表单触发的浏览器提交事件。
   */
  async function submit(event: FormEvent) {
    event.preventDefault()
    setValidationError(null)
    if (mode === 'register' && form.password !== passwordConfirmation) {
      // 确认密码只用于客户端校验，绝不能发送到 API。
      setValidationError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      await authenticate(mode, form)
    } catch {
      // 状态仓库会保留服务端错误，表单可以显示稳定的提示信息。
    } finally {
      setBusy(false)
    }
  }

  /** 切换认证模式，并清理新模式不需要的字段。 */
  function switchMode(nextMode: 'login' | 'register') {
    setMode(nextMode)
    setValidationError(null)
    setPasswordConfirmation('')
  }

  return <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
    <section className="w-full max-w-md rounded-3xl bg-white p-8 shadow-2xl">
      <div className="mb-8 flex items-center gap-3"><div className="rounded-2xl bg-indigo-600 p-3 text-white"><WandSparkles size={24} /></div><div><h1 className="text-2xl font-bold">Roleplex</h1><p className="text-sm text-slate-500">你的 Agent 群聊工作台</p></div></div>
      <div className="mb-6 flex rounded-xl bg-slate-100 p-1 text-sm"><button type="button" onClick={() => switchMode('login')} className={`flex-1 rounded-lg py-2 ${mode === 'login' ? 'bg-white font-semibold shadow-sm' : 'text-slate-500'}`}>登录</button><button type="button" onClick={() => switchMode('register')} className={`flex-1 rounded-lg py-2 ${mode === 'register' ? 'bg-white font-semibold shadow-sm' : 'text-slate-500'}`}>首次注册</button></div>
      <form onSubmit={submit} className="space-y-4">
        <label className="block text-sm font-medium">用户名<input required minLength={3} value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} className="mt-1 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-indigo-500" placeholder="owner" /></label>
        {mode === 'register' && <label className="block text-sm font-medium">昵称<input required value={form.nickname} onChange={(event) => setForm({ ...form, nickname: event.target.value })} className="mt-1 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-indigo-500" placeholder="我的名字" /></label>}
        <label className="block text-sm font-medium">密码<input required minLength={8} type="password" value={form.password} onChange={(event) => { setForm({ ...form, password: event.target.value }); setValidationError(null) }} className="mt-1 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-indigo-500" placeholder="至少 8 位" /></label>
        {mode === 'register' && <label className="block text-sm font-medium">再次输入密码<input required minLength={8} type="password" value={passwordConfirmation} onChange={(event) => { setPasswordConfirmation(event.target.value); setValidationError(null) }} className="mt-1 w-full rounded-xl border border-slate-200 px-4 py-3 outline-none focus:border-indigo-500" placeholder="再次输入密码" /></label>}
        {(validationError || error) && <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-600">{validationError || error}</p>}
        <button type="submit" disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 py-3 font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-50"><LogIn size={18} />{busy ? '处理中…' : mode === 'login' ? '进入工作台' : '创建 Owner 账号'}</button>
      </form>
    </section>
  </main>
}

/** 渲染会话列表和当前用户的 Agent 联系人。 */
function Sidebar() {
  const { conversations, activeConversationId, setActiveConversation, roles } = useAppStore()
  return <aside className="flex w-80 shrink-0 flex-col border-r border-slate-200 bg-white">
    <div className="flex items-center justify-between border-b border-slate-100 px-5 py-5"><div className="flex items-center gap-2"><div className="rounded-lg bg-indigo-600 p-2 text-white"><WandSparkles size={18} /></div><span className="font-bold">Roleplex</span></div><button type="button" aria-label="新建会话" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100"><Plus size={18} /></button></div>
    <div className="px-4 pt-4"><div className="flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2 text-slate-400"><Search size={16} /><input aria-label="搜索会话" className="w-full bg-transparent text-sm outline-none" placeholder="搜索会话" /></div></div>
    <div className="flex items-center gap-2 px-5 pb-2 pt-6 text-xs font-semibold uppercase tracking-wider text-slate-400"><MessageCircle size={14} />会话</div>
    <div className="flex-1 overflow-y-auto px-3">{conversations.length === 0 ? <div className="px-3 py-10 text-center text-sm text-slate-400">还没有会话<br /><span className="text-xs">创建角色后开始第一次对话</span></div> : conversations.map((conversation) => <button type="button" key={conversation.id} onClick={() => setActiveConversation(conversation.id)} className={`mb-1 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left ${activeConversationId === conversation.id ? 'bg-indigo-50 text-indigo-700' : 'hover:bg-slate-50'}`}><div className="rounded-lg bg-slate-100 p-2"><Users size={17} /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{conversation.title}</p><p className="text-xs text-slate-400">{conversation.type === 'group' ? '群聊' : '单聊'}{conversation.pinned ? ' · 已置顶' : ''}</p></div></button>)}</div>
    <div className="border-t border-slate-100 p-4"><div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400"><Bot size={14} />我的 Agent</div><div className="space-y-1">{roles.slice(0, 3).map((role) => <div key={role.id} className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm"><div className="rounded-full bg-indigo-100 p-1.5 text-indigo-600"><Bot size={14} /></div><span className="truncate">{role.name}</span></div>)}{roles.length === 0 && <p className="px-2 text-xs text-slate-400">还没有创建角色</p>}</div></div>
  </aside>
}

/** 渲染用户创建会话前的初始工作台状态。 */
function EmptyWorkspace() {
  const user = useAppStore((state) => state.user)
  const logout = useAppStore((state) => state.logout)
  return <section className="flex flex-1 flex-col items-center justify-center bg-[#f6f7f9] px-6"><div className="mb-5 rounded-3xl bg-white p-5 text-indigo-600 shadow-panel"><WandSparkles size={36} /></div><h2 className="text-2xl font-bold">欢迎来到 Roleplex</h2><p className="mt-2 max-w-md text-center text-slate-500">创建你的第一个 Agent 角色，然后开启单聊或邀请多个角色进入群聊。</p><div className="mt-8 flex gap-3"><button type="button" className="flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-3 font-semibold text-white hover:bg-indigo-700"><Plus size={17} />创建角色</button><button type="button" className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-5 py-3 font-semibold text-slate-600 hover:bg-slate-50"><Settings2 size={17} />打开设置</button></div><div className="mt-12 flex items-center gap-3 text-sm text-slate-400"><span>{user?.nickname}</span><span>·</span><button type="button" onClick={logout} className="hover:text-indigo-600">退出登录</button></div></section>
}

/** 启动已认证的工作台；没有会话时显示空状态。 */
export function App() {
  const { user, loading, bootstrap, activeConversationId } = useAppStore()
  useEffect(() => { void bootstrap() }, [bootstrap])
  if (loading) return <div className="flex min-h-screen items-center justify-center bg-slate-950 text-white">正在加载 Roleplex…</div>
  if (!user) return <AuthScreen />
  return <main className="flex min-h-screen bg-[#f6f7f9]"><Sidebar />{activeConversationId ? <section className="flex flex-1 items-center justify-center text-slate-400">聊天窗口将在 M2 接入</section> : <EmptyWorkspace />}</main>
}
