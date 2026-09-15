import { expect, test, type Page } from '@playwright/test'
import { ensureOwnerSession } from './owner'

const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8001'

/**
 * 通过 API 创建三个 fake 角色，群聊本身仍通过真实界面创建。
 * @param page 当前浏览器页面。
 * @returns 按 A、B、C 排列的角色 ID 与名称。
 */
async function seedGroupRoles(page: Page): Promise<Array<{ id: number; name: string }>> {
  return page.evaluate(async ({ base, suffix }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (route: string, body: unknown) => {
      const response = await fetch(`${base}${route}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${route} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `m4-group-${suffix}`,
      provider_type: 'openai_compatible',
      api_key: 'sk-e2e-placeholder',
    })
    const roles = []
    for (const marker of ['A', 'B', 'C']) {
      roles.push(await post('/api/roles', {
        name: `群聊角色${marker}-${suffix}`,
        system_prompt: `你是群聊角色 ${marker}`,
        model_config_id: config.id,
        model_name: 'fake-model',
      }))
    }
    return roles.map((role) => ({ id: role.id as number, name: role.name as string }))
  }, { base: backend, suffix: `${Date.now()}` })
}

test('creates a group, keeps no-mention messages quiet, and runs explicit mentions in order', async ({ page }) => {
  await ensureOwnerSession(page)
  const roles = await seedGroupRoles(page)
  await page.reload()

  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByRole('button', { name: /群聊.*协同多个 Agent/ }).click()
  await page.getByPlaceholder(/极速网页重构/).fill('M4a 群聊验收')
  for (const role of roles.slice(0, 2)) {
    await page.getByText(role.name, { exact: true }).last().click()
  }
  await page.getByRole('button', { name: '确认开启会话' }).click()
  await expect(page.getByRole('heading', { name: 'M4a 群聊验收' })).toBeVisible()

  const input = page.getByLabel('消息输入框')
  const messages = page.getByTestId('chat-message')
  await input.fill('这条消息不触发任何角色')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('这条消息不触发任何角色')).toBeVisible()
  await page.waitForTimeout(900)
  await expect(messages).toHaveCount(1)

  // 成员管理把 C 加到末尾，稳定顺序继续保持 A、B、C。
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '会话成员', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: '管理群聊成员', exact: true }).click()
  await page.getByRole('button', { name: roles[2].name }).click()
  await page.getByRole('button', { name: '保存成员' }).click()
  await expect(page.getByText(`会话成员: ${roles.map((role) => role.name).join(', ')}`)).toBeVisible()

  await input.fill('@')
  await page.getByRole('option', { name: `@${roles[0].name}` }).click()
  await input.fill(`${await input.inputValue()}@`)
  await page.getByRole('option', { name: `@${roles[1].name}` }).click()
  await input.fill(`${await input.inputValue()}请依次回复`)
  await page.getByLabel('发送消息').click()

  await expect(page.getByRole('button', { name: /停止整条链/ })).toBeVisible()
  await expect(messages).toHaveCount(4, { timeout: 20_000 })
  await expect(messages.nth(2)).toContainText(roles[0].name)
  await expect(messages.nth(3)).toContainText(roles[1].name)
  await expect(page.getByRole('button', { name: '发送消息' })).toBeVisible({ timeout: 20_000 })
  await page.screenshot({ path: 'test-results/m4-group-mentions.png', fullPage: true })
})
