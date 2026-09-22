import { test, expect } from '@playwright/test'
import { startWorkflow, workflowInspector, workflowMore, workflowOverview, workflowRecords, workflowToolbar } from '../workflow-ui'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 World 从目标读写编辑图、执行、运行中追加审查与历史版本', async ({ page }) => {
  test.setTimeout(420_000); page.setDefaultTimeout(15_000)
  let stage = '登录隔离世界'
  try {
    const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!, base = process.env.ROLEPLEX_E2E_API_ORIGIN!
    const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'graph-control')
    await mkdir(root, { recursive: true }); await page.setViewportSize({ width: 1680, height: 1000 })
    await page.goto('/#/auth'); await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20_000 })
    stage = '准备角色与空群'
    const fixture = await page.evaluate(async ({ root, base }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      async function request(route: string, body?: unknown, method = 'POST') {
        const response = await fetch(base + '/api' + route, { headers, method: body ? method : 'GET', body: body ? JSON.stringify(body) : undefined })
        if (!response.ok) throw new Error(`graph-control fixture ${response.status}`)
        return response.json()
      }
      const seed = (await request('/roles')).find((r: { model_config_id: number; deleted_at: string | null }) => r.model_config_id && !r.deleted_at)
      const roles = []
      for (const [name, tools] of [['图管理开发者',['workspace_read','workspace_write']],['图管理审查者',['workspace_read']],['图管理协调者',[]]] as const) {
        roles.push(await request('/roles', { name, model_config_id: seed.model_config_id, model_name: seed.model_name, context_window_tokens: 200000,
          params: { max_tokens: 4096, temperature: 0 }, builtin_tools: tools,
          system_prompt: '按本次明确授权与当前任务调用真实工具，工具成功后不要重复。协调时先读图和成员能力，整体建图使用 workflow_write_graph，局部调整使用 workflow_edit_graph，版本取实际工具响应。任务节点按 required result schema 使用 workflow_result 报告实际结果；只拥有汇总工具时使用 workflow_summary。没有文件工具的协调角色不可假装读写文件，不代签人工确认。' }))
      }
      const config = await request('/agent-budget/config')
      await request('/agent-budget/config', { decision_limit: 64, expected_revision: config.revision }, 'PUT')
      const workspace = await request('/workspaces', { display_name: '真实图管理工作区', root_path: root, acknowledge_existing_content: true })
      await request(`/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
      const conversation = await request('/conversations', { title: '真实自主图管理验收', type: 'group', role_ids: roles.map(r => r.id), workspace_binding_id: workspace.id })
      await request(`/conversations/${conversation.id}/orchestrator`, { role_id: roles[2].id, expected_revision: conversation.revision }, 'PUT')
      return { cid: conversation.id as number, role: roles[2].name as string }
    }, { root, base })
    const snapshot = () => page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json(), { cid: fixture.cid, base })
    await page.reload(); await page.getByRole('button', { name: '打开会话：真实自主图管理验收', exact: true }).click()
    const proof = `GRAPH-PROOF-${stamp}`
    stage = '模型从目标创建并编辑草稿'
    const input = page.getByRole('textbox', { name: '消息输入框' })
    await input.fill('/plan@'); await page.getByRole('option', { name: `@${fixture.role}`, exact: true }).click()
    await input.fill(`/plan@${fixture.role} 创建三步串行流程，名称为“真实图管理流程”：develop 是图管理开发者，用 workspace_write 创建 graph-proof.txt，内容严格为 ${proof}，不加换行；gate 是人工确认；review 是图管理审查者，用 workspace_read 读取该文件，核对内容并通过 workflow_result 报告 approved 布尔值和 observed 文本，result_schema 明确这两个类型。角色 ID 从成员能力读取。开发需要 workspace_read/workspace_write，审查只要 workspace_read。先实际 read_graph，再 write_graph 整体创建，最后 edit_graph 将 review 标题改为“文件验收”。现在只规划，不启动。`)
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await expect.poll(async () => (await snapshot()).coordinations[0]?.status, { timeout: 120_000 }).toBe('completed')
    const designed = await snapshot()
    expect(designed.runs.length).toBe(0); expect(designed.definitions.length).toBe(1)
    expect(designed.definitions[0].revision).toBeGreaterThanOrEqual(2)
    expect(designed.definitions[0].graph.nodes.some((n: { id: string; title: string }) => n.id === 'review' && n.title === '文件验收')).toBe(true)
    const tools = await page.evaluate(async ({ cid, sid, base }) => {
      const response = await fetch(`${base}/api/conversations/${cid}/workflows/coordination/${sid}/message`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
      const message = await response.json()
      return message?.parts_json?.filter((p: { type: string }) => p.type === 'tool_call').map((p: { name?: string; tool_name?: string }) => p.name ?? p.tool_name) ?? []
    }, { cid: fixture.cid, sid: designed.coordinations[0].id, base })
    expect(['workflow_read_graph','workflow_write_graph','workflow_edit_graph'].every(name => tools.includes(name))).toBe(true)
    stage = '授权执行并等待人工确认'
    await workflowOverview(page)
    await page.getByLabel('本次运行补充要求').fill('保持已保存图的结构和节点任务，读取图后启动当前流程；不要代签人工确认。')
    await startWorkflow(page, true)
    await expect(workflowToolbar(page).getByRole('status')).toContainText('等待人工确认', { timeout: 100_000 })
    expect((await readFile(path.join(root, 'graph-proof.txt'), 'utf8')) === proof).toBe(true)
    const before = (await snapshot()).runs[0]
    const develop = before.attempts.find((a: { node_id: string }) => a.node_id === 'develop')
    expect(before.chain_id === designed.coordinations[0].chain_id).toBe(true)
    stage = '真实模型运行中新增审查'
    await workflowMore(page, '让协调者调整运行')
    await page.getByLabel('运行调整要求').fill(`先 read_graph 和 inspect_run 核对当前运行；不要修改已执行 develop 或等待中的 gate。用 edit_graph 新增 extra_review 角色任务，标题“追加审查”，角色选择图管理审查者，仅 workspace_read，inputs 为 develop。实际读取 graph-proof.txt，核对内容为 ${proof}，workflow_result 上报布尔 approved（result_schema 明确 boolean）。连接 develop→extra_review、extra_review→review。不要重跑开发或代签人工确认，不要停止运行。提交后简短结束。`)
    await page.getByRole('button', { name: '发送协调要求', exact: true }).click()
    await expect.poll(async () => (await snapshot()).runs[0].attempts.some((a: { node_id: string; status: string }) => a.node_id === 'extra_review' && a.status === 'completed'), { timeout: 100_000 }).toBe(true)
    const revised = (await snapshot()).runs[0]
    expect(revised.attempts.filter((a: { node_id: string }) => a.node_id === 'develop').length).toBe(1)
    expect(revised.attempts.find((a: { node_id: string }) => a.node_id === 'develop').execution_id === develop.execution_id).toBe(true)
    expect(revised.graph_revision).toBeGreaterThan(before.graph_revision)
    stage = '人工确认并完成真实执行'
    const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
    const gateIndex = revised.graph.nodes.findIndex((n: { id: string }) => n.id === 'gate')
    await canvas.getByRole('button', { name: `节点 ${gateIndex+1}：${revised.graph.nodes[gateIndex].title}`, exact: true }).click()
    await workflowInspector(page).getByRole('button', { name: '确认继续', exact: true }).click()
    await expect(workflowToolbar(page).getByRole('status')).toContainText('本次执行结束', { timeout: 80_000 })
    const final = (await snapshot()).runs[0]
    expect(final.attempts.filter((a: { execution_id: string | null }) => a.execution_id).every((a: { usage: { output_tokens: number | null } }) => (a.usage.output_tokens ?? 0) > 0)).toBe(true)
    expect(final.attempts.find((a: { node_id: string }) => a.node_id === 'review').result.values.approved).toBe(true)
    stage = '刷新核对历史图'
    await page.reload()
    await expect(workflowToolbar(page).getByLabel('工作流对象')).toHaveValue(`run:${final.id}`)
    await workflowRecords(page)
    await page.getByLabel('查看运行图版本').selectOption(String(before.graph_revision))
    await expect(canvas.locator('.react-flow__node')).toHaveCount(3)
  } catch {
    await page.goto('about:blank')
    throw new Error(`真实图管理验收失败；阶段：${stage}。请查看保留 World 的控制状态。`)
  }
})
