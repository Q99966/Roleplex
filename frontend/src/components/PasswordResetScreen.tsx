import { type FormEvent, useState } from 'react'
import { AlertCircle, KeyRound, ShieldAlert } from 'lucide-react'
import { useAppStore } from '../store/app'
import { PasswordRequirements, isPasswordCompliant } from './PasswordRequirements'

/**
 * 强制修改密码页。
 *
 * 当前口令不满足密码策略时（典型场景是分发出去的世界使用初始弱口令），
 * 服务端签发的 Token 带待改密标记，除改密和查看本人资料外的接口一律被拒绝。
 * 因此这里不提供"稍后再说"，只能改密或退出登录。
 */
export function PasswordResetScreen() {
  const changePassword = useAppStore((state) => state.changePassword)
  const logout = useAppStore((state) => state.logout)
  const user = useAppStore((state) => state.user)
  const error = useAppStore((state) => state.error)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /**
   * 校验并提交改密表单。
   * @param event 表单触发的浏览器提交事件。
   */
  async function submit(event: FormEvent) {
    event.preventDefault()
    setValidationError(null)
    if (newPassword !== confirmation) {
      setValidationError('两次输入的新密码不一致')
      return
    }
    if (!isPasswordCompliant(newPassword)) {
      setValidationError('新密码不满足安全要求')
      return
    }
    setBusy(true)
    try {
      await changePassword(currentPassword, newPassword)
    } catch {
      // 状态仓库保留服务端错误码，表单直接展示。
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 relative overflow-hidden">
      <div className="absolute top-1/4 left-1/4 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-amber-500/5 blur-[120px] pointer-events-none z-0"></div>

      <section className="w-full max-w-md rounded-3xl bg-slate-900/60 border border-slate-800 p-8 shadow-2xl backdrop-blur-xl relative z-10">
        <div className="mb-6 flex items-center gap-3">
          <div className="rounded-2xl bg-amber-600 p-3 text-white shadow-[0_0_20px_rgba(217,119,6,0.4)]">
            <ShieldAlert size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">请先修改密码</h1>
            <p className="text-sm text-slate-400">
              {user ? `${user.nickname}（${user.username}）` : '当前账号'}的密码不符合安全要求
            </p>
          </div>
        </div>

        <p className="mb-6 rounded-xl bg-amber-500/10 border border-amber-500/20 px-4 py-3 text-sm text-amber-300">
          修改完成前无法使用工作台的其他功能。
        </p>

        <form onSubmit={submit} className="space-y-4 text-slate-300">
          <label className="block text-sm font-medium">
            <span className="text-slate-400">当前密码</span>
            <input
              required
              type="password"
              value={currentPassword}
              onChange={(event) => { setCurrentPassword(event.target.value); setValidationError(null) }}
              className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-amber-500 transition-all placeholder:text-slate-600"
              placeholder="当前密码"
            />
          </label>

          <label className="block text-sm font-medium">
            <span className="text-slate-400">新密码</span>
            <input
              required
              type="password"
              value={newPassword}
              onChange={(event) => { setNewPassword(event.target.value); setValidationError(null) }}
              className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-amber-500 transition-all placeholder:text-slate-600"
              placeholder="新密码"
            />
            <PasswordRequirements password={newPassword} />
          </label>

          <label className="block text-sm font-medium">
            <span className="text-slate-400">再次输入新密码</span>
            <input
              required
              type="password"
              value={confirmation}
              onChange={(event) => { setConfirmation(event.target.value); setValidationError(null) }}
              className="mt-1.5 w-full rounded-xl bg-slate-950 border border-slate-800 px-4 py-3 text-white outline-none focus:border-amber-500 transition-all placeholder:text-slate-600"
              placeholder="再次输入新密码"
            />
          </label>

          {(validationError || error) && (
            <p className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-sm text-red-400 flex items-center gap-2">
              <AlertCircle size={16} />
              <span>{validationError || error}</span>
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-amber-600 py-3 font-semibold text-white transition-all hover:bg-amber-500 active:scale-[0.98] disabled:opacity-50 shadow-lg shadow-amber-600/30"
          >
            <KeyRound size={18} />
            {busy ? '处理中…' : '修改密码并进入工作台'}
          </button>

          <button
            type="button"
            onClick={logout}
            className="w-full rounded-xl border border-slate-800 py-2.5 text-sm text-slate-400 transition hover:text-slate-200 hover:border-slate-700"
          >
            退出登录
          </button>
        </form>
      </section>
    </main>
  )
}
