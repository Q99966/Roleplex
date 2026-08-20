import { type FormEvent, useState } from 'react'
import { LogIn, WandSparkles, AlertCircle } from 'lucide-react'
import { useAppStore } from '../store/app'
import { PasswordRequirements, isPasswordCompliant } from './PasswordRequirements'

/** 渲染登录和首次 Owner 注册流程，背景采用简洁静雅的极光色调，无动画特效。 */
export function AuthScreen() {
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
    if (mode === 'register') {
      if (form.password !== passwordConfirmation) {
        setValidationError('两次输入的密码不一致')
        return
      }
      // 注册前先按前端镜像的策略拦一次，避免一次必然失败的请求；
      // 最终判定仍以服务端返回的 PASSWORD_POLICY_VIOLATION 为准。
      if (!isPasswordCompliant(form.password)) {
        setValidationError('密码不满足安全要求')
        return
      }
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

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 relative overflow-hidden">
      {/* 极光发光软罩 (静态背景) */}
      <div className="absolute top-1/4 left-1/4 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-indigo-500/5 blur-[120px] pointer-events-none z-0"></div>
      <div className="absolute bottom-1/4 right-1/4 translate-x-1/2 translate-y-1/2 w-96 h-96 rounded-full bg-purple-500/5 blur-[120px] pointer-events-none z-0"></div>

      <section className="w-full max-w-md rounded-3xl bg-slate-900/60 border border-slate-800 p-8 shadow-2xl backdrop-blur-xl relative z-10">
        <div className="mb-8 flex items-center gap-3">
          <div className="rounded-2xl bg-indigo-600 p-3 text-white shadow-[0_0_20px_rgba(99,102,241,0.4)]">
            <WandSparkles size={24} />
          </div>
          <div>
            <h1 className="text-2xl font-bold bg-gradient-to-r from-white via-slate-100 to-indigo-300 bg-clip-text text-transparent">Roleplex</h1>
            <p className="text-sm text-slate-400">你的 Agent 群聊工作台</p>
          </div>
        </div>

        <div className="mb-6 flex rounded-xl bg-slate-950 p-1 text-sm border border-slate-800/80">
          <button 
            type="button" 
            onClick={() => switchMode('login')} 
            className={`flex-1 rounded-lg py-2 transition-all ${mode === 'login' ? 'bg-indigo-600 text-white font-semibold shadow-md' : 'text-slate-400 hover:text-slate-200'}`}
          >
            登录
          </button>
          <button 
            type="button" 
            onClick={() => switchMode('register')} 
            className={`flex-1 rounded-lg py-2 transition-all ${mode === 'register' ? 'bg-indigo-600 text-white font-semibold shadow-md' : 'text-slate-400 hover:text-slate-200'}`}
          >
            首次注册
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4 text-slate-300">
          <label className="block text-sm font-medium">
            <span className="text-slate-400">用户名</span>
            <input 
              required 
              minLength={3} 
              value={form.username} 
              onChange={(event) => setForm({ ...form, username: event.target.value })} 
              className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-indigo-500 transition-all placeholder:text-slate-600" 
              placeholder="owner" 
            />
          </label>
          
          {mode === 'register' && (
            <label className="block text-sm font-medium">
              <span className="text-slate-400">昵称</span>
              <input 
                required 
                value={form.nickname} 
                onChange={(event) => setForm({ ...form, nickname: event.target.value })} 
                className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-indigo-500 transition-all placeholder:text-slate-600" 
                placeholder="我的名字" 
              />
            </label>
          )}
          
          <label className="block text-sm font-medium">
            <span className="text-slate-400">密码</span>
            <input
              required
              type="password"
              value={form.password}
              onChange={(event) => { setForm({ ...form, password: event.target.value }); setValidationError(null) }}
              className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-indigo-500 transition-all placeholder:text-slate-600"
              placeholder="密码"
            />
            {/* 登录时不提示策略：老账号可能是弱口令，登录后会被引导到强制重置页。 */}
            {mode === 'register' && <PasswordRequirements password={form.password} />}
          </label>

          {mode === 'register' && (
            <label className="block text-sm font-medium">
              <span className="text-slate-400">再次输入密码</span>
              <input
                required
                type="password"
                value={passwordConfirmation}
                onChange={(event) => { setPasswordConfirmation(event.target.value); setValidationError(null) }}
                className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-indigo-500 transition-all placeholder:text-slate-600"
                placeholder="再次输入密码"
              />
            </label>
          )}
          
          {(validationError || error) && (
            <p className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400 flex items-center gap-2">
              <AlertCircle size={16} />
              <span>{validationError || error}</span>
            </p>
          )}
          
          <button 
            type="submit" 
            disabled={busy} 
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 py-3 font-semibold text-white transition-all hover:bg-indigo-500 active:scale-[0.98] disabled:opacity-50 shadow-lg shadow-indigo-600/30"
          >
            <LogIn size={18} />
            {busy ? '处理中…' : mode === 'login' ? '进入工作台' : '创建 Owner 账号'}
          </button>
        </form>
      </section>
    </main>
  )
}
