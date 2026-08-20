import { execFileSync } from 'node:child_process'
import { test, expect } from '@playwright/test'
import { OWNER } from './owner'

// 端到端后端使用独立端口，地址由 playwright.config.ts 通过环境变量下发。
const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8001'

// 分发包中的初始弱口令形态：长度不足且缺少数字与符号，注册接口无法创建。
// 账号名带本轮时间戳，与数据库同轮次对应，避免同一个库里重复播种后口令已被改过。
const WEAK_ACCOUNT = {
  username: `weakreset${process.env.ROLEPLEX_E2E_STAMP ?? 'local'}`,
  password: 'owner',
  nickname: '分发世界访客',
}
const STRONG_PASSWORD = 'Distributed-World-2026'

/**
 * 直接写库播种一个弱口令账号。
 *
 * 强制重置流程要求账号的当前口令不合规，而注册接口会拒绝这类口令，
 * 因此只能绕过接口播种；脚本本身在 `backend/tests/seed_weak_account.py`。
 */
function seedWeakAccount() {
  const database = process.env.ROLEPLEX_E2E_DATABASE_PATH
  if (!database) throw new Error('缺少 ROLEPLEX_E2E_DATABASE_PATH，无法播种弱口令账号')
  execFileSync('python', [
    'tests/seed_weak_account.py',
    '--database', database,
    '--username', WEAK_ACCOUNT.username,
    '--password', WEAK_ACCOUNT.password,
    '--nickname', WEAK_ACCOUNT.nickname,
  ], { cwd: '../backend', stdio: 'pipe' })
}

/**
 * 密码策略的浏览器端行为。
 *
 * 覆盖两条用户可见路径：注册表单实时展示各项要求的达成情况，
 * 以及不合规密码在提交前被拦下并给出可读提示（不发出注册请求）。
 * 服务端的拦截规则与强制重置流程由 `backend/tests/test_password_policy.py` 覆盖。
 */
test.describe('password policy', () => {
  /** 打开注册表单，返回后续用例共用的字段定位器。 */
  async function openRegisterForm(page: import('@playwright/test').Page) {
    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '首次注册' }).click()
    return {
      username: page.getByPlaceholder('owner'),
      nickname: page.getByPlaceholder('我的名字'),
      password: page.getByPlaceholder('密码', { exact: true }),
      confirmation: page.getByPlaceholder('再次输入密码'),
    }
  }

  test('shows live password requirement feedback while typing', async ({ page }) => {
    const form = await openRegisterForm(page)
    const requirements = page.getByTestId('password-requirements')
    await expect(requirements).toBeVisible()

    // 只输入字母时，长度、数字和符号三项都还没达成。
    await form.password.fill('abcdef')
    await expect(requirements.getByText('至少 10 个字符')).toBeVisible()
    await expect(requirements.getByText('包含数字')).toBeVisible()
    await expect(requirements.getByText('包含符号（如 !@#$%^&*）')).toBeVisible()

    // 补齐后各项仍然展示，用于让用户确认全部达成。
    await form.password.fill(OWNER.password)
    await expect(requirements.getByText('至少 10 个字符')).toBeVisible()
    await page.screenshot({ path: 'test-results/password-requirements.png', fullPage: true })
  })

  test('blocks weak password registration before sending a request', async ({ page }) => {
    let registerRequests = 0
    page.on('request', (request) => {
      if (request.url().endsWith('/api/auth/register')) registerRequests += 1
    })

    const form = await openRegisterForm(page)
    await form.username.fill(`weak_${Date.now()}`)
    await form.nickname.fill('弱密码测试')
    // 长度足够但缺少数字和符号，属于典型的不合规输入。
    await form.password.fill('onlyletterspassword')
    await form.confirmation.fill('onlyletterspassword')
    await page.getByRole('button', { name: '创建 Owner 账号' }).click()

    await expect(page.getByText('密码不满足安全要求')).toBeVisible()
    expect(registerRequests).toBe(0)
    // 仍停留在认证页，没有进入工作台。
    await expect(page.getByText('你的 Agent 群聊工作台')).toBeVisible()
  })

  test('rejects a weak password at the API with a stable error code', async ({ request }) => {
    const response = await request.post(`${backend}/api/auth/register`, {
      data: { username: `weak_api_${Date.now()}`, password: 'short', nickname: '弱密码接口测试' },
    })
    expect(response.status()).toBe(422)
    const body = await response.json()
    expect(body.error.code).toBe('PASSWORD_POLICY_VIOLATION')
    expect(body.error.details.length).toBeGreaterThan(0)
  })

  test('forces a weak password account through the reset screen before the workspace', async ({ page }) => {
    const browserErrors: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
    page.on('pageerror', (error) => browserErrors.push(error.message))
    seedWeakAccount()

    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByPlaceholder('owner').fill(WEAK_ACCOUNT.username)
    await page.getByPlaceholder('密码', { exact: true }).fill(WEAK_ACCOUNT.password)
    await page.getByRole('button', { name: '进入工作台' }).click()

    // 登录成功但被拦在重置页，工作台不得渲染。
    await expect(page.getByText('请先修改密码')).toBeVisible()
    await expect(page.getByText(WEAK_ACCOUNT.nickname)).toBeVisible()
    await expect(page.getByText('欢迎来到 Roleplex')).toBeHidden()

    // 直接改 hash 也不能绕过前端门禁。
    await page.evaluate(() => { window.location.hash = '#/workspace' })
    await expect(page.getByText('请先修改密码')).toBeVisible()

    // 新密码同样要过策略，且给出可读提示而不是错误码。
    await page.getByPlaceholder('当前密码').fill(WEAK_ACCOUNT.password)
    await page.getByPlaceholder('新密码', { exact: true }).fill('alllowercase')
    await page.getByPlaceholder('再次输入新密码').fill('alllowercase')
    await page.getByRole('button', { name: '修改密码并进入工作台' }).click()
    await expect(page.getByText('新密码不满足安全要求')).toBeVisible()

    // 合规新密码通过后直接进入工作台，刷新也不再要求重置。
    await page.getByPlaceholder('新密码', { exact: true }).fill(STRONG_PASSWORD)
    await page.getByPlaceholder('再次输入新密码').fill(STRONG_PASSWORD)
    await page.getByRole('button', { name: '修改密码并进入工作台' }).click()
    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    await page.screenshot({ path: 'test-results/password-reset-done.png', fullPage: true })

    await page.reload()
    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    expect(browserErrors).toEqual([])
  })
})
