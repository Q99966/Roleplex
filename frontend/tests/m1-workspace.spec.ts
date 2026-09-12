import { test, expect } from '@playwright/test'
import { OWNER, ensureOwnerSession } from './owner'

// Owner 是实例级单例：本轮数据库中的首个注册者必须是共享 Owner 账号，
// 否则后续测试文件登录时只能拿到 Guest 身份并在配置类接口上被拒。
const password = OWNER.password

// 端到端后端使用独立端口，地址由 playwright.config.ts 通过环境变量下发；
// 硬编码开发端口会在没有开发服务时连不上。
const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8001'

test.describe('M1 authentication and workspace', () => {
  test('role tool settings omit unimplemented web placeholders', async ({ page }, testInfo) => {
    await ensureOwnerSession(page)
    await page.getByTitle('定制 Agent 角色').click()
    await expect(page.getByRole('button', { name: '联网搜索 (web_search)', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '抓取 URL (fetch_url)', exact: true })).toHaveCount(0)
    const write = page.getByRole('button', { name: '写入工作区文件 (workspace_write)', exact: true })
    await expect(write).toHaveAttribute('aria-pressed', 'false')
    await write.click()
    await expect(write).toHaveAttribute('aria-pressed', 'true')
    const edit = page.getByRole('button', { name: '局部编辑工作区文件 (workspace_edit)', exact: true })
    await expect(edit).toHaveAttribute('aria-pressed', 'false')
    await edit.click()
    await expect(edit).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('button', { name: '批量读取工作区文件 (workspace_read_many)', exact: true })).toHaveCount(0)
    const read = page.getByRole('button', { name: '读取工作区文件 (workspace_read)', exact: true })
    await expect(read).toHaveAttribute('aria-pressed', 'false')
    await read.click()
    await expect(read).toHaveAttribute('aria-pressed', 'true')
    const screenshot = testInfo.outputPath('implemented-tools.png')
    await write.locator('xpath=../..').screenshot({ path: screenshot })
    await testInfo.attach('已实现工具选择', { path: screenshot, contentType: 'image/png' })
  })

  test('registers a new account through the form and renders an empty workspace', async ({ page }) => {
    // 先确保共享 Owner 已注册：本用例注册的是一次性账号，若它抢到了实例的
    // 首个注册者位置，后续所有需要 Owner 权限的用例都会失败。
    // 这样本用例不再依赖"自己第一个跑"，加新 spec 文件不会因排序打乱它。
    // "首个注册者成为 Owner"属于后端不变式，由 backend/tests/test_owner_bootstrap.py 覆盖。
    await ensureOwnerSession(page)
    await page.evaluate(() => localStorage.clear())

    // 监听在准备步骤之后才挂上：ensureOwnerSession 会先试登录，账号不存在时
    // 的 401 是预期内的探测，不属于被测流程。
    const browserErrors: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
    page.on('pageerror', (error) => browserErrors.push(error.message))

    const username = `signup${Date.now()}`
    await page.goto('/')
    await expect(page.getByText('Roleplex').first()).toBeVisible()
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '首次注册' }).click()
    await page.getByPlaceholder('owner').fill(username)
    await page.getByPlaceholder('我的名字').fill(`注册测试 ${username}`)
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
    await page.getByPlaceholder('再次输入密码').fill(password)
    await page.getByRole('button', { name: '创建 Owner 账号' }).click()

    // 新账号的工作台是空的：没有会话也没有角色。
    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    await expect(page.getByText(`注册测试 ${username}`)).toBeVisible()
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
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
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
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
    await expect(page.getByText(OWNER.nickname)).toBeVisible()
  })

  test('shows an authentication error for invalid credentials', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: '进入工作台' }).first().click()
    await page.getByRole('button', { name: '登录' }).click()
    await page.getByPlaceholder('owner').fill('missing_user')
    await page.getByPlaceholder('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText('AUTH_INVALID')).toBeVisible()
  })

  test('rejects protected API access without authentication', async ({ request }) => {
    const response = await request.get(`${backend}/api/model-configs`)
    expect(response.status()).toBe(401)
    expect((await response.json()).error.code).toBe('AUTH_REQUIRED')
  })
})
