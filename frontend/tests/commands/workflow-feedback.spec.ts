import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  return page.evaluate(async ({ name, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    async function request(route: string, body?: unknown, method = 'POST') {
      const response = await fetch(base + '/api' + route, { headers, method: body ? method : 'GET', body: body ? JSON.stringify(body) : undefined })
      if (!response.ok) throw new Error(`feedback fixture ${response.status}`)
      return response.json()
    }
    const config = await request('/model-configs', { name, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const roles = []
    for (const label of ['审查', '裁定', '协调']) roles.push(await request('/roles', {
      name: `${name}${label}`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控反馈协作', builtin_tools: [],
    }))
    const budget = await request('/agent-budget/config')
    await request('/agent-budget/config', { decision_limit: 64, expected_revision: budget.revision }, 'PUT')
    const group = await request('/conversations', { type: 'group', title: name, role_ids: roles.map(role => role.id) })
    await request(`/conversations/${group.id}/orchestrator`, { role_id: roles[2].id, expected_revision: group.revision }, 'PUT')
    await request(`/conversations/${group.id}/workflows/definitions/${crypto.randomUUID()}`, {
      name: '反馈闭环', expected_revision: 0, graph: { runtime_version: 2, nodes: [
        { id: 'review', kind: 'role', title: '审查契约', role_id: roles[0].id, task: '[FEEDBACK_REPORT]', tools: [] },
        { id: 'deliver', kind: 'role', title: '交付', role_id: roles[1].id, task: '简述交付完成', tools: [] },
      ], edges: [['review', 'deliver']] },
    }, 'PUT')
    return group.id as number
  }, { name, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}

async function openDefinition(page: Page, name: string) {
  await page.reload()
  await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: '反馈闭环 v1', exact: true }).click()
}

async function snapshot(page: Page, cid: number) {
  return page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).json(), { cid, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}

test('人工交给协调者局部修正、来源补充反馈和处置版本冲突', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1600, height: 1000 }); await ensureOwnerSession(page)
  const name = '反馈人工交接验收', cid = await fixture(page, name)
  await openDefinition(page, name)
  await page.getByRole('button', { name: '启动流程', exact: true }).click()
  const panel = page.getByRole('region', { name: '节点反馈', exact: true })
  const issue = panel.getByRole('article', { name: '反馈：两个验收数字冲突', exact: true })
  await expect(issue).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('等待反馈处置 · 定义快照 v1', { exact: true })).toBeVisible()
  await issue.getByText('处理这条反馈', { exact: true }).click()
  await issue.getByRole('textbox', { name: '处置依据', exact: true }).fill('请安排责任角色裁定冲突，再核对验证结果。')
  await issue.getByRole('button', { name: '交给协调者处理', exact: true }).click()
  await expect(page.getByText('本次执行结束 · 定义快照 v1', { exact: true })).toBeVisible({ timeout: 45_000 })
  await panel.getByLabel('显示已处置').check()
  await expect(issue.getByText('已解决', { exact: true }).first()).toBeVisible()
  const run = (await snapshot(page, cid)).runs[0]
  expect(run.graph_revision).toBe(2)
  expect(run.attempts.filter((a: { node_id: string }) => a.node_id === 'review')).toHaveLength(1)
  expect((await snapshot(page, cid)).coordinations).toHaveLength(2)
  await issue.getByRole('button', { name: '查看反馈来源', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '节点尝试详情' })
  await dialog.getByText('提交节点反馈', { exact: true }).click()
  await dialog.getByLabel('反馈类型').selectOption('capability')
  await dialog.getByLabel('反馈标题').fill('需要运行验证能力')
  await dialog.getByLabel('问题依据').fill('本节点没有实际执行环境，不能宣称运行验证通过。')
  await dialog.getByLabel('需要的工具').fill('workspace_run_shell')
  await dialog.getByRole('button', { name: '提交反馈', exact: true }).click()
  await expect(dialog.getByText('反馈已登记，可在“节点反馈”中查看和处置。', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '关闭尝试详情' }).click()
  const capability = panel.getByRole('article', { name: '反馈：需要运行验证能力' })
  await expect(capability.getByText('当前未授权：workspace_run_shell', { exact: true })).toBeVisible()
  await capability.getByText('处理这条反馈', { exact: true }).click()
  await capability.getByRole('textbox', { name: '处置依据', exact: true }).fill('等待 Owner 配置验证能力。')
  let raced = false
  await page.route('**/workflows/runs/*/feedback/*/actions', async route => {
    if (!raced) {
      raced = true
      const body = route.request().postDataJSON()
      const response = await page.request.post(route.request().url(), { headers: route.request().headers(),
        data: { ...body, request_key: crypto.randomUUID(), action: 'wait', reason: '另一个窗口已登记等待条件' } })
      expect(response.ok()).toBe(true)
    }
    await route.continue()
  })
  await capability.getByRole('button', { name: '记录处置', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '反馈已被其他操作更新' })).toBeVisible()
  await page.unroute('**/workflows/runs/*/feedback/*/actions')
  await capability.getByLabel('处置方式').selectOption('resolve')
  await capability.getByRole('textbox', { name: '处置依据', exact: true }).fill('Owner 已在受控环境完成验证并核对结果。')
  await capability.getByLabel('我已完成人工核验，并在处置依据中记录证据').check()
  await capability.getByRole('button', { name: '记录处置', exact: true }).click()
  await expect(capability.getByText('已解决', { exact: true }).first()).toBeVisible()
  const screenshot = testInfo.outputPath('feedback-disposition.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('反馈处置界面', { path: screenshot, contentType: 'image/png' })
})

test('协调执行显式授权自动反馈处置并完成实际局部补图', async ({ page }) => {
  test.setTimeout(100_000)
  await page.setViewportSize({ width: 1600, height: 1000 }); await ensureOwnerSession(page)
  const name = '反馈自动处置验收', cid = await fixture(page, name)
  await openDefinition(page, name)
  await page.getByLabel('协调执行时自动处理节点反馈').check()
  await page.getByRole('button', { name: '协调执行', exact: true }).click()
  await expect(page.getByText('本次执行结束 · 定义快照 v2', { exact: true })).toBeVisible({ timeout: 60_000 })
  const run = (await snapshot(page, cid)).runs[0]
  expect(run.feedback_mode).toBe('automatic')
  expect(run.feedback[0].status).toBe('resolved')
  expect(run.attempts.find((attempt: { id: string }) => attempt.id === run.feedback[0].verification_attempt_id).status).toBe('completed')
  await page.getByLabel('显示已处置').check()
  await expect(page.getByRole('article', { name: '反馈：两个验收数字冲突' })).toContainText('已解决')
})
