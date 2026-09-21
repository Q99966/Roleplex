import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  return page.evaluate(async ({ name, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    async function request(route: string, body?: unknown, method = 'POST') {
      const response = await fetch(base + '/api' + route, { headers, method: body ? method : 'GET', body: body ? JSON.stringify(body) : undefined })
      if (!response.ok) throw new Error(`readability fixture ${response.status}`)
      return response.json()
    }
    const config = await request('/model-configs', { name, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/roles', { name: `${name}开发`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控工作流角色', builtin_tools: [] })
    const reviewer = await request('/roles', { name: `${name}审查`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控审查角色', builtin_tools: [] })
    const group = await request('/conversations', { type: 'group', title: name, role_ids: [role.id, reviewer.id] })
    const budget = await request('/agent-budget/config')
    await request('/agent-budget/config', { decision_limit: 64, expected_revision: budget.revision }, 'PUT')
    const did = crypto.randomUUID()
    const labels = [['design', '架构与技术设计'], ['core', '实现核心逻辑并核对较长的模块职责说明'], ['ui', '渲染与交互'], ['join', '核心与表现汇合'], ['review', '代码审查'], ['judge', '审查门槛'], ['end', '交付报告']]
    const graph = { runtime_version: 2, concurrency: 2, nodes: labels.map(([id, title], index) => ({
      id, title, kind: id === 'judge' ? 'condition' : id === 'join' || id === 'end' ? 'join' : 'role',
      role_id: ['judge', 'join', 'end'].includes(id) ? null : ['review', 'ui'].includes(id) ? reviewer.id : role.id, task: id === 'review' ? '[FEEDBACK_REPORT]' : '说明当前任务完成',
      tools: [], inputs: [], position: { x: (6 - index) * 200, y: index % 2 * 170 },
      ...(id === 'core' ? { color: '#59a588' } : {}),
      ...(id === 'judge' ? { condition: { sources: ['review'], key: 'checked', value: true } } : {}),
    })), edges: [['design', 'core'], ['design', 'ui'], ['core', 'join'], ['ui', 'join'], ['join', 'review'], ['review', 'judge'], ['judge', 'review'], ['judge', 'end']],
      loops: [{ id: 'revision', entry: 'review', decision: 'judge', exit: 'end', body: ['review', 'judge'], repeat_when: false, max_iterations: 3 }],
      presentation: { groups: [{ id: 'build', title: '并行实现', node_ids: ['core', 'ui'] }, { id: 'check', title: '审查与修订', node_ids: ['review', 'judge'] }],
        edge_labels: [{ source: 'judge', target: 'review', label: '需要修订' }, { source: 'judge', target: 'end', label: '通过' }] },
    }
    await request(`/conversations/${group.id}/workflows/definitions/${did}`, { name: '可读工作流', expected_revision: 0, graph }, 'PUT')
    return { cid: group.id as number, did }
  }, { name, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}

async function snapshot(page: Page, cid: number) {
  return page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).json(), { cid, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}
async function open(page: Page, name: string, restored = false) {
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  if (restored) await expect(page.getByRole('region', { name: '本地草稿', exact: true })).toBeVisible()
  else {
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  }
  const list = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: /^已保存流程/ }) }).first()
  await expect(list.locator('summary')).toContainText('1')
  if (!await list.evaluate(element => (element as HTMLDetailsElement).open)) await list.locator('summary').click()
  await page.getByRole('button', { name: /可读工作流 v/ }).click()
}

test('阶段总览、循环展开、布局撤销和保存、业务标签与窄屏', async ({ page }, info) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1920, height: 1100 }); await ensureOwnerSession(page)
  const name = '工作流可读性验收', { cid } = await fixture(page, name)
  const initial = (await snapshot(page, cid)).definitions[0].graph
  await open(page, name)
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await expect(canvas.getByRole('button', { name: '阶段总览', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(canvas.locator('.workflow-stage')).toHaveCount(2)
  await expect(canvas.locator('.react-flow__node')).toHaveCount(5)
  await canvas.getByRole('button', { name: '展开阶段：并行实现', exact: true }).focus()
  await page.keyboard.press('Delete')
  await expect(canvas.locator('.react-flow__node')).toHaveCount(5)
  await expect(page.getByText('已保存版本 1', { exact: true })).toBeVisible()
  await canvas.getByRole('button', { name: '适配视图', exact: true }).click()
  const overview = info.outputPath('workflow-overview.png')
  await canvas.screenshot({ path: overview }); await info.attach('阶段总览', { path: overview, contentType: 'image/png' })
  await canvas.getByRole('button', { name: '展开阶段：并行实现', exact: true }).click()
  await expect(canvas.locator('.react-flow__node')).toHaveCount(6)
  await canvas.getByRole('button', { name: '展开循环：审查与修订', exact: true }).click()
  await expect(canvas.locator('.react-flow__node')).toHaveCount(7)
  await expect(canvas.getByRole('group', { name: '循环范围：审查与修订', exact: true })).toBeVisible()
  await canvas.getByRole('button', { name: '整理布局', exact: true }).click()
  await expect(canvas.getByText('返回下一轮 · 需要修订', { exact: true })).toBeVisible()
  await canvas.getByRole('button', { name: '撤销整理', exact: true }).click()
  await page.getByRole('button', { name: '保存流程', exact: true }).click()
  await expect(page.getByText('已保存版本 2', { exact: true })).toBeVisible()
  expect((await snapshot(page, cid)).definitions[0].graph).toEqual(initial)
  await canvas.getByRole('button', { name: '整理布局', exact: true }).click()
  await page.getByRole('button', { name: '保存流程', exact: true }).click()
  await expect(page.getByText('已保存版本 3', { exact: true })).toBeVisible()
  const organized = (await snapshot(page, cid)).definitions[0].graph
  const withoutPositions = (graph: typeof organized) => ({ ...graph, nodes: graph.nodes.map((node: { position: unknown }) => ({ ...node, position: null })) })
  expect(withoutPositions(organized)).toEqual(withoutPositions(initial))
  const positions = Object.fromEntries(organized.nodes.map((node: { id: string; position: { x: number; y: number } }) => [node.id, node.position]))
  expect(positions.core.x).toBe(positions.ui.x)
  expect(positions.design.x).toBeLessThan(positions.core.x)
  expect(positions.join.x).toBeGreaterThan(positions.core.x)
  await page.getByText('连线工具', { exact: true }).click()
  await page.getByLabel('选择连线', { exact: true }).selectOption(JSON.stringify(['judge', 'end']))
  await page.getByLabel('分支显示名称', { exact: true }).fill('验收完成')
  await expect(canvas.getByRole('note')).toContainText('checked = true')
  await page.getByRole('button', { name: '保存流程', exact: true }).click()
  await expect(page.getByText('已保存版本 4', { exact: true })).toBeVisible()
  await open(page, name, true)
  await canvas.getByRole('button', { name: '节点细节', exact: true }).click()
  await canvas.getByRole('button', { name: '适配视图', exact: true }).click()
  await expect(canvas.getByText('验收完成', { exact: true })).toBeVisible()
  const details = info.outputPath('workflow-details.png')
  await canvas.screenshot({ path: details }); await info.attach('并行、分支和循环', { path: details, contentType: 'image/png' })
  await page.setViewportSize({ width: 430, height: 900 })
  if (await page.getByRole('button', { name: '收起侧边栏', exact: true }).isVisible()) await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await expect(canvas.getByRole('toolbar', { name: '画布视图操作' })).toBeVisible()
  const bounds = await canvas.getByRole('toolbar', { name: '画布视图操作' }).boundingBox()
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(431)
  await canvas.getByRole('button', { name: '阶段总览', exact: true }).click()
  await canvas.getByLabel('定位节点', { exact: true }).selectOption('core')
  await expect(canvas.getByRole('button', { name: '节点 2：实现核心逻辑并核对较长的模块职责说明', exact: true })).toBeInViewport()
  const narrow = info.outputPath('workflow-narrow.png')
  await page.screenshot({ path: narrow }); await info.attach('窄屏定位任务', { path: narrow, contentType: 'image/png' })
})

test('运行折叠仍显示反馈等待，聚焦与整理视图不改运行版本', async ({ page }, info) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1920, height: 1100 }); await ensureOwnerSession(page)
  const name = '运行总览反馈验收', { cid } = await fixture(page, name)
  await open(page, name)
  await page.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(page.getByText('等待反馈处置 · 定义快照 v1', { exact: true })).toBeVisible({ timeout: 30_000 })
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  const group = canvas.getByRole('group', { name: '循环：审查与修订', exact: true })
  await expect(group).toContainText('待处理反馈 1')
  await expect(group).toContainText('等待 1')
  await expect(group).toContainText('第 1 / 3 轮')
  await canvas.getByRole('button', { name: '适配视图', exact: true }).click()
  const shot = info.outputPath('workflow-feedback-overview.png')
  await canvas.screenshot({ path: shot }); await info.attach('折叠后的真实反馈等待', { path: shot, contentType: 'image/png' })
  const before = (await snapshot(page, cid)).runs[0]
  await canvas.getByRole('button', { name: '整理视图', exact: true }).click()
  await canvas.getByRole('button', { name: '撤销整理', exact: true }).click()
  const issue = page.getByRole('article', { name: '反馈：两个验收数字冲突', exact: true })
  await issue.getByRole('button', { name: '定位处理路径', exact: true }).click()
  await expect(canvas.getByLabel('画布关系')).toHaveValue('feedback')
  await expect(canvas.getByText(/尚未关联处理节点/)).toBeVisible()
  const after = (await snapshot(page, cid)).runs[0]
  expect(after.graph_revision).toBe(before.graph_revision)
  expect(after.graph).toEqual(before.graph)
  expect(after.attempts.map((attempt: { id: string }) => attempt.id)).toEqual(before.attempts.map((attempt: { id: string }) => attempt.id))
  await page.getByRole('button', { name: '停止此运行', exact: true }).click()
  await expect(page.getByText('已停止 · 定义快照 v1', { exact: true })).toBeVisible()
})
