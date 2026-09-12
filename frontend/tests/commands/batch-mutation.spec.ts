import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('批量写入编辑独立文件 diff、拒绝不重写、刷新与 Guest 隔离', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'batch-mutation')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('批量修改准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'batch-mutation', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '批量修改助手', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '修改受控文件。', builtin_tools: ['workspace_write', 'workspace_edit'] })
    const workspace = await request('/api/workspaces', { display_name: '批量修改工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '批量修改验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('批量修改验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[BATCH_MUTATION_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('批量修改验收完成。', { exact: true })).toBeVisible()
  const create = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_write', exact: true }) }).first()
  await create.scrollIntoViewIfNeeded()
  await expect(create.getByRole('region', { name: '文件差异：batch-a.txt', exact: true })).toContainText('alpha old')
  const edit = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_edit', exact: true }) }).first()
  await edit.scrollIntoViewIfNeeded()
  const batch = edit.getByRole('region', { name: '本次批量修改', exact: true })
  await expect(batch.locator('summary')).toHaveCount(2)
  await expect(batch.getByRole('region', { name: '文件差异：batch-a.txt', exact: true })).toContainText('alpha new')
  await expect(batch.getByRole('region', { name: '文件差异：batch-b.txt', exact: true })).toContainText('beta new')
  await batch.locator('summary').first().click()
  await expect(batch.getByRole('region', { name: '文件差异：batch-a.txt', exact: true })).not.toBeVisible()
  await expect(batch.getByRole('region', { name: '文件差异：batch-b.txt', exact: true })).toBeVisible()
  await batch.locator('summary').first().click()
  await expect(edit.getByText('原始输入与输出')).toHaveCount(0)
  expect(await readFile(path.join(root, 'batch-a.txt'), 'utf8')).toBe('alpha new')
  expect(await readFile(path.join(root, 'batch-b.txt'), 'utf8')).toBe('beta new')
  const screenshot = testInfo.outputPath('batch-mutation.png')
  await edit.screenshot({ path: screenshot })
  await testInfo.attach('批量修改的文件差异', { path: screenshot, contentType: 'image/png' })
  await page.reload()
  await edit.scrollIntoViewIfNeeded()
  await expect(batch.getByRole('region', { name: '文件差异：batch-a.txt', exact: true })).toContainText('alpha new')
  await page.getByLabel('消息输入框').fill('[BATCH_MUTATION_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('批量修改验收完成。', { exact: true })).toHaveCount(2)
  const rejected = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_write', exact: true }) }).last()
  await rejected.scrollIntoViewIfNeeded()
  await expect(rejected.getByText('批次未通过写前检查，未开始修改。')).toBeVisible()
  await expect(rejected.getByText('batch-b.txt · 未执行', { exact: true })).toBeVisible()
  expect(await readFile(path.join(root, 'batch-a.txt'), 'utf8')).toBe('alpha new')
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await edit.scrollIntoViewIfNeeded()
  await expect.poll(() => edit.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const narrow = testInfo.outputPath('batch-mutation-narrow.png')
  await edit.screenshot({ path: narrow })
  await testInfo.attach('窄屏批量差异', { path: narrow, contentType: 'image/png' })
  await page.setViewportSize({ width: 1280, height: 720 })
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
  await expect(page.getByText('batch-a.txt', { exact: false })).toHaveCount(0)
  expect(requests).toBe(0)
})
