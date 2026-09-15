import { expect, test } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

for (const decision of ['approve', 'reject'] as const) {
  test(`真实 Provider 同轮 Shell 审批与写入：${decision}`, async ({ page }, testInfo) => {
    const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
    const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
    const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', `write-wait-${decision}`)
    await mkdir(root)
    const script = 'echo stage3-approval-proof'
    const content = 'stage3-write-proof'
    const files = decision === 'approve' ? ['nested/proof.txt'] : ['nested/first.txt', 'nested/second.txt']
    const args = decision === 'approve' ? { path: files[0], content } : { items: files.map((file) => ({ path: file, content })) }
    let cid: number | null = null
    let stage = '准备', failedStage: string | null = null
    let cleanup = false
    const observation: Record<string, unknown> = { decision, input_mode: decision === 'approve' ? 'single' : 'batch' }
    /** 仅从本轮 Owner 会话请求资源，不将访问凭据返回测试进程。
     * @param route 本轮 API 相对路径。
     */
    async function api(route: string) {
      return page.evaluate(async ({ base, route }) => {
        const response = await fetch(base + route, { signal: AbortSignal.timeout(5000), headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
        if (!response.ok) throw new Error('受控查询失败')
        return response.json()
      }, { base, route })
    }
    try {
      await page.goto('/#/auth')
      await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
      await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
      await page.getByRole('button', { name: '进入工作台' }).click()
      await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })
      cid = await page.evaluate(async ({ base, root, decision }) => {
        const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
        const request = async (route: string, body?: unknown, method = 'POST') => {
          const response = await fetch(base + route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
          if (!response.ok) throw new Error('受控准备失败')
          return response.json()
        }
        const seed = (await request('/api/roles')).find((role: { model_config_id: number | null }) => role.model_config_id)
        const workspace = await request('/api/workspaces', { display_name: `真实并发写入 ${decision}`, root_path: root, acknowledge_existing_content: true })
        await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true, file_tools_enabled: true }, 'PATCH')
        const role = await request('/api/roles', { name: `真实并发助手 ${decision}`, model_config_id: seed.model_config_id, model_name: seed.model_name,
          params: { max_tokens: 1024 }, system_prompt: '按要求在同一个响应发起两个独立工具调用。审批是宿主职责，不等待一个工具结果才生成另一个。失败或拒绝后不要重试。',
          builtin_tools: ['workspace_run_shell', 'workspace_write'] })
        return (await request('/api/conversations', { title: `真实并发验收 ${decision}`, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
      }, { base, root, decision })
      await page.reload()
      await page.getByText(`真实并发验收 ${decision}`, { exact: true }).click()
      stage = '模型同轮调用'
      await page.getByLabel('消息输入框').fill(`请在同一次模型响应中发起两个独立工具调用，先列 workspace_run_shell，script 精确为 ${JSON.stringify(script)}；再列 workspace_write，参数精确为 ${JSON.stringify(args)}。不要把它们拆成前后两轮，不等 Shell 结果才调用 write。不要调用其他工具或创建其他文件。工具返回后只简短报告状态，Shell 被拒绝就接受拒绝，不重试也不换脚本。`)
      await page.getByLabel('发送消息').click()
      await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible({ timeout: 60000 })
      const approvals = await api(`/api/conversations/${cid}/tool-approvals`)
      observation.exact_script = approvals.length === 1 && approvals[0].script.trim() === script
      if (!observation.exact_script) throw new Error('脚本超出批准范围')
      const approval = approvals[0]
      stage = '批准前写入'
      await expect.poll(async () => (await Promise.all(files.map((file) => readFile(path.join(root, file), 'utf8').catch(() => '')))).every((text) => text === content), { timeout: 10000 }).toBe(true)
      // 超过原五秒锁等待窗口后审批仍 pending；这里只等本轮占位任务，不消耗新的模型请求。
      await page.waitForTimeout(6000)
      const stillPending = await api(`/api/conversations/${cid}/tool-approvals`)
      observation.write_before_decision = stillPending.some((row: { id: number; status: string }) => row.id === approval.id && row.status === 'pending')
      const before = await api(`/api/conversations/${cid}/messages`)
      const message = before.items.find((row: { sender_type: string }) => row.sender_type === 'role')
      const calls = message?.parts_json.filter((part: { type: string }) => part.type === 'tool_call') ?? []
      observation.calls_before_decision = calls.length
      observation.write_success_before_decision = calls.some((call: { tool_name: string; status: string }) => call.tool_name === 'workspace_write' && call.status === 'success')
      if (!observation.write_before_decision || !observation.write_success_before_decision || calls.length !== 2) throw new Error('未完成真实并发路径')
      stage = '审批决定与收尾'
      const [, response] = await Promise.all([
        page.getByRole('button', { name: `${decision === 'approve' ? '批准' : '拒绝'}本次 Shell`, exact: true }).click(),
        page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/tool-approvals/${approval.id}/decision`)),
      ])
      if (!response.ok()) throw new Error('审批决定失败')
      await expect.poll(async () => (await api(`/api/conversations/${cid}/messages`)).active_generation_ids.length, { timeout: 60000 }).toBe(0)
      const history = await api(`/api/conversations/${cid}/messages`)
      const reply = history.items.find((row: { sender_type: string }) => row.sender_type === 'role')
      const finalCalls = reply.parts_json.filter((part: { type: string }) => part.type === 'tool_call')
      observation.final_call_count = finalCalls.length
      observation.shell_expected_status = finalCalls.some((call: { tool_name: string; status: string }) => call.tool_name === 'workspace_run_shell' && call.status === (decision === 'approve' ? 'success' : 'rejected'))
      observation.files_preserved = (await Promise.all(files.map((file) => readFile(path.join(root, file), 'utf8').catch(() => '')))).every((text) => text === content)
      observation.normal_finish = reply.status === 'done'
      if (finalCalls.length !== 2 || !observation.shell_expected_status || !observation.files_preserved || !observation.normal_finish) throw new Error('结果不符合预期')
      const events = (await readRunEvents()).filter((event) => event.conversation_id === cid)
      const providers = events.filter((event) => event.event === 'provider.call_completed')
      observation.provider_calls = providers.length
      // 缺失 usage 保持未知，不以零或文本估算冒充厂商统计。
      observation.input_tokens = providers.length && providers.every((event) => typeof event.input_tokens === 'number')
        ? providers.reduce((sum, event) => sum + Number(event.input_tokens), 0) : null
      observation.output_tokens = providers.length && providers.every((event) => typeof event.output_tokens === 'number')
        ? providers.reduce((sum, event) => sum + Number(event.output_tokens), 0) : null
      observation.wait_records = events.filter((event) => event.event === 'tool.write_wait_completed').length
      observation.model = providers[0]?.model ?? null
    } catch {
      failedStage = stage
    } finally {
      if (cid !== null) cleanup = await page.evaluate(async ({ base, cid }) => {
        const response = await fetch(`${base}/api/conversations/${cid}/stop`, { method: 'POST', signal: AbortSignal.timeout(5000), headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
        return response.ok
      }, { base, cid }).catch(() => false)
      await page.goto('about:blank').catch(() => undefined)
      const report = testInfo.outputPath('write-wait-observation.json')
      await writeFile(report, JSON.stringify({ ...observation, failed_stage: failedStage, cleanup_passed: cleanup }))
      await testInfo.attach('真实审批与写入观察', { path: report, contentType: 'application/json' })
    }
    if (failedStage || !cleanup) throw new Error(`真实写入等待验证失败：${failedStage ?? '清理'}`)
  })
}
