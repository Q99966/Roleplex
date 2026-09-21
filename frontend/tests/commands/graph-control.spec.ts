import { test, expect, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, name)
  await mkdir(root, { recursive: true })
  return page.evaluate(async ({ root, name, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body?: unknown, method = 'POST') => {
      const response = await fetch(base + '/api' + route, { headers, method: body ? method : 'GET', body: body ? JSON.stringify(body) : undefined })
      if (!response.ok) throw new Error(`graph fixture ${response.status}`)
      return response.json()
    }
    const cfg = await request('/model-configs', { name: `${name}配置`, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const roles = []
    for (const [label, tools] of [['开发', ['workspace_read','workspace_write']], ['审查', ['workspace_read']], ['协调者', []]] as const) {
      roles.push(await request('/roles', { name: `${name}${label}`, model_config_id: cfg.id, model_name: 'fake-model', system_prompt: '受控角色', builtin_tools: tools }))
    }
    const workspace = await request('/workspaces', { display_name: `${name}工作区`, root_path: root, acknowledge_existing_content: true })
    await request(`/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    const budget = await request('/agent-budget/config')
    await request('/agent-budget/config', { decision_limit: 64, expected_revision: budget.revision }, 'PUT')
    const conversation = await request('/conversations', { title: name, type: 'group', role_ids: roles.map(r => r.id), workspace_binding_id: workspace.id })
    await request(`/conversations/${conversation.id}/orchestrator`, { role_id: roles[2].id, expected_revision: conversation.revision }, 'PUT')
    return { cid: conversation.id as number, role: roles[2].name as string, writer: roles[0].id as number }
  }, { root, name, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}
async function snapshot(page: Page, cid: number) {
  return page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json(), { cid, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}

test('从 /plan 创建草稿、真实图工具、共同编辑冲突与沿用规划预算启动', async ({ page }, testInfo) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1600, height: 1000 }); await ensureOwnerSession(page)
  const { cid, role } = await fixture(page, '图规划命令验收')
  await page.reload(); await page.getByRole('button', { name: '打开会话：图规划命令验收', exact: true }).click()
  const input = page.getByRole('textbox', { name: '消息输入框' })
  await input.fill('/plan@'); await page.getByRole('option', { name: `@${role}`, exact: true }).click()
  await input.fill(`/plan@${role} [GRAPH_CREATE] 创建一个先人工确认再执行的流程`)
  await expect(page.getByLabel('规划目标')).toHaveValue('')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await expect(canvas.locator('.react-flow__node')).toHaveCount(2, { timeout: 30_000 })
  await expect.poll(async () => (await snapshot(page, cid)).coordinations[0].status).toBe('completed')
  const data = await snapshot(page, cid), did = data.definitions[0].id
  expect(data.runs).toHaveLength(0); expect(data.definitions[0].revision).toBe(2)
  const screenshot = testInfo.outputPath('graph-planning.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('从目标生成的流程与协调入口', { path: screenshot, contentType: 'image/png' })
  await page.getByRole('button', { name: '查看协调工具记录', exact: true }).click()
  const record = page.getByRole('dialog', { name: '协调执行记录' })
  await expect(record.getByText(/workflow_write_graph/)).toBeVisible()
  await expect(record.getByText(/workflow_edit_graph/)).toBeVisible()
  await record.getByRole('button', { name: '关闭协调记录', exact: true }).click()
  await canvas.getByRole('button', { name: '节点 2：执行任务', exact: true }).click()
  await canvas.getByLabel('本步任务').fill('人工补充，保留这段草稿')
  await page.evaluate(async ({ cid, did, base }) => {
    const response = await fetch(`${base}/api/conversations/${cid}/workflows/graphs/definition/${did}/edit`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      body: JSON.stringify({ expected_graph_revision: 2, mutation_key: crypto.randomUUID(), operations: [{ op: 'update_node', node_id: 'gate', changes: { title: '远端确认' } }] }),
    })
    if (!response.ok) throw new Error(`remote edit ${response.status}`)
  }, { cid, did, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
  const conflict = page.getByRole('region', { name: '流程编辑冲突' })
  await expect(conflict).toBeVisible(); await expect(canvas.getByLabel('本步任务')).toHaveValue('人工补充，保留这段草稿')
  await conflict.getByRole('button', { name: '另存本地草稿' }).click()
  await page.getByRole('button', { name: '保存流程', exact: true }).click()
  await expect.poll(async () => (await snapshot(page, cid)).definitions.length).toBe(2)
  const after = await snapshot(page, cid)
  expect(after.definitions.find((d: { id: string }) => d.id === did).graph.nodes[0].title).toBe('远端确认')
  expect(after.definitions.find((d: { id: string }) => d.id !== did).graph.nodes[1].task).toBe('人工补充，保留这段草稿')
  await page.getByRole('button', { name: '查看流程草稿', exact: true }).click()
  await page.getByLabel('本次运行补充要求').fill('执行已保存的流程')
  await page.getByRole('button', { name: '协调执行', exact: true }).click()
  await expect(page.getByText(/等待人工确认 · 定义快照/)).toBeVisible({ timeout: 30_000 })
  const running = await snapshot(page, cid)
  expect(running.runs[0].chain_id).toBe(data.coordinations[0].chain_id)
  expect(running.runs[0].used_decisions).toBeGreaterThan(data.coordinations[0].used_decisions)
  await canvas.getByRole('button', { name: '节点 1：远端确认', exact: true }).click()
  await canvas.getByRole('button', { name: '确认继续', exact: true }).click()
  await expect(page.getByText(/本次执行结束 · 定义快照/)).toBeVisible({ timeout: 30_000 })
})

test('协调者在真实运行中追加审查，历史图不混入新节点', async ({ page }) => {
  page.setDefaultTimeout(12_000); await page.setViewportSize({ width: 1600, height: 1000 }); await ensureOwnerSession(page)
  const { cid, writer } = await fixture(page, '运行重规划验收')
  const rid = await page.evaluate(async ({ cid, writer, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }, did = crypto.randomUUID()
    const saved = await fetch(`${base}/api/conversations/${cid}/workflows/definitions/${did}`, { method: 'PUT', headers, body: JSON.stringify({ name: '运行调整', expected_revision: 0, graph: { runtime_version: 2,
      nodes: [{ id: 'develop', title: '开发', kind: 'role', role_id: writer, task: '[WF_BUILD]', tools: ['workspace_read','workspace_write'], result_keys: ['round'] }, { id: 'gate', title: '人工确认', kind: 'approval' }], edges: [['develop','gate']] } }) })
    if (!saved.ok) throw new Error('fixture definition failed')
    const response = await fetch(`${base}/api/conversations/${cid}/workflows/runs`, { method: 'POST', headers, body: JSON.stringify({ definition_id: did, expected_revision: 1, request_key: crypto.randomUUID() }) })
    if (!response.ok) throw new Error('fixture start failed')
    return (await response.json()).id as string
  }, { cid, writer, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
  await page.reload(); await page.getByRole('button', { name: '打开会话：运行重规划验收', exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByLabel('选择工作流运行').selectOption(rid)
  await expect(page.getByText(/等待人工确认 · 定义快照/)).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('运行调整要求').fill('追加一个读取开发文件的审查节点，不重跑开发。')
  await page.getByRole('button', { name: '让协调者调整运行', exact: true }).click()
  await expect.poll(async () => (await snapshot(page, cid)).runs[0].attempts.some((a: { node_id: string; status: string }) => a.node_id === 'runtime_review' && a.status === 'completed'), { timeout: 30_000 }).toBe(true)
  const run = (await snapshot(page, cid)).runs[0]
  expect(run.attempts.filter((a: { node_id: string }) => a.node_id === 'develop')).toHaveLength(1)
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await expect(canvas.locator('.react-flow__node')).toHaveCount(3)
  await page.getByLabel('查看运行图版本').selectOption('1')
  await expect(canvas.locator('.react-flow__node')).toHaveCount(2)
  await expect(canvas.getByText('运行新增审查')).toHaveCount(0)
  await page.getByLabel('查看运行图版本').selectOption(''); await expect(canvas.locator('.react-flow__node')).toHaveCount(3)
  await page.getByRole('button', { name: '停止此运行', exact: true }).click()
  await expect(page.getByText(/已停止 · 定义快照/)).toBeVisible()
})
