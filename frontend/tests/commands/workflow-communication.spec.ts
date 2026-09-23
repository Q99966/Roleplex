import { test, expect } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

test('世界委派署名准确，同批三角色只有一条广播且可查看独立输入', async ({ page }, info) => {
  test.setTimeout(90000); page.setDefaultTimeout(15000)
  await page.setViewportSize({ width: 1500, height: 980 }); await ensureOwnerSession(page)
  const fixture = await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const api = async (url: string, method = 'GET', body?: unknown) => {
      const response = await fetch('/api' + url, { method, headers, ...(body ? { body: JSON.stringify(body) } : {}) })
      if (!response.ok) throw new Error(`communication fixture ${response.status}`)
      return response.json()
    }
    const budget = await api('/agent-budget/config')
    await api('/agent-budget/config', 'PUT', { decision_limit: 64, expected_revision: budget.revision })
    const config = await api('/model-configs', 'POST', { name: '通信验收模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const roles = []
    for (const label of ['A', 'B', 'C']) roles.push(await api('/roles', 'POST', { name: `通信角色 ${label}`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控协作角色' }))
    const group = await api('/conversations', 'POST', { type: 'group', title: '协作署名验收', role_ids: roles.map(role => role.id) })
    await api(`/conversations/${group.id}/orchestrator`, 'PUT', { role_id: roles[0].id, expected_revision: 0 })
    await api(`/conversations/${group.id}/workflows/definitions/${crypto.randomUUID().replaceAll('-', '')}`, 'PUT', { name: '三角色同批任务', expected_revision: 0,
      graph: { runtime_version: 2, concurrency: 2, entries: ['a','b','c'], nodes: roles.map((role, index) => ({ id: ['a','b','c'][index], kind: 'role',
        role_id: role.id, title: `任务 ${index + 1}`, task: `[COMMUNICATOR_WORK_PROBE] PRIVATE_NODE_${index}`, tools: [], result_keys: ['completed'] })), edges: [] } })
    const manager = await api('/world-orchestrator')
    const configured = await api('/world-orchestrator/import-role', 'POST', { role_id: roles[0].id, expected_revision: manager.revision })
    return { manager: configured.role_id, cid: group.id, roleIds: roles.map(role => role.id) }
  })
  await page.reload(); await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '世界协调面板', exact: true })
  await panel.getByLabel('世界协调发送方式').selectOption('execute')
  await panel.getByLabel('世界协调输入').fill('[COMMUNICATOR_WORLD_PROBE] 安排这次协作')
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await expect(panel.getByLabel('当前世界任务').locator('option').first()).toContainText('已完成', { timeout: 45000 })
  await panel.getByRole('button', { name: '收起世界协调面板', exact: true }).click()
  await page.getByRole('button', { name: '打开会话：协作署名验收', exact: true }).click()
  const feed = page.getByRole('log', { name: '会话消息' })
  const cards = feed.getByRole('region', { name: '工作流任务派发', exact: true })
  const work = cards.filter({ hasText: '按流程完成本批分工' })
  await expect(work).toHaveCount(1)
  await expect(work.getByText('已完成', { exact: true })).toHaveCount(3)
  await expect(feed.getByText(/WORLD_PUBLIC_TASK_TARGET/)).toHaveCount(1)
  const targets = work.locator('..').getByLabel('消息接收对象')
  for (const label of ['A','B','C']) await expect(targets).toContainText(`@通信角色 ${label}`)
  await expect(feed.getByText(/PRIVATE_NODE_/)).toHaveCount(0)
  const factual = await page.evaluate(async cid => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    return (await (await fetch(`/api/conversations/${cid}/messages`, { headers })).json()).items
  }, fixture.cid)
  expect(factual.find((m: any) => m.communication?.kind === 'delegation').communication.actor.kind).toBe('world_manager')
  expect(factual.filter((m: any) => m.sender_type === 'user')).toHaveLength(0)
  await work.getByText('分工与完整执行输入', { exact: true }).click()
  await work.getByRole('button', { name: '查看 通信角色 A 的输入', exact: true }).click()
  await expect(work.locator('pre')).toContainText('PRIVATE_NODE_0')
  await expect(work.locator('pre')).toContainText('WORLD_PUBLIC_TASK_TARGET')
  await work.getByText('分工与完整执行输入', { exact: true }).click()
  const shot = info.outputPath('workflow-broadcast.png'); await feed.screenshot({ path: shot }); await info.attach('真实署名与多角色任务广播', { path: shot, contentType: 'image/png' })
  await page.reload()
  await expect(page.getByRole('region', { name: '工作流任务派发' }).filter({ hasText: '按流程完成本批分工' })).toHaveCount(1)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('region', { name: '工作流任务派发' }).filter({ hasText: '按流程完成本批分工' })).toBeVisible()
})
