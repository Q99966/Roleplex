import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { randomBytes } from 'node:crypto'
import { expect, test } from '@playwright/test'
import { readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 Provider 请求简单 Shell，经 Owner 批准后依据真实文件回答', async ({ page }) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', 'shell-approval')
  await mkdir(root)
  const proof = `SHELL-${randomBytes(12).toString('hex')}`
  await writeFile(path.join(root, 'shell-proof-source.txt'), proof + '\n', { encoding: 'utf-8', mode: 0o600, flag: 'wx' })
  const title = `真实 Shell 验收 ${stamp}`
  let stage = '登录'
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20_000 })
    stage = '准备受限的真实脚本测试资源'
    const prepared = await page.evaluate(async ({ base, root, title }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const request = async (route: string, body?: unknown, method = 'POST') => {
        const response = await fetch(base + route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
        if (!response.ok) throw new Error('Shell smoke 资源准备失败')
        return response.json()
      }
      const health = await request('/api/health')
      if (!health.world_managed) throw new Error('测试必须使用物理 World')
      const caps = await request('/api/workspaces/capabilities')
      if (!caps.shell_available) throw new Error('本机无允许的 Shell')
      const script = caps.shell_kind === 'bash' ? 'cat shell-proof-source.txt' : "Get-Content -Raw -LiteralPath 'shell-proof-source.txt'"
      const roles = await request('/api/roles')
      const seed = roles.find((role: { model_config_id: number | null; deleted_at: string | null }) => role.model_config_id && !role.deleted_at)
      const workspace = await request('/api/workspaces', { display_name: title, root_path: root, acknowledge_existing_content: true })
      await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true }, 'PATCH')
      const role = await request('/api/roles', { name: `Shell 助手 ${title}`, model_config_id: seed.model_config_id,
        model_name: seed.model_name, system_prompt: '必须按用户指定的精确脚本调用一次 workspace_run_shell，等待审批和真实结果后仅回答文件内容，禁止猜测或附加其他命令。',
        builtin_tools: ['workspace_run_shell'], params: { max_tokens: 1024 } })
      const conversation = await request('/api/conversations', { title, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })
      return { id: conversation.id as number, script }
    }, { base, root, title })
    await page.reload()
    await page.getByText(title, { exact: true }).click()
    stage = '真实模型提出精确脚本并等待人工决定'
    await page.getByLabel('消息输入框').fill(`请调用 workspace_run_shell，script 必须精确为：${prepared.script}。等待 Owner 批准，执行后仅回复文件中的校验值。`)
    await page.getByLabel('发送消息').click()
    await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible({ timeout: 90_000 })
    // 只能批准预先限定的这条只读脚本，不因测试通过目标而批准模型额外提出的命令。
    const allowed = await page.evaluate(async ({ base, id, script }) => {
      const rows = await (await fetch(`${base}/api/conversations/${id}/tool-approvals`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }, cache: 'no-store',
      })).json()
      return rows.length === 1 && rows[0].script.trim() === script
    }, { base, id: prepared.id, script: prepared.script })
    if (!allowed) {
      await page.getByRole('button', { name: '拒绝本次 Shell', exact: true }).click()
      throw new Error('模型请求超出本次 smoke 批准范围')
    }
    await page.reload()
    await page.getByRole('button', { name: '批准本次 Shell', exact: true }).click()
    stage = '真实脚本输出及角色收尾'
    await expect.poll(() => page.evaluate(async (proof) => {
      const state = (await import('/src/store/chat.ts')).useChatStore.getState()
      const reply = state.messages.find((message) => message.sender_type === 'role')
      return reply?.status === 'done' && !state.generating && reply.parts_json.some((part) => part.type === 'text' && part.text?.includes(proof))
    }, proof), { timeout: 90_000 }).toBe(true)
    stage = 'Owner 展开真实脚本和输出并刷新恢复'
    await page.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).click()
    await expect.poll(async () => (await page.getByRole('region', { name: '标准输出', exact: true }).textContent())?.includes(proof) ?? false).toBe(true)
    await expect.poll(async () => (await page.getByRole('region', { name: '执行脚本', exact: true }).textContent())?.trim() === prepared.script).toBe(true)
    await page.reload()
    await page.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).click()
    await expect.poll(async () => (await page.getByRole('region', { name: '标准输出', exact: true }).textContent())?.includes(proof) ?? false).toBe(true)
    stage = '审计与日志隔离'
    const resolved = await waitForRunEvents((event) => event.event === 'tool.approval_resolved' && event.conversation_id === prepared.id, 1)
    expect(resolved[0].approval_status).toBe('approved')
    const tools = await waitForRunEvents((event) => event.event === 'tool.call_completed' && event.conversation_id === prepared.id, 1)
    expect(tools[0].status).toBe('success')
    expect(tools[0].tool_call_id).toBe(resolved[0].tool_call_id)
    const calls = await waitForRunEvents((event) => event.event === 'provider.call_completed' && event.conversation_id === prepared.id, 2)
    expect(calls.every((event) => event.usage_source === 'provider')).toBe(true)
    const serialized = JSON.stringify(await readRunEvents())
    expect([proof, prepared.script, root].every((value) => !serialized.includes(value))).toBe(true)
  } catch {
    await page.goto('about:blank').catch(() => undefined)
    throw new Error(`真实 Shell 验收失败：${stage}`)
  }
})
