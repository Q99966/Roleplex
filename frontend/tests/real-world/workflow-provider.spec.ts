import { test, expect } from '@playwright/test'
import { workflowInspector, workflowToolbar } from '../workflow-ui'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 World 工作流开发、人工确认和审查文件交接', async ({ page }) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'workflow')
  await mkdir(root, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/auth')
  await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20_000 })
  const first = `REAL-WORKFLOW-FIRST-${stamp}`
  const final = `REAL-WORKFLOW-FINAL-${stamp}`
  const cid = await page.evaluate(async ({ base, root, first, final, stamp }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (url: string, data?: unknown, method = 'POST') => {
      const r = await fetch(base + url, { method: data === undefined ? 'GET' : method, headers, body: data === undefined ? undefined : JSON.stringify(data) })
      if (!r.ok) throw new Error(`workflow fixture status ${r.status}`)
      return r.json()
    }
    const seed = (await request('/api/roles')).find((r: { model_config_id: number | null; deleted_at: string | null }) => r.model_config_id && !r.deleted_at)
    if (!seed) throw new Error('真实模型角色未准备')
    const roles = []
    for (const [name, tools] of [['开发者', ['workspace_write']], ['审查者', ['workspace_read', 'workspace_edit']]] as const) {
      roles.push(await request('/api/roles', { name: `真实流程${name} ${stamp}`, model_config_id: seed.model_config_id,
        model_name: seed.model_name, context_window_tokens: 200000, params: { max_tokens: 4096, temperature: 0 },
        system_prompt: '按节点任务实际调用授权文件工具。成功后简短报告，不假装操作；不要重复已成功的工具调用。', builtin_tools: tools }))
    }
    const workspace = await request('/api/workspaces', { display_name: `真实流程工作区 ${stamp}`, root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    const conv = await request('/api/conversations', { title: `真实工作流协作 ${stamp}`, type: 'group', role_ids: roles.map(r => r.id), workspace_binding_id: workspace.id })
    const nodes = [
      { id: 'develop', kind: 'role', title: '开发文件', role_id: roles[0].id, task: `实际调用 workspace_write，在工作区创建 workflow-proof.txt，内容严格为 ${first}，没有换行。完成后只回复已创建。`, inputs: [] },
      { id: 'human', kind: 'approval', title: '人工确认文件', inputs: ['develop'] },
      { id: 'review', kind: 'role', title: '审查并修改', role_id: roles[1].id, task: `实际调用 workspace_read 读取 workflow-proof.txt，然后用真实返回的 sha256 调用 workspace_edit，把 ${first} 精确替换为 ${final}。成功后只回复已审查修改。`, inputs: ['develop'] },
    ]
    await request(`/api/conversations/${conv.id}/workflows/definitions/${crypto.randomUUID()}`, { name: `真实开发审查 ${stamp}`, expected_revision: 0, graph: { nodes, edges: [['develop', 'human'], ['human', 'review']] } }, 'PUT')
    return conv.id as number
  }, { base, root, first, final, stamp })
  await page.reload()
  await page.getByRole('button', { name: `打开会话：真实工作流协作 ${stamp}`, exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: new RegExp(`真实开发审查 ${stamp}`) }).click()
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await page.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(workflowToolbar(page).getByRole('status')).toContainText('等待人工确认', { timeout: 60_000 })
  expect((await readFile(path.join(root, 'workflow-proof.txt'), 'utf8')) === first).toBe(true)
  await canvas.getByRole('button', { name: '节点 2：人工确认文件', exact: true }).click()
  await workflowInspector(page).getByRole('button', { name: '确认继续', exact: true }).click()
  await expect(workflowToolbar(page).getByRole('status')).toContainText('本次执行结束', { timeout: 60_000 })
  expect((await readFile(path.join(root, 'workflow-proof.txt'), 'utf8')) === final).toBe(true)
  const facts = await page.evaluate(async ({ base, cid }) => {
    const response = await fetch(`${base}/api/conversations/${cid}/workflows`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
    const run = (await response.json()).runs[0]
    return { status: run.status, calls: run.used_decisions, attempts: run.attempts.map((a: { status: string; execution_id: string | null; usage: { output_tokens: number | null } }) => ({ status: a.status, executed: Boolean(a.execution_id), output: a.usage.output_tokens })) }
  }, { base, cid })
  expect(facts.status).toBe('completed')
  expect(facts.attempts.filter((a: { executed: boolean }) => a.executed)).toHaveLength(2)
  expect(facts.calls).toBeGreaterThanOrEqual(4)
  expect(facts.attempts.filter((a: { executed: boolean }) => a.executed).every((a: { output: number | null }) => a.output !== null && a.output > 0)).toBe(true)
})
