import { expect, type Page } from '@playwright/test'

/**
 * 端到端测试共用的 Owner 账号。
 *
 * 账号名带本轮时间戳，与数据库文件名一致：数据库按轮次保留，
 * 测试结束后可以用这个账号登录进去核对本轮产生的数据。
 */
const STAMP = process.env.ROLEPLEX_E2E_STAMP ?? 'local'

export const OWNER = {
  username: `test${STAMP}`,
  nickname: `测试 Owner ${STAMP}`,
  // 固定占位口令，符合密码策略以免测试账号被强制改密；仅用于本地测试库。
  password: 'Roleplex-Test-1234',
}

/**
 * 保证浏览器处于已登录 Owner 状态：账号不存在时注册，存在时直接登录。
 *
 * 由于 Owner 是实例级单例，多个测试文件必须复用同一个账号，
 * 否则后注册的账号会成为 Guest 并在配置类接口上被拒绝。
 */
export async function ensureOwnerSession(page: Page) {
  await page.goto('/#/auth')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByPlaceholder('owner').fill(OWNER.username)
  await page.getByPlaceholder('密码', { exact: true }).fill(OWNER.password)
  await page.getByRole('button', { name: '进入工作台' }).click()

  const workspace = page.getByText('欢迎来到 Roleplex')
  const authFailed = page.getByText('AUTH_INVALID')
  await expect(workspace.or(authFailed)).toBeVisible({ timeout: 15_000 })
  if (await workspace.isVisible()) return

  await page.getByRole('button', { name: '首次注册' }).click()
  await page.getByPlaceholder('owner').fill(OWNER.username)
  await page.getByPlaceholder('我的名字').fill(OWNER.nickname)
  await page.getByPlaceholder('密码', { exact: true }).fill(OWNER.password)
  await page.getByPlaceholder('再次输入密码').fill(OWNER.password)
  await page.getByRole('button', { name: '创建 Owner 账号' }).click()
  await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible({ timeout: 15_000 })
}
