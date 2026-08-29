import { expect, type Page } from '@playwright/test'

/**
 * 用真实 Provider 完成两轮浏览器对话，并验证历史、落库和运行模式。
 * @param page 当前浏览器页面。
 * @param options 本轮账号时间戳、API 地址、预期世界模式和截图名。
 */
export async function verifyRealProviderConversation(
  page: Page,
  options: {
    stamp: string
    apiOrigin: string
    expectedWorldManaged: boolean
    screenshotName: string
  },
) {
  const browserErrors: string[] = []
  page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  const username = `realtest${options.stamp}`
  const conversationTitle = `真实 API 验证 ${options.stamp}`
  await page.goto('/#/auth')
  await page.getByPlaceholder('owner').fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
  await page.getByRole('button', { name: '进入工作台' }).click()

  await expect(page.getByText(conversationTitle)).toBeVisible({ timeout: 20_000 })
  const health = await page.evaluate(
    async (base) => (await fetch(`${base}/api/health`)).json(),
    options.apiOrigin,
  )
  expect(health.world_managed).toBe(options.expectedWorldManaged)
  if (options.expectedWorldManaged) expect(health.world_name).toBe('default')

  await page.getByText(conversationTitle).first().click()
  await expect(page.getByLabel('消息输入框')).toBeEnabled()

  const messages = page.getByTestId('chat-message')
  const initialMessageCount = await messages.count()
  const verificationCode = `RP-${options.stamp.slice(-6)}`
  const prompt = `请记住验证码 ${verificationCode}，并简短回复已经记住。`
  await page.getByLabel('消息输入框').fill(prompt)
  await page.getByLabel('发送消息').click()
  await expect(page.getByText(prompt)).toBeVisible()
  await expect(messages).toHaveCount(initialMessageCount + 2, { timeout: 90_000 })

  const reply = messages.nth(initialMessageCount + 1)
  await expect(reply.getByText('生成中…')).toBeHidden({ timeout: 90_000 })
  await expect(reply.getByText('生成失败')).toBeHidden()
  const replyText = (await reply.textContent()) ?? ''
  expect(replyText.trim().length).toBeGreaterThan(4)
  expect(replyText).not.toContain('M2 fake provider')

  const followUp = '上一轮要求你记住的验证码是什么？请只回复验证码。'
  await page.getByLabel('消息输入框').fill(followUp)
  await page.getByLabel('发送消息').click()
  await expect(page.getByText(followUp)).toBeVisible()
  await expect(messages).toHaveCount(initialMessageCount + 4, { timeout: 90_000 })
  const secondReply = messages.nth(initialMessageCount + 3)
  await expect(secondReply.getByText('生成中…')).toBeHidden({ timeout: 90_000 })
  await expect(secondReply.getByText('生成失败')).toBeHidden()
  await expect(secondReply).toContainText(verificationCode)
  expect(browserErrors).toEqual([])

  await page.screenshot({ path: `test-results/${options.screenshotName}`, fullPage: true })
}
