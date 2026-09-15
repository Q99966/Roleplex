import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from './owner'

// 后端地址由 playwright.config.ts 统一下发，端口常量不在测试里重复维护。
const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8001'

/**
 * 通过 API 准备一个可用于单聊的模型配置、角色和会话。
 * @param page 当前浏览器页面。
 * @param title 会话标题。
 * @param options 可选角色 system 与上下文窗口。
 */
async function seedConversation(
  page: Page,
  title: string,
  options: { systemPrompt?: string; contextWindowTokens?: number } = {},
): Promise<number> {
  return page.evaluate(async ({ base, convTitle, suffix, systemPrompt, contextWindowTokens }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch(`${base}${path}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${path} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `e2e-fake-${suffix}`, provider_type: 'openai_compatible', api_key: 'sk-e2e-placeholder',
    })
    // 角色名在同一 Owner 下唯一，因此按调用附加后缀。
    const role = await post('/api/roles', {
      name: `E2E 助手 ${suffix}`,
      system_prompt: systemPrompt,
      model_config_id: config.id,
      model_name: 'fake-model',
      context_window_tokens: contextWindowTokens,
    })
    const conversation = await post('/api/conversations', {
      type: 'single', title: convTitle, role_ids: [role.id],
    })
    return conversation.id as number
  }, {
    base: backend,
    convTitle: title,
    suffix: `${Date.now()}`,
    systemPrompt: options.systemPrompt ?? '你是端到端测试助手',
    contextWindowTokens: options.contextWindowTokens ?? 200_000,
  })
}

test.describe('M2 single chat', () => {
  test('sends a message and renders the streamed reply', async ({ page }) => {
    const browserErrors: string[] = []
    const websocketUrls: string[] = []
    const historyRequests: string[] = []

    await ensureOwnerSession(page)
    await seedConversation(page, '单聊流式测试')

    // 账号不存在时准备步骤会先产生一次预期 401，监听必须在准备完成后挂载。
    page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
    page.on('pageerror', (error) => browserErrors.push(error.message))
    page.on('websocket', (socket) => {
      if (socket.url().endsWith('/api/ws')) websocketUrls.push(socket.url())
    })
    page.on('request', (request) => {
      if (request.method() === 'GET' && /\/api\/conversations\/\d+\/messages$/.test(new URL(request.url()).pathname)) {
        historyRequests.push(request.url())
      }
    })

    await page.reload()

    await page.getByText('单聊流式测试').first().click()
    await expect(page.getByLabel('消息输入框')).toBeEnabled()

    await page.getByLabel('消息输入框').fill('你好，请自我介绍')
    await page.getByLabel('发送消息').click()

    // 用户消息立即出现，随后 Agent 回复按流式增量补齐。
    await expect(page.getByText('你好，请自我介绍')).toBeVisible()
    await expect(page.getByText(/已收到你的消息：你好，请自我介绍/)).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText('这是 M2 fake provider 的确定性回复。')).toBeVisible({ timeout: 20_000 })

    // React StrictMode 会重复执行 effect；会话打开逻辑必须抵消过期异步调用，
    // 一个页面、一个会话只能读取一次历史并保留一条 WebSocket。
    expect(historyRequests).toHaveLength(1)
    expect(websocketUrls).toHaveLength(1)

    // 刷新后历史仍然完整，证明内容已经落库而不是只存在于内存。
    await page.reload()
    await expect(page.getByText(/已收到你的消息：你好，请自我介绍/)).toBeVisible({ timeout: 20_000 })

    await page.screenshot({ path: 'test-results/m2-single-chat.png', fullPage: true })
    expect(browserErrors).toEqual([])
  })

  test('stops an in-flight generation and keeps partial content', async ({ page }) => {
    await ensureOwnerSession(page)
    await seedConversation(page, '停止生成测试')
    await page.reload()

    await page.getByText('停止生成测试').first().click()
    await page.getByLabel('消息输入框').fill('请开始一段较长的回复')
    await page.getByLabel('发送消息').click()

    const stopButton = page.getByRole('button', { name: '停止生成', exact: true })
    await stopButton.click()
    await expect(stopButton).toBeHidden({ timeout: 20_000 })

    // 停止后不应再出现完整结尾，且消息标记为已停止。
    await expect(page.getByText('已停止', { exact: true })).toBeVisible({ timeout: 20_000 })
    await expect(page.getByRole('region', { name: '系统执行记录' })).toHaveCount(0)
  })

  test('rejects message posting for non-members', async ({ request }) => {
    const response = await request.post(`${backend}/api/conversations/999999/messages`, {
      data: { parts: [{ type: 'text', text: '越权测试' }] },
    })
    expect(response.status()).toBe(401)
    expect((await response.json()).error.code).toBe('AUTH_REQUIRED')
  })

  test('configures a 200K role context window with presets', async ({ page }) => {
    await ensureOwnerSession(page)
    await page.evaluate(async ({ base, suffix }) => {
      const token = localStorage.getItem('roleplex_token')
      const response = await fetch(`${base}/api/model-configs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          name: `context-ui-${suffix}`,
          provider_type: 'openai_compatible',
          api_key: 'sk-e2e-placeholder',
        }),
      })
      if (!response.ok) throw new Error(`model config failed: ${response.status}`)
    }, { base: backend, suffix: `${Date.now()}` })
    await page.reload()

    await page.getByRole('tab', { name: '角色', exact: true }).click()
    await page.getByTitle('定制 Agent 角色').click()
    const contextInput = page.getByLabel('上下文窗口 tokens')
    await expect(contextInput).toHaveValue('200000')
    const oneMillion = page.getByRole('button', { name: '1M', exact: true })
    await oneMillion.click()
    await expect(contextInput).toHaveValue('1000000')
    await expect(oneMillion).toHaveClass(/bg-indigo-600/)
    await expect(page.getByText(/服务上限 2,000,000 · 有效 1,000,000/)).toBeVisible()
    await page.screenshot({ path: 'test-results/context-window-role-modal.png', fullPage: true })
  })

  test('shows an Owner-safe message when minimum context exceeds the window', async ({ page }) => {
    await ensureOwnerSession(page)
    await seedConversation(page, '上下文预算测试', {
      contextWindowTokens: 4096,
      systemPrompt: '必须遵守的角色约束'.repeat(1200),
    })
    await page.reload()

    await page.getByText('上下文预算测试').first().click()
    await page.getByLabel('消息输入框').fill('不能被静默截断的当前消息')
    await page.getByLabel('发送消息').click()
    await expect(page.getByText(/当前消息与角色基础配置超过模型上下文上限/)).toBeVisible({ timeout: 20_000 })
    await page.screenshot({ path: 'test-results/context-budget-error.png', fullPage: true })
  })

  test('renders markdown formatting and code blocks in AI replies', async ({ page }) => {
    await ensureOwnerSession(page)
    await seedConversation(page, 'Markdown 格式渲染测试')
    await page.reload()

    await page.getByText('Markdown 格式渲染测试').first().click()
    await expect(page.getByLabel('消息输入框')).toBeEnabled()

    // 发送包含 Markdown 标记的提示词，fake provider 会回显该文本并由前端 MarkdownRenderer 渲染
    await page.getByLabel('消息输入框').fill('**加粗内容** 与 `const answer = 42`')
    await page.getByLabel('发送消息').click()

    // 验证 AI 回复中的 Markdown HTML 节点正确生成
    const replyBubble = page.getByTestId('chat-message').last()
    await expect(replyBubble).toContainText('这是 M2 fake provider 的确定性回复。', { timeout: 20_000 })
    await expect(replyBubble.locator('strong')).toHaveText('加粗内容')
    await expect(replyBubble.locator('code')).toContainText('const answer = 42')

    await page.getByLabel('消息输入框').fill('[危险链接](javascript:alert(1)) <script>window.__roleplex_xss = true</script>')
    await page.getByLabel('发送消息').click()
    const securityReply = page.getByTestId('chat-message').last()
    await expect(securityReply.getByRole('link', { name: '危险链接' })).toHaveAttribute('href', '#', { timeout: 20_000 })
    await expect(securityReply.locator('script')).toHaveCount(0)
    await expect(securityReply).toContainText('<script>window.__roleplex_xss = true</script>')

    await page.screenshot({ path: 'test-results/m2-markdown-render.png', fullPage: true })
  })
})
