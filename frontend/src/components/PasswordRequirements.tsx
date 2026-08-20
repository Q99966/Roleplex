import { Check, X } from 'lucide-react'

/**
 * 前端密码策略镜像。
 *
 * 服务端 `backend/app/password_policy.py` 是唯一权威，这里只做实时提示，
 * 两边规则必须同步修改。前端不做最终判定：即使这里全部通过，仍以服务端
 * 返回的 `PASSWORD_POLICY_VIOLATION` 为准。
 */
export const PASSWORD_MIN_LENGTH = 10
export const PASSWORD_MAX_BYTES = 72

// 与后端 string.punctuation 逐字对应的 ASCII 符号集合；空格不在其中。
const SYMBOLS = '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'

/** 判断密码是否包含至少一个符号。 */
function hasSymbol(password: string): boolean {
  return Array.from(password).some((character) => SYMBOLS.includes(character))
}

/** 单条要求的达成情况。 */
export type PasswordCheck = { label: string; passed: boolean }

/**
 * 计算各项密码要求的达成情况。
 * @param password 当前输入的明文密码。
 * @returns 与服务端策略一一对应的检查项列表。
 */
export function checkPassword(password: string): PasswordCheck[] {
  return [
    { label: `至少 ${PASSWORD_MIN_LENGTH} 个字符`, passed: password.length >= PASSWORD_MIN_LENGTH },
    { label: '包含字母', passed: /[A-Za-z]/.test(password) },
    { label: '包含数字', passed: /[0-9]/.test(password) },
    { label: '包含符号（如 !@#$%^&*）', passed: hasSymbol(password) },
  ]
}

/** 判断密码是否满足全部前端可见的要求（不含服务端才能确认的部分）。 */
export function isPasswordCompliant(password: string): boolean {
  return checkPassword(password).every((item) => item.passed) && !isPasswordTooLong(password)
}

/** 判断密码 UTF-8 编码是否超过 bcrypt 的 72 字节上限。 */
export function isPasswordTooLong(password: string): boolean {
  return new TextEncoder().encode(password).length > PASSWORD_MAX_BYTES
}

/**
 * 实时展示密码策略各项要求的达成情况。
 * @param password 当前输入的明文密码，为空时以未达成状态展示全部要求。
 */
export function PasswordRequirements({ password }: { password: string }) {
  const checks = checkPassword(password)
  const tooLong = isPasswordTooLong(password)
  return (
    <ul className="mt-2 space-y-1 text-xs" data-testid="password-requirements">
      {checks.map((item) => (
        <li key={item.label} className={`flex items-center gap-1.5 ${item.passed ? 'text-emerald-400' : 'text-slate-500'}`}>
          {item.passed ? <Check size={13} /> : <X size={13} />}
          <span>{item.label}</span>
        </li>
      ))}
      {tooLong && (
        <li className="flex items-center gap-1.5 text-red-400">
          <X size={13} />
          <span>密码过长，请控制在 {PASSWORD_MAX_BYTES} 字节以内</span>
        </li>
      )}
    </ul>
  )
}
