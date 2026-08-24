import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from './owner'

// 后端地址由 playwright.config.ts 统一下发，端口常量不在测试里重复维护。
const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8001'

type Seeded = { conversationId: number; roleId: number; roleName: string }

/**
 * 通过 API 准备一个单聊会话，并返回后续断言需要的角色标识。
 * @param page 已登录 Owner 的页面，用于取出本地 Token。
 * @param title 会话标题，用例之间必须不同以便定位。
 */
async function seedConversation(page: Page, title: string): Promise<Seeded> {
  return page.evaluate(async ({ base, convTitle, suffix }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch(`${base}${path}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${path} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `del-fake-${suffix}`, provider_type: 'openai_compatible', api_key: 'sk-e2e-placeholder',
    })
    const roleName = `待删除助手 ${suffix}`
    const role = await post('/api/roles', {
      name: roleName, system_prompt: '你是端到端测试助手', model_config_id: config.id, model_name: 'fake-model',
    })
    const conversation = await post('/api/conversations', { type: 'single', title: convTitle, role_ids: [role.id] })
    return { conversationId: conversation.id as number, roleId: role.id as number, roleName }
  }, { base: backend, convTitle: title, suffix: `${Date.now()}` })
}

test.describe('delete semantics', () => {
  test('keeps the sender name on history after the role is deleted', async ({ page }) => {
    await ensureOwnerSession(page)
    const title = `墓碑会话 ${Date.now()}`
    const seeded = await seedConversation(page, title)

    await page.reload()
    await page.getByText(title).first().click()
    await expect(page.getByLabel('消息输入框')).toBeEnabled()
    await page.getByLabel('消息输入框').fill('删除前的对话')
    await page.getByLabel('发送消息').click()
    // 等 fake provider 的回复落定，历史里才有角色发出的消息。
    await expect(page.getByText('这是 M2 fake provider 的确定性回复。')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(seeded.roleName).first()).toBeVisible()

    await page.evaluate(async ({ base, roleId }) => {
      const token = localStorage.getItem('roleplex_token')
      const response = await fetch(`${base}/api/roles/${roleId}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(`delete role failed: ${response.status}`)
    }, { base: backend, roleId: seeded.roleId })

    await page.reload()
    await page.getByText(title).first().click()

    // 墓碑保留原名称，并在发送者旁标注已删除。
    await expect(page.getByText(seeded.roleName).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('已删除').first()).toBeVisible()
    await page.screenshot({ path: 'test-results/deleted-role-history.png', fullPage: true })

    // 墓碑不再出现在侧边栏的角色列表里；测试库由多个用例共享，
    // 因此断言"这个角色不在列表中"，而不是断言角色总数。
    await expect(page.getByTestId('sidebar-role').filter({ hasText: seeded.roleName })).toHaveCount(0)
  })

  test('moves a deleted conversation to the recycle bin and restores it', async ({ page }) => {
    await ensureOwnerSession(page)
    const title = `回收站会话 ${Date.now()}`
    const seeded = await seedConversation(page, title)

    await page.reload()
    await expect(page.getByText(title)).toBeVisible({ timeout: 15_000 })

    await page.evaluate(async ({ base, conversationId }) => {
      const token = localStorage.getItem('roleplex_token')
      const response = await fetch(`${base}/api/conversations/${conversationId}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(`delete conversation failed: ${response.status}`)
    }, { base: backend, conversationId: seeded.conversationId })

    await page.reload()
    await expect(page.getByText(title)).toBeHidden({ timeout: 15_000 })

    // 回收站里能找到它，并显示剩余保留天数。
    await page.getByRole('button', { name: '回收站' }).click()
    const entry = page.getByTestId('recycle-bin-item').filter({ hasText: title })
    await expect(entry).toBeVisible()
    await expect(entry.getByText(/还可恢复 \d+ 天/)).toBeVisible()
    await page.screenshot({ path: 'test-results/recycle-bin.png', fullPage: true })

    await entry.getByRole('button', { name: '恢复' }).click()
    await expect(entry).toBeHidden({ timeout: 15_000 })

    // 恢复后回到会话列表；刷新同时关掉弹窗，验证的是服务端状态而不是内存残留。
    await page.reload()
    await expect(page.getByText(title)).toBeVisible({ timeout: 15_000 })
  })
})
