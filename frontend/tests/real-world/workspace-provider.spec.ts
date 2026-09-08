import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

const STAMP = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP ?? 'missing'
const API_ORIGIN = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8004'
const WORKSPACE_ROOT = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT ?? '/home/chen/workspace/testworkspace'
const WORKSPACE_RELATIVE_ROOT = process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT ?? 'missing'

test('real provider uses native tools inside an isolated managed-world workspace', async ({ page }) => {
  const username = `realtest${STAMP}`
  const title = `真实 API W1a 工作区 ${STAMP}`
  const firstContent = `REAL-W1A-FIRST-${STAMP}`
  const finalContent = `REAL-W1A-FINAL-${STAMP}`
  const workspacePath = path.join(WORKSPACE_ROOT, WORKSPACE_RELATIVE_ROOT, 'default')

  await page.goto('/#/auth')
  await page.getByPlaceholder('owner').fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByText(`真实 API 验证 ${STAMP}`)).toBeVisible({ timeout: 20_000 })

  const conversationId = await page.evaluate(async (options) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const request = async (route: string, init: RequestInit = {}) => {
      const response = await fetch(`${options.base}${route}`, { ...init, headers })
      if (!response.ok) throw new Error(`${route} failed: ${response.status}`)
      return response.status === 204 ? null : response.json()
    }
    const roles = await request('/api/roles')
    const seedRole = roles.find((role: { model_config_id: number | null; deleted_at: string | null }) => (
      role.model_config_id !== null && role.deleted_at === null
    ))
    if (!seedRole) throw new Error('real-world seed role missing')
    const workspace = await request('/api/workspaces', {
      method: 'POST',
      body: JSON.stringify({
        display_name: '真实 Provider W1a 工作区',
        root_path: options.workspacePath,
        create_directory: false,
        acknowledge_existing_content: true,
      }),
    })
    await request(`/api/workspaces/${workspace.id}`, {
      method: 'PATCH', body: JSON.stringify({ file_tools_enabled: true }),
    })
    const role = await request('/api/roles', {
      method: 'POST',
      body: JSON.stringify({
        name: `真实 W1a 文件助手 ${options.stamp}`,
        system_prompt: '必须按用户给定顺序实际调用工作区工具；不能假装执行。每次更新必须使用读取结果中的 sha256。',
        model_config_id: seedRole.model_config_id,
        model_name: seedRole.model_name,
        context_window_tokens: 200000,
        params: { max_tokens: 1024 },
        builtin_tools: ['workspace_list', 'workspace_read', 'workspace_write'],
      }),
    })
    const conversation = await request('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({
        type: 'single', title: options.title, role_ids: [role.id], workspace_binding_id: workspace.id,
      }),
    })
    return conversation.id as number
  }, {
    base: API_ORIGIN,
    workspacePath,
    stamp: STAMP,
    title,
  })

  await page.reload()
  await page.getByText(title, { exact: true }).first().click()
  const prompt = [
    '这是 W1a 真实工具验收，必须实际调用工具并严格按顺序完成：',
    '1. workspace_list 列出根目录。',
    `2. workspace_write 新建 provider-proof.txt，内容精确为 ${firstContent}。`,
    '3. workspace_read 读取该文件并取得 sha256。',
    `4. 携带刚取得的 expected_sha256，用 workspace_write 把内容更新为 ${finalContent}。`,
    '5. workspace_read 再次读取确认，最后只回复文件中的最终内容。',
  ].join('\n')
  await page.getByLabel('消息输入框').fill(prompt)
  await page.getByLabel('发送消息').click()

  const messages = page.getByTestId('chat-message')
  await expect(messages).toHaveCount(2, { timeout: 120_000 })
  const reply = messages.nth(1)
  await expect(reply.getByText('生成中…')).toBeHidden({ timeout: 120_000 })
  await expect(reply.getByText('生成失败')).toBeHidden()
  await expect(reply).toContainText(finalContent)

  const actual = await readFile(
    path.join(WORKSPACE_ROOT, WORKSPACE_RELATIVE_ROOT, 'default', 'provider-proof.txt'),
    'utf-8',
  )
  expect(actual).toBe(finalContent)

  const toolEvents = await waitForRunEvents(
    (event) => event.event === 'tool.call_completed' && event.conversation_id === conversationId,
    5,
  )
  const toolNames = toolEvents.map((event) => event.tool_name)
  expect(toolNames).toContain('workspace_list')
  expect(toolNames.filter((name) => name === 'workspace_write').length).toBeGreaterThanOrEqual(2)
  expect(toolNames.filter((name) => name === 'workspace_read').length).toBeGreaterThanOrEqual(2)

  const providerEvents = await waitForRunEvents(
    (event) => event.event === 'provider.call_completed'
      && event.conversation_id === conversationId
      && event.provider_mode === 'real',
    2,
  )
  expect(providerEvents.length).toBeGreaterThanOrEqual(2)
  expect(providerEvents.at(-1)?.base_url).toEqual(expect.any(String))

  const serialized = JSON.stringify(await readRunEvents())
  expect(serialized).not.toContain('provider-proof.txt')
  expect(serialized).not.toContain(firstContent)
  expect(serialized).not.toContain(finalContent)
  expect(serialized).not.toContain(WORKSPACE_ROOT)
})
