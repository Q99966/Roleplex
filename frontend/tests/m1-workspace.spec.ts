import { test, expect } from '@playwright/test'
import { OWNER } from './owner'

// Owner 是实例级单例：本轮数据库中的首个注册者必须是共享 Owner 账号，
// 否则后续测试文件登录时只能拿到 Guest 身份并在配置类接口上被拒。
const password = OWNER.password

test.describe('M1 authentication and workspace', () => {
  test('registers the first Owner and renders the workspace', async ({ page }) => {
    const browserErrors: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
    page.on('pageerror', (error) => browserErrors.push(error.message))
    await page.goto('/')
    await expect(page.getByText('Roleplex').first()).toBeVisible()
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '首次注册' }).click()
    await page.getByPlaceholder('owner').fill(OWNER.username)
    await page.getByPlaceholder('我的名字').fill(OWNER.nickname)
    await page.getByPlaceholder('至少 8 位').fill(password)
    await page.getByPlaceholder('再次输入密码').fill(password)
    await page.getByRole('button', { name: '创建 Owner 账号' }).click()

    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    await expect(page.getByText(OWNER.nickname)).toBeVisible()
    await expect(page.getByText('还没有创建角色')).toBeVisible()
    await expect(page).toHaveTitle('Roleplex')
    await page.screenshot({ path: 'test-results/m1-empty-workspace.png', fullPage: true })
    expect(browserErrors).toEqual([])
  })

  test('blocks registration when password confirmation differs', async ({ page }) => {
    let registerRequests = 0
    await page.on('request', (request) => {
      if (request.url().endsWith('/api/auth/register')) registerRequests += 1
    })
    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '首次注册' }).click()
    await page.getByPlaceholder('owner').fill(`mismatch_${Date.now()}`)
    await page.getByPlaceholder('我的名字').fill('密码校验测试')
    await page.getByPlaceholder('至少 8 位').fill(password)
    await page.getByPlaceholder('再次输入密码').fill('different123')
    await page.getByRole('button', { name: '创建 Owner 账号' }).click()
    await expect(page.getByText('两次输入的密码不一致')).toBeVisible()
    expect(registerRequests).toBe(0)
    await expect(page.getByText('你的 Agent 群聊工作台')).toBeVisible()
  })

  test('logs in an existing Owner and preserves the empty workspace', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByPlaceholder('owner').fill(OWNER.username)
    await page.getByPlaceholder('至少 8 位').fill(password)
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    await expect(page.getByText(OWNER.nickname)).toBeVisible()
  })

  test('shows an authentication error for invalid credentials', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByPlaceholder('owner').fill('missing_user')
    await page.getByPlaceholder('至少 8 位').fill(password)
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText('AUTH_INVALID')).toBeVisible()
  })

  test('rejects protected API access without authentication', async ({ request }) => {
    const response = await request.get('http://127.0.0.1:8000/api/model-configs')
    expect(response.status()).toBe(401)
    expect((await response.json()).error.code).toBe('AUTH_REQUIRED')
  })
})
