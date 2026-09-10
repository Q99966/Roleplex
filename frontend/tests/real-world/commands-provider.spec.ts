import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { expectManagedWorldLayout, readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

// 真实内容只在内存中断言；失败时不保留模型正文截图或追踪产物。
test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('real provider runs structured commands in the normal managed world', async ({ page }) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const apiOrigin = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const workspacePath = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!,
    process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', 'w1b-commands')
  const expected = await readFile(path.join(workspacePath, 'command-proof.txt'), 'utf-8')
  const proof = expected.split('\n')[0]
  const title = `真实 W1b 命令 ${stamp}`
  let stage = '登录与世界边界'
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`)).toBeVisible({ timeout: 20_000 })
    const health = await page.evaluate(async (base) => (await fetch(`${base}/api/health`)).json(), apiOrigin)
    expect(health.world_managed).toBe(true)
    expect(health.world_name).toBe('default')
    await expectManagedWorldLayout(process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0])

    stage = '登记真实命令角色与绑定'
    const conversationId = await page.evaluate(async (options) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const request = async (route: string, init: RequestInit = {}) => {
        const response = await fetch(`${options.base}${route}`, { ...init, headers })
        if (!response.ok) throw new Error(`W1b setup failed: ${response.status}`)
        return response.json()
      }
      const roles = await request('/api/roles')
      const seed = roles.find((role: { model_config_id: number | null; deleted_at: string | null }) => (
        role.model_config_id !== null && role.deleted_at === null
      ))
      if (!seed) throw new Error('Real provider seed missing')
      const workspace = await request('/api/workspaces', { method: 'POST', body: JSON.stringify({
        display_name: '真实 W1b 命令工作区', root_path: options.workspacePath, acknowledge_existing_content: true,
      }) })
      await request(`/api/workspaces/${workspace.id}`, { method: 'PATCH', body: JSON.stringify({ basic_commands_enabled: true }) })
      const role = await request('/api/roles', { method: 'POST', body: JSON.stringify({
        name: `真实命令助手 ${options.stamp}`, model_config_id: seed.model_config_id, model_name: seed.model_name,
        system_prompt: '必须实际顺序调用用户指定的结构化命令。只从工具结果取值，不猜测文件内容或统计。最后仅输出用户要求的 JSON。',
        context_window_tokens: 200000, params: { max_tokens: 1024 }, builtin_tools: ['workspace_run_command'],
      }) })
      const conversation = await request('/api/conversations', { method: 'POST', body: JSON.stringify({
        type: 'single', title: options.title, role_ids: [role.id], workspace_binding_id: workspace.id,
      }) })
      return conversation.id as number
    }, { base: apiOrigin, stamp, workspacePath, title })
    await page.reload()
    await page.getByText(title, { exact: true }).first().click()
    stage = '真实模型与四种命令'
    await page.getByLabel('消息输入框').fill([
      '必须通过 workspace_run_command 严格依次调用以下四次，每次等待结果再进行下一次：',
      '1. command="pwd", args={}。',
      '2. command="list", args={}。',
      '3. command="read", args={"path":"command-proof.txt"}。',
      '4. command="count", args={"path":"command-proof.txt"}。',
      '最后只输出 JSON：{"proof":"文件第一行","bytes":count返回的字节数,"lines":count返回的行数}。',
    ].join('\n'))
    await page.getByLabel('发送消息').click()
    const replies = page.getByTestId('chat-message')
    await expect(replies).toHaveCount(2, { timeout: 120_000 })
    await expect(replies.nth(1).getByText('生成中…')).toBeHidden({ timeout: 120_000 })

    // 只返回断言布尔值与工具安全元数据，不把完整模型回复交给失败报告。
    const observed = await page.evaluate(async (options) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${options.base}/api/conversations/${options.id}/messages`, { headers })).json()
      const reply = history.items.find((item: { sender_type: string }) => item.sender_type === 'role')
      const text = reply.parts_json.filter((part: { type: string }) => part.type === 'text')
        .map((part: { text: string }) => part.text).join('')
      let verified = false
      try {
        const result = JSON.parse(text.match(/\{[\s\S]*\}/)?.[0] ?? '')
        verified = result.proof === options.proof && result.bytes === options.bytes && result.lines === 2
      } catch { /* 格式错误仅以 false 返回，不持久化原始输出。 */ }
      const cards = reply.parts_json.filter((part: { type: string }) => part.type === 'tool_call')
      return {
        done: reply.status === 'done', verified,
        timeline: reply.timeline_version,
        order: reply.parts_json.filter((part: { type: string; text?: string }) => part.type !== 'text' || Boolean(part.text))
          .map((part: { type: string }) => part.type),
        commands: cards.map((part: { command: string }) => part.command),
        succeeded: cards.every((part: { status: string; exit_code: number }) => part.status === 'success' && part.exit_code === 0),
      }
    }, { base: apiOrigin, id: conversationId, proof, bytes: Buffer.byteLength(expected, 'utf-8') })
    expect(observed.done).toBe(true)
    expect(observed.verified).toBe(true)
    expect(observed.commands).toEqual(['pwd', 'list', 'read', 'count'])
    expect(observed.succeeded).toBe(true)
    expect(observed.timeline).toBe(1)
    expect(observed.order.at(-1)).toBe('text')
    expect(observed.order.filter((kind: string) => kind === 'tool_call')).toHaveLength(4)

    stage = 'Owner 展开与刷新后的加密详情'
    const expandRead = page.getByRole('button', { name: '执行详情：workspace_run_command · read', exact: true })
    await expandRead.click()
    await expect.poll(async () => (await page.getByRole('region', { name: '工具输出' }).textContent())?.includes(proof) ?? false).toBe(true)
    await page.reload()
    await page.getByRole('button', { name: '执行详情：workspace_run_command · read', exact: true }).click()
    await expect.poll(async () => (await page.getByRole('region', { name: '工具输出' }).textContent())?.includes(proof) ?? false).toBe(true)

    stage = '真实 usage、执行身份与日志脱敏'
    const tools = await waitForRunEvents((event) => event.event === 'tool.call_completed'
      && event.conversation_id === conversationId, 4)
    expect(tools.every((event) => event.status === 'success' && typeof event.execution_id === 'string')).toBe(true)
    expect(new Set(tools.map((event) => event.execution_id)).size).toBe(1)
    const provider = await waitForRunEvents((event) => event.event === 'provider.call_completed'
      && event.conversation_id === conversationId && event.provider_mode === 'real', 5)
    expect(provider.every((event) => event.usage_source === 'provider' && typeof event.input_tokens === 'number')).toBe(true)
    const serialized = JSON.stringify(await readRunEvents())
    expect([proof, expected, workspacePath, 'command-proof.txt'].every((value) => !serialized.includes(value))).toBe(true)
  } catch {
    // 清空真实正文后再让 Playwright 采集失败上下文，错误只含安全阶段标签。
    await page.goto('about:blank').catch(() => undefined)
    throw new Error(`W1b 真实世界验收未通过：${stage}`)
  }
})
