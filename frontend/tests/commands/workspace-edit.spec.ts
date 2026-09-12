import { expect, test } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('局部编辑卡显示真实 diff，失败显示版本冲突，Guest 不获取片段', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'workspace-edit')
  await mkdir(root)
  const first = "export const title = '第一版';\nexport const keep = '保持';\n"
  await writeFile(path.join(root, 'edit.ts'), first)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('编辑测试准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'edit-test', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '局部编辑助手', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '精确编辑。', builtin_tools: ['workspace_read', 'workspace_edit'] })
    const workspace = await request('/api/workspaces', { display_name: '编辑工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '局部编辑验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('局部编辑验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[EDIT_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('编辑流程已结束。', { exact: true })).toBeVisible()
  const card = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_edit', exact: true }) }).first()
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('button', { name: '执行详情：workspace_edit', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(card.getByRole('region', { name: '文件差异：edit.ts' })).toContainText('第二版🙂')
  await expect(card.getByText('原始输入与输出')).toHaveCount(0)
  expect(await readFile(path.join(root, 'edit.ts'), 'utf8')).toBe(first.replace('第一版', '第二版🙂'))
  const shot = testInfo.outputPath('workspace-edit.png')
  await card.screenshot({ path: shot })
  await testInfo.attach('局部编辑的实际差异', { path: shot, contentType: 'image/png' })
  await page.reload()
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('region', { name: '文件差异：edit.ts' })).toContainText('第二版🙂')
  // 固定 fake 再次使用第一版 hash，复现旧版本请求而非让测试重写文件。
  await page.getByLabel('消息输入框').fill('[EDIT_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('WORKSPACE_FILE_REVISION_CONFLICT', { exact: true })).toBeVisible()
  expect(await readFile(path.join(root, 'edit.ts'), 'utf8')).toBe(first.replace('第一版', '第二版🙂'))
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  let reads = 0
  page.on('request', (request) => { if (/\/tools\//.test(request.url())) reads++ })
  await page.reload()
  await page.getByRole('button', { name: '执行详情：workspace_edit', exact: true }).first().click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '文件差异：edit.ts' })).toHaveCount(0)
  expect(reads).toBe(0)
})
