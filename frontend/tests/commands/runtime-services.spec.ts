import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

/** 只在本轮准备阶段选择端口，服务运行时不允许静默换端口。 */
async function availablePort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as { port: number }).port
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  return port
}

test('后台服务回复后仍可访问，/ps 不调用模型并能读取日志和停止', async ({ page }, testInfo) => {
  const browserErrors: string[] = []
  page.on('pageerror', (error) => browserErrors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && message.text().includes('same key')) browserErrors.push('Duplicate React child key') })
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'runtime-services')
  await mkdir(root)
  await writeFile(path.join(root, 'index.html'), '<h1>Hello runtime services</h1>')
  const port = await availablePort()
  await ensureOwnerSession(page)
  await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body?: unknown, method = 'POST') => {
      const response = await fetch(route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
      if (!response.ok) throw new Error('Runtime fixture failed')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: '服务模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '服务助手', system_prompt: '启动服务后使用登记工具管理。', model_config_id: config.id,
      model_name: 'fake-model', builtin_tools: ['workspace_start_service', 'workspace_service_status', 'workspace_service_logs', 'workspace_stop_service'] })
    const workspace = await request('/api/workspaces', { display_name: '服务工作区', root_path: root, acknowledge_existing_content: true })
    await request('/api/runtime/config', { scope: 'workspace', scope_id: workspace.id, limit: 5, expected_revision: 0, services_enabled: true }, 'PUT')
    await request('/api/conversations', { title: '后台服务验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })
  }, root)
  await page.reload()
  await page.getByText('后台服务验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill(`[SERVICE_FAKE:${port}]`)
  await page.getByLabel('发送消息').click()
  await page.getByRole('button', { name: '批准后台服务启动', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止生成', exact: true })).toHaveCount(0)
  await expect.poll(async () => (await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
  await page.reload()
  let modelRequests = 0
  page.on('request', (request) => { if (request.method() === 'POST' && /\/messages$/.test(new URL(request.url()).pathname)) modelRequests++ })
  await page.getByLabel('消息输入框').fill('/ps')
  await page.getByLabel('消息输入框').press('Enter')
  const panel = page.getByRole('dialog', { name: '会话进程与详情' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('就绪', { exact: true })).toBeVisible()
  expect(modelRequests).toBe(0)
  await expect(panel.getByLabel('会话进程上限')).toHaveValue('3')
  await panel.getByLabel('会话进程上限').fill('4')
  await panel.getByRole('button', { name: '保存会话进程上限' }).click()
  await expect(panel.getByText(/已用及预留 1\/4/)).toBeVisible()
  await panel.getByRole('button', { name: /^查看日志 / }).first().click()
  await expect(panel.getByRole('region', { name: '服务日志' })).toContainText('GET /')
  const screenshot = testInfo.outputPath('runtime-panel.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('会话进程面板', { path: screenshot, contentType: 'image/png' })
  await panel.getByRole('button', { name: /^停止 / }).first().click()
  await expect(panel.getByText('已停止', { exact: true })).toBeVisible()
  await expect(panel.getByText(/已用及预留 0\/4/)).toBeVisible()
  await expect.poll(async () => page.request.get(`http://127.0.0.1:${port}`, { timeout: 1000 }).then(() => false).catch(() => true)).toBe(true)
  await panel.getByRole('button', { name: '关闭进程面板' }).click()
  expect(browserErrors).toEqual([])
  await expect(panel).not.toBeVisible()
  await page.getByLabel('消息输入框').fill(`[SERVICE_FAKE:${port}]`)
  await page.getByLabel('发送消息').click()
  await page.getByRole('button', { name: '拒绝后台服务启动', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止生成', exact: true })).toHaveCount(0)
  await page.getByLabel('消息输入框').fill('/ps')
  await page.getByLabel('消息输入框').press('Enter')
  await expect(panel.getByText('已拒绝', { exact: true })).toBeVisible()
  await expect.poll(async () => page.request.get(`http://127.0.0.1:${port}`, { timeout: 1000 }).then(() => false).catch(() => true)).toBe(true)
})
