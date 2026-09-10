import { expect, test } from '@playwright/test'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { ensureOwnerSession } from '../owner'

test('工具在正文中原位更新，Owner 可展开输入输出且刷新后顺序保持', async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    const Original = window.WebSocket
    const sockets: WebSocket[] = []
    ;(window as any).__timelineSockets = sockets
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        sockets.push(this)
      }
    }
  })
  await ensureOwnerSession(page)
  const title = '工具时间线验收'
  const conversationId = await page.evaluate(async ({ root, title }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`Timeline setup failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', { name: title, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: title, model_config_id: config.id, model_name: 'fake-model',
      system_prompt: '使用工具完成任务。', builtin_tools: ['workspace_run_command'] })
    const workspace = await post('/api/workspaces', { display_name: title, root_path: root, acknowledge_existing_content: true })
    await post(`/api/workspaces/${workspace.id}`, { basic_commands_enabled: true }, 'PATCH')
    return (await post('/api/conversations', { title, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, { root: path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'timeline'), title })
  await page.reload()
  await page.getByText(title, { exact: true }).first().click()
  await page.getByLabel('消息输入框').fill('[TOOL_TIMELINE_FAKE]')
  await page.getByLabel('发送消息').click()
  const reply = page.getByTestId('chat-message').nth(1)
  await expect(reply.getByTestId('tool-call-card').first()).toBeVisible()
  await page.evaluate(() => (window as any).__timelineSockets.at(-1).close())
  await expect(reply.getByText('处理完成。', { exact: true })).toBeVisible()
  const sequence = reply.locator('[data-testid="message-text-part"], [data-testid="tool-call-card"]')
  await expect(sequence).toHaveCount(5)
  expect(await sequence.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-testid'))))
    .toEqual(['message-text-part', 'tool-call-card', 'message-text-part', 'tool-call-card', 'message-text-part'])
  await page.screenshot({ path: 'test-results/tool-timeline-order.png', fullPage: true })
  const readCard = reply.getByTestId('tool-call-card').first()
  const button = readCard.getByRole('button', { name: '执行详情：workspace_run_command · read', exact: true })
  await expect(button).toHaveAttribute('aria-expanded', 'false')
  await button.click()
  await expect(readCard.getByRole('region', { name: '工具输入' })).toContainText('hello.txt')
  await expect(readCard.getByRole('region', { name: '工具输出' })).toContainText('TIMELINE-PRIVATE-PLACEHOLDER')
  await page.screenshot({ path: 'test-results/tool-timeline-expanded.png', fullPage: true })
  await button.click()
  await expect(readCard.getByRole('region', { name: '工具输出' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('chat-message').nth(1).getByTestId('message-text-part')).toHaveCount(3)
  await page.getByRole('button', { name: '执行详情：workspace_run_command · read', exact: true }).click()
  await expect(page.getByRole('region', { name: '工具输出' })).toContainText('TIMELINE-PRIVATE-PLACEHOLDER')
  const detailRoute = await page.evaluate(async (id) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const history = await (await fetch(`/api/conversations/${id}/messages`, { headers })).json()
    const message = history.items.find((item: { sender_type: string }) => item.sender_type === 'role')
    const call = message.parts_json.find((part: { type: string }) => part.type === 'tool_call')
    return `/api/conversations/${id}/messages/${message.id}/tools/${call.call_id}`
  }, conversationId)
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(conversationId)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.getByRole('button', { name: '退出登录', exact: true }).first().click()
  await page.goto('/#/auth')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByPlaceholder('owner').fill(`test${process.env.ROLEPLEX_E2E_STAMP}_toolviewer`)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Test-1234')
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
  await page.getByText(title, { exact: true }).first().click()
  let detailRequests = 0
  page.on('request', (request) => { if (request.url().includes('/tools/')) detailRequests += 1 })
  await page.getByRole('button', { name: '执行详情：workspace_run_command · read', exact: true }).click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '工具输出' })).toHaveCount(0)
  expect(detailRequests).toBe(0)
  const denied = await page.evaluate(async (route) => (await fetch(route, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).status, detailRoute)
  expect(denied).toBe(403)
  await testInfo.attach('工具时间线截图', { path: 'test-results/tool-timeline-order.png', contentType: 'image/png' })
})
