import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('批量读取按文件独立展开，部分失败、历史版本及 Guest 隔离', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'read-many')
  await mkdir(root)
  await writeFile(path.join(root, 'first.txt'), 'FIRST-PRIVATE-PLACEHOLDER')
  await writeFile(path.join(root, 'second.txt'), '第二个文件🙂')
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('批量读取准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'read-many', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '批量读取助手', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '读取文件。', builtin_tools: ['workspace_read'] })
    const workspace = await request('/api/workspaces', { display_name: '批量读取工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '批量读取验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('批量读取验收', { exact: true }).click()
  let reads = 0
  page.on('request', (request) => { if (request.url().includes('/tools/')) reads++ })
  await page.getByLabel('消息输入框').fill('[READ_MANY_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('批量读取验收完成。', { exact: true })).toBeVisible()
  expect(reads).toBe(0)
  const card = page.getByTestId('tool-call-card').first()
  await expect(card.getByText('部分完成', { exact: true })).toBeVisible()
  await card.getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  const batch = card.getByRole('region', { name: '本次批量读取', exact: true })
  await expect(batch.locator('summary')).toHaveCount(3)
  expect(await batch.locator('summary').allTextContents()).toEqual([
    'first.txt · 已读取', 'missing.txt · 失败WORKSPACE_FILE_NOT_FOUND', 'second.txt · 已读取',
  ])
  await batch.locator('summary').first().click()
  await expect(batch.getByRole('region', { name: '读取内容：first.txt', exact: true })).toContainText('FIRST-PRIVATE-PLACEHOLDER')
  await expect(batch.getByRole('region', { name: '读取内容：second.txt', exact: true })).not.toBeVisible()
  await batch.locator('summary').nth(2).click()
  await expect(batch.getByRole('region', { name: '读取内容：second.txt', exact: true })).toContainText('第二个文件🙂')
  await expect(card.getByRole('region', { name: '工具输入', exact: true })).toHaveCount(0)
  const legacy = page.getByTestId('tool-call-card').last()
  await legacy.getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  await expect(legacy.getByRole('region', { name: '工具输出', exact: true })).toContainText('第二个文件🙂')
  await expect(legacy.getByRole('region', { name: '本次批量读取', exact: true })).toHaveCount(0)
  await legacy.getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  const screenshot = testInfo.outputPath('read-many.png')
  await card.screenshot({ path: screenshot })
  await testInfo.attach('批量读取独立文件节点', { path: screenshot, contentType: 'image/png' })
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => card.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const narrow = testInfo.outputPath('read-many-narrow.png')
  await card.screenshot({ path: narrow })
  await testInfo.attach('窄屏批量读取节点', { path: narrow, contentType: 'image/png' })
  await page.setViewportSize({ width: 1280, height: 720 })
  // 改动受控固件后刷新，历史详情必须仍是执行时的原版本，不得重读当前文件。
  await writeFile(path.join(root, 'first.txt'), 'LATER-VERSION')
  await page.reload()
  await card.getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  await batch.locator('summary').first().click()
  await expect(batch.getByRole('region', { name: '读取内容：first.txt', exact: true })).toContainText('FIRST-PRIVATE-PLACEHOLDER')
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  reads = 0
  await page.reload()
  await card.getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByText('first.txt', { exact: false })).toHaveCount(0)
  expect(reads).toBe(0)
})
