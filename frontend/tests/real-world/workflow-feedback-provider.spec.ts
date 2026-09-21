import { test, expect } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 World 节点反馈、协调局部修正和文件验证后关闭', async ({ page }) => {
  test.setTimeout(420_000); page.setDefaultTimeout(15_000)
  let stage = '登录隔离 World'
  try {
    const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!, base = process.env.ROLEPLEX_E2E_API_ORIGIN!
    const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'workflow-feedback')
    await mkdir(root, { recursive: true })
    await writeFile(path.join(root, 'release.txt'), 'release=alpha\nrelease=beta\n裁定依据：本次应采用 beta。', 'utf8')
    await page.setViewportSize({ width: 1680, height: 1000 }); await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20_000 })
    stage = '准备受控契约和来源任务'
    const cid = await page.evaluate(async ({ root, base }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      async function request(route: string, body?: unknown, method = 'POST') {
        const response = await fetch(base + '/api' + route, { headers, method: body ? method : 'GET', body: body ? JSON.stringify(body) : undefined })
        if (!response.ok) throw new Error(`feedback fixture ${response.status}`)
        return response.json()
      }
      const seed = (await request('/roles')).find((role: { model_config_id: number; deleted_at: string | null }) => role.model_config_id && !role.deleted_at)
      const roles = []
      for (const [name, tools] of [['反馈审查者', ['workspace_read']], ['契约裁定者', ['workspace_read', 'workspace_write']], ['反馈协调者', []]] as const) {
        roles.push(await request('/roles', { name, model_config_id: seed.model_config_id, model_name: seed.model_name,
          context_window_tokens: 200000, params: { max_tokens: 4096, temperature: 0 }, builtin_tools: tools,
          system_prompt: '按本次工具授权完成当前任务，使用工具返回的真实版本。工作节点通过 workflow_result 报告结果与需要处置的 feedback。协调请求先读图和运行；只调整未派发区域，原审查保留。用 workflow_edit_graph 增加处理任务，再 workflow_feedback_update assign 关联。处理完成后的协调请求先 inspect_run，再用真实验证尝试 resolve；不能代签人工核验，不要轮询。汇总阶段使用 workflow_summary。' }))
      }
      const budget = await request('/agent-budget/config')
      await request('/agent-budget/config', { decision_limit: 64, expected_revision: budget.revision }, 'PUT')
      const workspace = await request('/workspaces', { display_name: '反馈验证工作区', root_path: root, acknowledge_existing_content: true })
      await request(`/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
      const group = await request('/conversations', { title: '真实反馈处置验收', type: 'group', role_ids: roles.map(role => role.id), workspace_binding_id: workspace.id })
      await request(`/conversations/${group.id}/orchestrator`, { role_id: roles[2].id, expected_revision: group.revision }, 'PUT')
      await request(`/conversations/${group.id}/workflows/definitions/${crypto.randomUUID()}`, {
        name: '真实反馈闭环', expected_revision: 0, graph: { runtime_version: 2, nodes: [
          { id: 'review', kind: 'role', title: '检查发布契约', role_id: roles[0].id, tools: ['workspace_read'],
            task: '用 workspace_read 读取 release.txt。文件有 alpha 和 beta 两个冲突约定；通过 workflow_result 报告 checked=true，并 feedback 上报一项 category=contract、request_key=release-conflict、summary=发布约定冲突、blocking=true、suggested_role_id 为契约裁定者。details 说明裁定依据已有“本次应采用 beta”，需要契约裁定者实际读取并写回 release.txt，最终严格为 release=beta 不加换行，写后再次读取核对，处理节点使用 feedback_resolved 布尔结果。自己不要尝试写文件。',
            result_schema: { checked: 'boolean' } },
          { id: 'deliver', kind: 'join', title: '交付', inputs: ['review'] },
        ], edges: [['review', 'deliver']] },
      }, 'PUT')
      return group.id as number
    }, { root, base })
    const snapshot = () => page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
    })).json(), { cid, base })
    stage = '授权自动反馈处置并启动'
    await page.reload(); await page.getByRole('button', { name: '打开会话：真实反馈处置验收', exact: true }).click()
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
    await page.getByRole('button', { name: '真实反馈闭环 v1', exact: true }).click()
    await page.getByLabel('协调执行时自动处理节点反馈').check()
    await page.getByLabel('本次运行补充要求').fill('保留现有图和角色任务，读图后直接启动。在节点正式反馈后再按反馈局部补图处置，不提前替换原审查。反馈处置只需追加一个契约裁定者任务，以原审查为上游并连接交付；该任务读写并再次读取 release.txt 核对 beta，result_schema 包含 feedback_resolved:boolean。完成后再复核关闭，继续交付。')
    await page.getByRole('button', { name: '协调执行', exact: true }).click()
    stage = '真实模型反馈、补图、处理、复核'
    await expect.poll(async () => (await snapshot()).runs[0]?.status, { timeout: 300_000, intervals: [1000, 2000, 3000] }).toBe('completed')
    const data = await snapshot(), run = data.runs[0], feedback = run.feedback[0]
    expect(run.feedback_mode === 'automatic').toBe(true)
    expect(run.feedback.length).toBe(1)
    expect(feedback.category === 'contract' && feedback.status === 'resolved').toBe(true)
    expect(feedback.graph_changes.length > 0).toBe(true)
    const reviews = run.attempts.filter((attempt: { node_id: string }) => attempt.node_id === 'review')
    expect(reviews.length).toBe(1)
    expect(feedback.actor_execution_id === reviews[0].execution_id).toBe(true)
    const proof = run.attempts.find((attempt: { id: string }) => attempt.id === feedback.verification_attempt_id)
    expect(proof?.status === 'completed' && proof.result.values.feedback_resolved === true).toBe(true)
    expect((await readFile(path.join(root, 'release.txt'), 'utf8')) === 'release=beta').toBe(true)
    expect(data.coordinations.filter((grant: { run_id: string }) => grant.run_id === run.id).length >= 2).toBe(true)
    expect(feedback.history.some((event: { action: string; actor_kind: string }) => event.action === 'resolve' && event.actor_kind === 'role')).toBe(true)
    expect(run.attempts.filter((attempt: { execution_id: string | null }) => attempt.execution_id).every((attempt: { usage: { output_tokens: number | null } }) => (attempt.usage.output_tokens ?? 0) > 0)).toBe(true)
    stage = '浏览器查看来源与处置记录'
    await page.getByLabel('显示已处置').check()
    await expect(page.getByRole('article', { name: '反馈：发布约定冲突' })).toContainText('已解决')
  } catch {
    await page.goto('about:blank')
    throw new Error(`真实反馈验收失败；阶段：${stage}。保留 World 供核对，不输出模型正文或凭据。`)
  }
})
