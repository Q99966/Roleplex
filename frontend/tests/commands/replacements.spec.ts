import { test, expect } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { ensureOwnerSession } from '../owner'

test('多片段一次编辑与单项/批次失败序号，刷新及 Guest 隔离', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'replacements')
  await mkdir(root)
  await writeFile(path.join(root, 'sample.txt'), 'alpha=1\nbeta=2\ngamma=3\n')
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async root => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('替换验证准备失败')
      return response.json()
    }
    const config = await post('/api/model-configs', { name: 'replacements', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: '多片段助手', system_prompt: '受控编辑。', model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_read', 'workspace_edit'] })
    const workspace = await post('/api/workspaces', { display_name: '多片段工作区', root_path: root, acknowledge_existing_content: true })
    await post(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await post('/api/conversations', { title: '多片段编辑验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id
  }, root)
  await page.reload()
  await page.getByRole('button', { name: '打开会话：多片段编辑验收', exact: true }).click()
  await page.getByLabel('消息输入框').fill('[REPLACEMENTS_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('多片段编辑完成。', { exact: true })).toBeVisible()
  const card = page.getByTestId('chat-message').nth(1).getByTestId('tool-call-card').nth(1)
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('region', { name: '文件差异：sample.txt', exact: true })).toContainText('alpha=10')
  await expect(card.getByRole('region', { name: '文件差异：sample.txt', exact: true })).toContainText('gamma=30')
  expect(await readFile(path.join(root, 'sample.txt'), 'utf8')).toBe('alpha=10\nbeta=20\ngamma=30\n')
  const success = testInfo.outputPath('replacements-diff.png')
  await card.screenshot({ path: success })
  await testInfo.attach('一次提交的三处差异', { path: success, contentType: 'image/png' })
  await page.getByLabel('消息输入框').fill('[REPLACEMENTS_BAD_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('多片段拒绝已确认。', { exact: true })).toBeVisible()
  const failed = page.getByTestId('chat-message').nth(3)
  await failed.scrollIntoViewIfNeeded()
  await expect(failed.getByText(/第 2 处替换未通过校验/)).toHaveCount(2)
  expect(await readFile(path.join(root, 'sample.txt'), 'utf8')).toBe('alpha=10\nbeta=20\ngamma=30\n')
  const failure = testInfo.outputPath('replacements-error.png')
  await failed.screenshot({ path: failure })
  await testInfo.attach('单项与批次失败序号', { path: failure, contentType: 'image/png' })
  await page.reload()
  await failed.scrollIntoViewIfNeeded()
  await expect(failed.getByText(/第 2 处替换未通过校验/)).toHaveCount(2)
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 准备失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  await page.reload()
  const edit = failed.getByRole('button', { name: '执行详情：workspace_edit', exact: true }).first()
  await edit.click()
  await expect(failed).toContainText('详细输入和输出仅 Owner 可见。')
  await expect(failed.getByText(/第 2 处替换未通过校验/)).toHaveCount(0)
})
