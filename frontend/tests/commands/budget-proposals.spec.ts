import { test, expect } from '@playwright/test'
import { mkdir, readdir } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import { readRunEvents } from '../e2e-log-assertions'

test('预算停止显示确定未派发的工具，刷新保留已写文件与未执行事实', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'budget-proposals')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async root => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('预算验证准备失败')
      return response.json()
    }
    const config = await post('/api/model-configs', { name: 'budget', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: '预算验证助手', system_prompt: '受控验证。', model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_write'] })
    const workspace = await post('/api/workspaces', { display_name: '预算验证工作区', root_path: root, acknowledge_existing_content: true })
    await post(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await post('/api/conversations', { title: '预算未派发验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id
  }, root)
  await page.reload()
  await page.getByRole('button', { name: '打开会话：预算未派发验收', exact: true }).click()
  await page.getByLabel('消息输入框').fill('[BUDGET_PROPOSALS_FAKE]')
  await page.getByLabel('发送消息').click()
  const reply = page.getByTestId('chat-message').nth(1)
  await expect(reply.getByText('达到本轮执行步数上限', { exact: true })).toBeVisible({ timeout: 30000 })
  const cards = reply.getByTestId('tool-call-card')
  await expect(cards).toHaveCount(9)
  await expect(cards.nth(7)).toContainText('未执行')
  await cards.nth(7).getByRole('button', { name: '执行详情：workspace_write' }).click()
  await expect(cards.nth(7)).toContainText('此工具未派发，未执行任何操作')
  await expect(cards.nth(7)).toContainText('记录时间')
  await expect(cards.nth(7)).not.toContainText('开始：')
  await expect(cards.nth(7)).not.toContainText('耗时')
  expect((await readdir(root)).sort()).toEqual(Array.from({ length: 7 }, (_, i) => `created-${i}.txt`))
  await cards.nth(7).scrollIntoViewIfNeeded()
  const screenshot = testInfo.outputPath('budget-not-dispatched.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('已提交与未派发调用', { path: screenshot, contentType: 'image/png' })
  await page.reload()
  await expect(cards).toHaveCount(9)
  await expect(cards.nth(8)).toContainText('未执行')
  const events = (await readRunEvents()).filter(event => event.conversation_id === cid)
  expect(events.filter(event => event.event === 'tool.call_started')).toHaveLength(7)
  expect(events.filter(event => event.event === 'tool.call_not_dispatched')).toHaveLength(2)
  expect(events.filter(event => event.event === 'provider.call_started')).toHaveLength(8)
  expect(JSON.stringify(events)).not.toContain('must-not-write')
  expect(JSON.stringify(events)).not.toContain('not-created-0.txt')
  await expect(page.getByRole('region', { name: '系统执行记录' })).toHaveCount(0)
})
