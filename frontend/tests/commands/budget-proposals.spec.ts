import { test, expect } from '@playwright/test'
import { mkdir, readdir } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import { readRunEvents } from '../e2e-log-assertions'

for (const final of ['tools', 'text']) {
  test(`最后决策 ${final} 正确结束，刷新保留实际文件结果`, async ({ page }, testInfo) => {
    const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, `decision-${final}`)
    await mkdir(root)
    await ensureOwnerSession(page)
    const title = `最后决策验收-${final}`
    const cid = await page.evaluate(async ({ root, title }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const post = async (route: string, body: unknown, method = 'POST') => {
        const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
        if (!response.ok) throw new Error(`预算验证准备失败：${route} HTTP ${response.status}`)
        return response.json()
      }
      const config = await post('/api/model-configs', { name: title, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
      const role = await post('/api/roles', { name: title, system_prompt: '受控验证。', model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_write'] })
      const workspace = await post('/api/workspaces', { display_name: title, root_path: root, acknowledge_existing_content: true })
      await post(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
      return (await post('/api/conversations', { title, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id
    }, { root, title })
    await page.reload()
    await page.getByRole('button', { name: `打开会话：${title}`, exact: true }).click()
    await page.getByLabel('消息输入框').fill(final === 'tools' ? '[BUDGET_PROPOSALS_FAKE]' : '[BUDGET_FINAL_FAKE]')
    await page.getByLabel('发送消息').click()
    const reply = page.getByTestId('chat-message').nth(1)
    if (final === 'tools') await expect(reply.getByText('达到本轮决策上限', { exact: true })).toBeVisible({ timeout: 30000 })
    else await expect(reply.getByText('预算边界任务完成。', { exact: true })).toBeVisible({ timeout: 30000 })
    const expectedCount = final === 'tools' ? 9 : 7
    const cards = reply.getByTestId('tool-call-card')
    await expect(cards).toHaveCount(expectedCount)
    await expect(cards.last()).toContainText('已完成')
    await expect(reply.getByText('未执行', { exact: true })).toHaveCount(0)
    const names = Array.from({ length: 7 }, (_, i) => `created-${i}.txt`)
    if (final === 'tools') names.push('last-0.txt', 'last-1.txt')
    expect((await readdir(root)).sort()).toEqual(names.sort())
    await cards.last().scrollIntoViewIfNeeded()
    const detailsButton = cards.last().getByRole('button', { name: '执行详情：workspace_write' })
    if (await detailsButton.getAttribute('aria-expanded') !== 'true') await detailsButton.click()
    await expect(cards.last().getByRole('region', { name: '本次文件变更' })).toBeVisible()
    const screenshot = testInfo.outputPath(`last-decision-${final}.png`)
    await cards.last().scrollIntoViewIfNeeded()
    await page.screenshot({ path: screenshot })
    await testInfo.attach('最后决策真实结果', { path: screenshot, contentType: 'image/png' })
    await page.reload()
    await expect(cards).toHaveCount(expectedCount)
    const saved = await page.evaluate(async cid => {
      const response = await fetch(`/api/conversations/${cid}/messages`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
      const message = (await response.json()).items.find((item: { sender_type: string }) => item.sender_type === 'role')
      return { reason: message.stop_reason, status: message.status }
    }, cid)
    expect(saved.reason).toBe(final === 'tools' ? 'decision_budget' : null)
    expect(saved.status).toBe(final === 'tools' ? 'stopped' : 'done')
    const events = (await readRunEvents()).filter(event => event.conversation_id === cid)
    expect(events.filter(event => event.event === 'tool.call_started')).toHaveLength(expectedCount)
    expect(events.filter(event => event.event === 'tool.call_not_dispatched')).toHaveLength(0)
    expect(events.filter(event => event.event === 'provider.call_started')).toHaveLength(8)
    await expect(page.getByRole('region', { name: '系统执行记录' })).toHaveCount(0)
  })
}
