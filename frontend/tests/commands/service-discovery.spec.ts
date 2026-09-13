import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createServer } from 'node:net'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('Agent 无 ID 发现当前服务，Owner 可查看列表且 Guest 不读取私有详情', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'service-discovery')
  await mkdir(root)
  await writeFile(path.join(root, 'index.html'), '<h1>Service discovery fixture</h1>')
  const reservation = createServer()
  await new Promise<void>((resolve) => reservation.listen(0, '127.0.0.1', resolve))
  const port = (reservation.address() as { port: number }).port
  await new Promise<void>((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()))
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown) => {
      const response = await fetch(route, { method: route === '/api/runtime/config' ? 'PUT' : 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('服务发现准备失败')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: 'discovery-model', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '服务查询助手', system_prompt: '使用登记工具查询当前服务。', model_config_id: config.id,
      model_name: 'fake-model', builtin_tools: ['workspace_start_service', 'workspace_service_status'] })
    const workspace = await request('/api/workspaces', { display_name: '服务查询工作区', root_path: root, acknowledge_existing_content: true })
    await request('/api/runtime/config', { scope: 'workspace', scope_id: workspace.id, limit: 5, expected_revision: 0, services_enabled: true })
    return (await request('/api/conversations', { title: '服务发现验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('服务发现验收', { exact: true }).click()
  const status = page.getByRole('button', { name: '执行详情：workspace_service_status', exact: true })
  let runtimeId = ''
  try {
    await page.getByLabel('消息输入框').fill(`[SERVICE_FAKE:${port}]`)
    await page.getByLabel('发送消息').click()
    await page.getByRole('button', { name: '批准后台服务启动', exact: true }).click()
    await expect(page.getByText('服务启动流程已结束，请使用 /ps 管理。', { exact: true })).toBeVisible()
    runtimeId = await page.evaluate(async (cid) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const values = await (await fetch(`/api/conversations/${cid}/processes`, { headers })).json()
      return values.items.find((row: { state: string }) => row.state === 'ready').id as string
    }, cid)
    expect(await page.evaluate(async ({ cid, runtimeId }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`/api/conversations/${cid}/messages`, { headers })).json()
      return JSON.stringify(history.items.map((row: { parts_json: unknown }) => row.parts_json)).includes(runtimeId)
    }, { cid, runtimeId })).toBe(false)
    await page.getByLabel('消息输入框').fill('[SERVICE_DISCOVERY_FAKE]')
    await page.getByLabel('发送消息').click()
    await expect(page.getByText('当前会话服务查询完成。', { exact: true })).toBeVisible()
    await status.click()
    const card = page.getByTestId('tool-call-card').filter({ has: status })
    await expect(card.getByRole('region', { name: '工具输入', exact: true })).toHaveText('{}')
    await expect(card.getByRole('region', { name: '工具输出', exact: true })).toContainText(runtimeId)
    await expect(card.getByRole('region', { name: '工具输出', exact: true })).toContainText('"state":"ready"')
    expect((await page.request.get(`http://127.0.0.1:${port}/`)).status()).toBe(200)
    const screenshot = testInfo.outputPath('service-discovery.png')
    await card.screenshot({ path: screenshot })
    await testInfo.attach('当前会话服务发现', { path: screenshot, contentType: 'image/png' })
    await page.reload()
    await status.click()
    await expect(card.getByRole('region', { name: '工具输出', exact: true })).toContainText(runtimeId)
  } finally {
    // 无论查询断言是否成功，都经本轮 Owner 的正常停止路径收口，不能靠包装器退出掩盖失败。
    const cleanup = await page.evaluate(async (cid) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const response = await fetch(`/api/conversations/${cid}/processes`, { headers })
      if (!response.ok) return false
      const terminal = ['stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted']
      for (const row of (await response.json()).items) {
        if (!terminal.includes(row.state)) {
          const result = await fetch(`/api/conversations/${cid}/processes/${row.id}/stop`, { method: 'POST', headers })
          if (!result.ok || !terminal.includes((await result.json()).state)) return false
        }
      }
      return true
    }, cid)
    expect(cleanup).toBe(true)
    await expect.poll(() => page.request.get(`http://127.0.0.1:${port}`, { timeout: 1000 }).then(() => false).catch(() => true)).toBe(true)
  }
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  let requests = 0
  page.on('request', (request) => { if (request.url().includes('/tools/')) requests++ })
  await page.reload()
  await status.click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '工具输出', exact: true })).toHaveCount(0)
  expect(requests).toBe(0)
})
