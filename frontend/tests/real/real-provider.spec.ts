import { test, expect } from '@playwright/test'

const STAMP = process.env.ROLEPLEX_REAL_E2E_STAMP ?? 'missing'
const OWNER = {
  username: `realtest${STAMP}`,
  password: 'Roleplex-Real-E2E-1',
}
const CONVERSATION_TITLE = `真实 API 验证 ${STAMP}`

test('sends a browser message through the real provider and persists the reply', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('console', (message) => { if (message.type() === 'error') browserErrors.push(message.text()) })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.goto('/#/auth')
  await page.getByPlaceholder('owner').fill(OWNER.username)
  await page.getByPlaceholder('密码', { exact: true }).fill(OWNER.password)
  await page.getByRole('button', { name: '进入工作台' }).click()

  await expect(page.getByText(CONVERSATION_TITLE)).toBeVisible({ timeout: 20_000 })
  await page.getByText(CONVERSATION_TITLE).first().click()
  await expect(page.getByLabel('消息输入框')).toBeEnabled()

  const messages = page.getByTestId('chat-message')
  const initialMessageCount = await messages.count()
  const prompt = `真实 API 连通验证 ${STAMP}`
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
  expect(browserErrors).toEqual([])

  await page.screenshot({ path: 'test-results/real-provider-chat.png', fullPage: true })
})
