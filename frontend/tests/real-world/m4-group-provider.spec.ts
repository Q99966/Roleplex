import { expect, test } from '@playwright/test'
import { expectManagedWorldLayout, readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

test('runs two real provider roles serially inside one managed-world chain', async ({ page }) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP ?? 'missing'
  const username = `realtest${stamp}`
  const roleNames = [`真实群聊角色 A ${stamp}`, `真实群聊角色 B ${stamp}`]
  const collaborationCode = `RG-${stamp.slice(-6)}`
  const prompt = '请按角色职责完成协作：A 先给出内部协作码，B 再复述 A 刚刚给出的协作码。'

  await page.goto('/#/auth')
  await page.getByPlaceholder('owner').fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByText(`真实群聊验证 ${stamp}`)).toBeVisible({ timeout: 20_000 })
  await expectManagedWorldLayout((process.env.ROLEPLEX_E2E_WORLDS ?? '').split(',')[0])

  await page.getByText(`真实群聊验证 ${stamp}`).first().click()
  const input = page.getByLabel('消息输入框')
  for (const roleName of roleNames) {
    await input.fill(`${await input.inputValue()}@`)
    await page.getByRole('option', { name: `@${roleName}` }).click()
  }
  await input.fill(`${await input.inputValue()}${prompt}`)
  await page.getByLabel('发送消息').click()

  const messages = page.getByTestId('chat-message')
  await expect(messages).toHaveCount(3, { timeout: 120_000 })
  await expect(messages.nth(1).getByText('生成中…')).toBeHidden({ timeout: 90_000 })
  await expect(messages.nth(1)).toContainText(roleNames[0])
  await expect(messages.nth(1)).toContainText(collaborationCode, { timeout: 60_000 })
  await expect(messages.nth(2).getByText('生成中…')).toBeHidden({ timeout: 90_000 })
  await expect(messages.nth(2)).toContainText(roleNames[1])
  await expect(messages.nth(2)).toContainText(collaborationCode, { timeout: 60_000 })

  const contexts = (await waitForRunEvents((event) => event.event === 'context.loaded', 2)).slice(-2)
  expect(contexts.map((event) => event.context_message_count)).toEqual([0, 1])
  expect(contexts[1].runtime_prefix_hash).toBe(contexts[0].runtime_prefix_hash)
  expect(contexts[1].conversation_prefix_hash).toBe(contexts[0].conversation_prefix_hash)
  expect(contexts[1].role_prefix_hash).not.toBe(contexts[0].role_prefix_hash)

  const calls = (await waitForRunEvents(
    (event) => event.event === 'provider.call_completed' && event.provider_mode === 'real',
    2,
  )).slice(-2)
  expect(calls[0].chain_id).toBe(calls[1].chain_id)
  expect(calls[0].execution_id).not.toBe(calls[1].execution_id)
  expect(calls.every((event) => typeof event.input_tokens === 'number')).toBe(true)
  expect(calls.every((event) => typeof event.base_url === 'string')).toBe(true)
  expect(calls.every((event) => ['configured', 'default'].includes(String(event.base_url_source)))).toBe(true)
  const serialized = JSON.stringify(await readRunEvents())
  expect(serialized).not.toContain(prompt)
  expect(serialized).not.toContain(collaborationCode)

  await page.screenshot({ path: 'test-results/m4-real-group.png', fullPage: true })
})
