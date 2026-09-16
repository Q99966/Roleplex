import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('已有单聊换绑与解绑，群聊串行文件交接和 Guest 隔离', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'group-workspace')
  await mkdir(path.join(root, 'a'), { recursive: true })
  await mkdir(path.join(root, 'b'), { recursive: true })
  await ensureOwnerSession(page)
  const data = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown, method = 'POST') => {
      const response = await fetch(url, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`fixture failed: ${response.status}`)
      return response.json()
    }
    const model = await post('/api/model-configs', { name: 'group-files', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const writer = await post('/api/roles', { name: '群聊写入者', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '写入文件', builtin_tools: ['workspace_write', 'workspace_run_command'] })
    const editor = await post('/api/roles', { name: '群聊编辑者', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '读取并编辑文件', builtin_tools: ['workspace_read', 'workspace_edit'] })
    const bindings = []
    for (const name of ['a', 'b']) {
      const row = await post('/api/workspaces', { display_name: `群聊目录 ${name}`, root_path: `${root}/${name}`, acknowledge_existing_content: true })
      await post(`/api/workspaces/${row.id}`, { file_tools_enabled: true, basic_commands_enabled: true }, 'PATCH')
      bindings.push(row.id)
    }
    const single = await post('/api/conversations', { type: 'single', title: '已有单聊换绑', role_ids: [writer.id] })
    return { writer, editor, bindings, single }
  }, root)
  await page.reload()
  await page.getByText('已有单聊换绑', { exact: true }).click()
  await expect(page.getByRole('complementary', { name: '会话详情', exact: true }).getByRole('region', { name: '会话工作区管理' })).toBeVisible()
  for (const [value, label] of [[String(data.bindings[0]), '群聊目录 a'], [String(data.bindings[1]), '群聊目录 b'], ['', '未绑定工作区']]) {
    await page.getByRole('button', { name: /工作区：.*管理/ }).click()
    await page.getByLabel('会话工作区', { exact: true }).selectOption(value)
    await page.getByRole('button', { name: '保存工作区', exact: true }).click()
    await expect(page.getByRole('button', { name: `工作区：${label} 管理`, exact: true })).toBeVisible()
  }
  await page.getByRole('button', { name: /工作区：.*管理/ }).click()
  await page.getByLabel('会话工作区', { exact: true }).selectOption(String(data.bindings[0]))
  await page.evaluate(async ({ cid, wid }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const list = await (await fetch('/api/conversations', { headers })).json()
    const revision = list.find((item: { id: number }) => item.id === cid).revision
    const response = await fetch(`/api/conversations/${cid}/workspace`, { method: 'PUT', headers,
      body: JSON.stringify({ workspace_binding_id: wid, expected_revision: revision }) })
    if (!response.ok) throw new Error('并发更新准备失败')
  }, { cid: data.single.id, wid: data.bindings[1] })
  await page.getByRole('button', { name: '保存工作区', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('会话已在其他位置更新')
  await page.getByRole('button', { name: /工作区：.*收起/ }).click()
  await page.getByRole('button', { name: '新建会话', exact: true }).click()
  await page.getByRole('button', { name: /群聊 \(协同多个 Agent 角色\)/ }).click()
  await page.getByLabel('会话名称 (必填)').fill('群聊工作区验收')
  await page.getByRole('button', { name: /群聊写入者/ }).click()
  await page.getByRole('button', { name: /群聊编辑者/ }).click()
  await page.getByLabel('会话工作区', { exact: true }).selectOption(String(data.bindings[0]))
  await page.getByRole('button', { name: '确认开启会话' }).click()
  await expect(page.getByRole('heading', { name: '群聊工作区验收', exact: true })).toBeVisible()
  const input = page.getByLabel('消息输入框')
  await input.fill('@')
  await page.getByRole('option', { name: '@群聊写入者', exact: true }).click()
  await input.fill(`${await input.inputValue()}@`)
  await page.getByRole('option', { name: '@群聊编辑者', exact: true }).click()
  await input.fill(`${await input.inputValue()}[GROUP_FILES_FAKE]`)
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('群聊写入角色完成。', { exact: true })).toBeVisible()
  await expect(page.getByText('群聊编辑角色完成。', { exact: true })).toBeVisible()
  expect(await readFile(path.join(root, 'a', 'group.txt'), 'utf8')).toBe('second')
  await expect(page.getByRole('button', { name: '执行详情：workspace_edit', exact: true })).toBeVisible()
  const cid = Number(page.url().split('/').at(-1))
  // 撤销编辑者权限后，它仍可回复，但没有文件工具。
  await page.evaluate(async (editor) => {
    const response = await fetch(`/api/roles/${editor.id}`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      body: JSON.stringify({ ...editor, builtin_tools: [] }) })
    if (!response.ok) throw new Error('撤销权限失败')
  }, data.editor)
  await input.fill('@')
  await page.getByRole('option', { name: '@群聊编辑者', exact: true }).click()
  await input.fill(`${await input.inputValue()}[GROUP_FILES_FAKE]`)
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('本角色未获文件工具授权。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /工作区：.*管理/ }).click()
  const desktopShot = testInfo.outputPath('overview-workspace-desktop.png')
  await page.getByRole('complementary', { name: '会话详情', exact: true }).screenshot({ path: desktopShot })
  await testInfo.attach('概览内的工作区管理', { path: desktopShot, contentType: 'image/png' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '工作区侧栏' })).toBeHidden()
  await page.getByRole('button', { name: '打开会话详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '会话详情', exact: true })
  await drawer.getByRole('button', { name: /工作区：.*管理/ }).click()
  await expect(drawer.getByRole('region', { name: '会话工作区管理' })).toBeVisible()
  await expect(page.getByLabel('会话工作区', { exact: true })).toBeVisible()
  const shot = testInfo.outputPath('group-workspace-mobile.png')
  await page.screenshot({ path: shot })
  await testInfo.attach('群聊工作区移动布局', { path: shot, contentType: 'image/png' })
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async (stamp) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
  }, process.env.ROLEPLEX_COMMAND_E2E_STAMP)
  await page.reload()
  await page.getByRole('button', { name: '打开会话详情', exact: true }).click()
  await expect(page.getByText('已绑定工作区 · 由 Owner 管理', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /工作区：.*管理/ })).toHaveCount(0)
  expect(await page.evaluate(async (cid) => (await fetch(`/api/conversations/${cid}/workspace`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
    body: JSON.stringify({ workspace_binding_id: null, expected_revision: 0 }) })).status, cid)).toBe(403)
})
