import { expect, test } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createServer } from 'node:net'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('写入受阻有准确私有提示，停服后可继续，批次与 Guest 边界保持', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'write-diagnostics')
  await mkdir(root)
  await writeFile(path.join(root, 'existing.txt'), 'old')
  await writeFile(path.join(root, 'unstarted.txt'), 'old')
  const reservation = createServer()
  await new Promise<void>((resolve) => reservation.listen(0, '127.0.0.1', resolve))
  const port = (reservation.address() as { port: number }).port
  await new Promise<void>((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()))
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('拒绝诊断准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'diagnostic-model', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '诊断助手', system_prompt: '尊重实际拒绝原因，不绕过限制。', model_config_id: model.id,
      model_name: 'fake-model', builtin_tools: ['workspace_start_service', 'workspace_write', 'workspace_edit', 'workspace_service_status'] })
    const workspace = await request('/api/workspaces', { display_name: '诊断工作区', root_path: root, acknowledge_existing_content: true })
    await request('/api/runtime/config', { scope: 'workspace', scope_id: workspace.id, limit: 5, expected_revision: 0, services_enabled: true }, 'PUT')
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '写入拒绝诊断验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('写入拒绝诊断验收', { exact: true }).click()
  let runtimeId = ''
  const writes = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_write', exact: true }) })
  try {
    await page.getByLabel('消息输入框').fill(`[SERVICE_FAKE:${port}]`)
    await page.getByLabel('发送消息').click()
    await page.getByRole('button', { name: '批准后台服务启动', exact: true }).click()
    await expect(page.getByText('服务启动流程已结束，请使用 /ps 管理。', { exact: true })).toBeVisible()
    runtimeId = await page.evaluate(async (cid) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const rows = await (await fetch(`/api/conversations/${cid}/processes`, { headers })).json()
      return rows.items.find((row: { state: string }) => row.state === 'ready').id as string
    }, cid)
    await page.getByLabel('消息输入框').fill('[WRITE_DIAGNOSTIC_FAKE]')
    await page.getByLabel('发送消息').click()
    await expect(page.getByText('写入拒绝诊断流程结束。', { exact: true })).toBeVisible()
    await writes.first().scrollIntoViewIfNeeded()
    const note = writes.first().getByRole('note', { name: '写入受阻原因' })
    await expect(note).toContainText('同一工作区存在尚未结束的托管服务')
    await expect(note).toContainText(runtimeId)
    await expect(note).toContainText('workspace_service_status')
    const batch = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_edit', exact: true }) }).first()
    await batch.scrollIntoViewIfNeeded()
    await expect(batch.getByRole('note', { name: '写入受阻原因' })).toHaveCount(1)
    await expect(batch.getByText('unstarted.txt · 未执行', { exact: true })).toBeVisible()
    expect(await readFile(path.join(root, 'blocked.txt'), 'utf8').catch(() => null)).toBeNull()
    expect(await readFile(path.join(root, 'existing.txt'), 'utf8')).toBe('old')
    expect((await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
    const screenshot = testInfo.outputPath('write-diagnostic.png')
    await writes.first().screenshot({ path: screenshot })
    await testInfo.attach('Owner 写入拒绝诊断', { path: screenshot, contentType: 'image/png' })
    await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
    await page.setViewportSize({ width: 390, height: 844 })
    await writes.first().scrollIntoViewIfNeeded()
    await expect.poll(() => writes.first().evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    const narrow = testInfo.outputPath('write-diagnostic-narrow.png')
    await writes.first().screenshot({ path: narrow })
    await testInfo.attach('窄屏写入拒绝诊断', { path: narrow, contentType: 'image/png' })
    await page.setViewportSize({ width: 1280, height: 720 })
    const stopped = await page.evaluate(async ({ cid, runtimeId }) => {
      const response = await fetch(`/api/conversations/${cid}/processes/${runtimeId}/stop`, { method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
      return response.ok && (await response.json()).state === 'stopped'
    }, { cid, runtimeId })
    expect(stopped).toBe(true)
    await page.getByLabel('消息输入框').fill('[WRITE_DIAGNOSTIC_FAKE]')
    await page.getByLabel('发送消息').click()
    await expect(page.getByText('写入拒绝诊断流程结束。', { exact: true })).toHaveCount(2)
    expect(await readFile(path.join(root, 'blocked.txt'), 'utf8')).toBe('new')
    expect(await readFile(path.join(root, 'existing.txt'), 'utf8')).toBe('new')
    await writes.last().scrollIntoViewIfNeeded()
    await expect(writes.last().getByRole('region', { name: '文件差异：blocked.txt' })).toBeVisible()
    await expect(writes.last().getByRole('note', { name: '写入受阻原因' })).toHaveCount(0)
    await page.reload()
    await writes.first().scrollIntoViewIfNeeded()
    await expect(note).toContainText(runtimeId)
    await expect(note).toContainText('拒绝时状态')
  } finally {
    const cleaned = await page.evaluate(async (cid) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const response = await fetch(`/api/conversations/${cid}/processes`, { headers })
      if (!response.ok) return false
      const terminal = ['stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted']
      for (const row of (await response.json()).items) if (!terminal.includes(row.state)) {
        const stopped = await fetch(`/api/conversations/${cid}/processes/${row.id}/stop`, { method: 'POST', headers })
        if (!stopped.ok || !terminal.includes((await stopped.json()).state)) return false
      }
      return true
    }, cid)
    expect(cleaned).toBe(true)
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
  await page.getByRole('button', { name: '执行详情：workspace_write', exact: true }).first().click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('note', { name: '写入受阻原因' })).toHaveCount(0)
  expect(requests).toBe(0)
})
