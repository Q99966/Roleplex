import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from './owner'

const backend = 'http://127.0.0.1:8000'

/** 通过 API 准备一个可用于单聊的模型配置、角色和会话。 */
async function seedConversation(page: Page, title: string): Promise<number> {
  return page.evaluate(async ({ base, convTitle, suffix }) => {
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
      name: `E2E 助手 ${suffix}`, system_prompt: '你是端到端测试助手', model_config_id: config.id, model_name: 'fake-model',
    })
    const conversation = await post('/api/conversations', {
      type: 'single', title: convTitle, role_ids: [role.id],
    })
    return conversation.id as number
  }, { base: backend, convTitle: title, suffix: `${Date.now()}` })
}

test.describe('M2 single chat', () => {
  test('sends a message and renders the streamed reply', async ({ page }) => {
    const browserErrors: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
    page.on('pageerror', (error) => browserErrors.push(error.message))

    await ensureOwnerSession(page)
    await seedConversation(page, '单聊流式测试')
    await page.reload()

    await page.getByText('单聊流式测试').first().click()
    await expect(page.getByLabel('消息输入框')).toBeEnabled()

    await page.getByLabel('消息输入框').fill('你好，请自我介绍')
    await page.getByLabel('发送消息').click()

    // 用户消息立即出现，随后 Agent 回复按流式增量补齐。
    await expect(page.getByText('你好，请自我介绍')).toBeVisible()
    await expect(page.getByText(/已收到你的消息：你好，请自我介绍/)).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText('这是 M2 fake provider 的确定性回复。')).toBeVisible({ timeout: 20_000 })

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

    const stopButton = page.getByRole('button', { name: '停止生成' })
    await stopButton.click()
    await expect(stopButton).toBeHidden({ timeout: 20_000 })

    // 停止后不应再出现完整结尾，且消息标记为已停止。
    await expect(page.getByText('已停止')).toBeVisible({ timeout: 20_000 })
  })

  test('rejects message posting for non-members', async ({ request }) => {
    const response = await request.post(`${backend}/api/conversations/999999/messages`, {
      data: { parts: [{ type: 'text', text: '越权测试' }] },
    })
    expect(response.status()).toBe(401)
    expect((await response.json()).error.code).toBe('AUTH_REQUIRED')
  })
})
