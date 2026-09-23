import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page) {
  return page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown) => {
      const response = await fetch('/api' + url, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('controlled coordinator setup failed')
      return response.json()
    }
    const config = await post('/model-configs', { name: '世界协调验收模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/roles', { name: '世界协调验收角色', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控世界协调角色' })
    const conversation = await post('/conversations', { type: 'single', title: '保持主对话草稿', role_ids: [role.id] })
    return { role, conversation }
  })
}

test('固定管理者导入与编辑配置，独立对话和上下文不覆盖主聊天', async ({ page }, info) => {
  test.setTimeout(80000); page.setDefaultTimeout(15000)
  await page.setViewportSize({ width: 1680, height: 1050 }); await ensureOwnerSession(page)
  const { role } = await fixture(page)
  await page.reload(); await page.getByRole('button', { name: '打开会话：保持主对话草稿', exact: true }).click()
  const mainInput = page.getByRole('textbox', { name: '消息输入框', exact: true })
  await mainInput.fill('主聊天尚未发送的草稿')
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '世界协调面板', exact: true })
  await panel.getByLabel('导入世界管理者配置', { exact: true }).selectOption(String(role.id))
  await panel.getByLabel('世界协调输入', { exact: true }).fill('WORLD_COORDINATOR_OWNER_GOAL')
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await expect(panel.getByRole('region', { name: '世界协调对话' })).toContainText('收到你的消息：WORLD_COORDINATOR_OWNER_GOAL', { timeout: 20000 })
  await panel.getByLabel('世界协调输入', { exact: true }).fill('协调者草稿仍要保留')
  await panel.getByRole('button', { name: '详情', exact: true }).click()
  await expect(panel.getByRole('region', { name: '会话上下文管理' })).toBeVisible()
  const shot = info.outputPath('world-coordinator-conversation.png')
  await page.screenshot({ path: shot }); await info.attach('独立世界协调与上下文', { path: shot, contentType: 'image/png' })
  await panel.getByRole('button', { name: '收起世界协调面板', exact: true }).click()
  await expect(mainInput).toHaveValue('主聊天尚未发送的草稿')
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  await expect(panel.getByLabel('世界协调输入', { exact: true })).toHaveValue('协调者草稿仍要保留')
  await expect(panel).toContainText('WORLD_COORDINATOR_OWNER_GOAL')
  const managerId = await page.evaluate(async () => {
    const response = await fetch('/api/world-orchestrator', { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
    return (await response.json()).role_id
  })
  expect(managerId).not.toBe(role.id)
  await panel.getByRole('button', { name: '世界管理者设置', exact: true }).click()
  await panel.getByRole('button', { name: '配置管理者模型与人设', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '角色配置', exact: true })
  await expect(editor.getByRole('button', { name: '删除角色', exact: true })).toHaveCount(0)
  await editor.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(editor).toHaveCount(0)
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  await expect(panel.getByLabel('世界协调输入', { exact: true })).toHaveValue('协调者草稿仍要保留')
  expect(await page.evaluate(async () => {
    const response = await fetch('/api/world-orchestrator', { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
    return (await response.json()).role_id
  })).toBe(managerId)
  await panel.getByRole('button', { name: '收起世界协调面板', exact: true }).click()
  await page.getByRole('button', { name: '桌宠互动与设置', exact: true }).click()
  await page.getByRole('menuitem', { name: '投喂', exact: true }).click()
  await expect(mainInput).toHaveValue('主聊天尚未发送的草稿')
  await expect(page.getByText('更换外观', { exact: true })).toHaveCount(0)
})

test('世界任务实际委派两群，流程钻取、根用量和世界记忆均可操作', async ({ page }, info) => {
  test.setTimeout(100000); page.setDefaultTimeout(15000)
  await page.setViewportSize({ width: 1680, height: 1050 }); await ensureOwnerSession(page)
  await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const call = async (url: string, method = 'GET', body?: unknown) => {
      const response = await fetch('/api' + url, { method, headers, ...(body ? { body: JSON.stringify(body) } : {}) })
      if (!response.ok) throw new Error(`controlled world task setup ${response.status}`)
      return response.json()
    }
    const budget = await call('/agent-budget/config')
    await call('/agent-budget/config', 'PUT', { decision_limit: 64, expected_revision: budget.revision })
    const config = await call('/model-configs', 'POST', { name: '世界任务模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const worker = await call('/roles', 'POST', { name: '世界任务工作角色', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控工作角色' })
    const coordinator = await call('/roles', 'POST', { name: '世界任务协调角色', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控协调角色' })
    for (const title of ['世界委派甲群', '世界委派乙群']) {
      const group = await call('/conversations', 'POST', { type: 'group', title, role_ids: [worker.id, coordinator.id] })
      await call(`/conversations/${group.id}/orchestrator`, 'PUT', { role_id: coordinator.id, expected_revision: 0 })
      await call(`/conversations/${group.id}/workflows/definitions/${crypto.randomUUID().replaceAll('-', '')}`, 'PUT', {
        name: '受控世界委派模板', expected_revision: 0, graph: { runtime_version: 2, nodes: [{ id: 'work', kind: 'role', title: '完成受控工作', role_id: worker.id, task: '完成本群的受控任务。', tools: [] }], edges: [] },
      })
    }
    const appointment = await call('/world-orchestrator')
    await call('/world-orchestrator/import-role', 'POST', { role_id: coordinator.id, expected_revision: appointment.revision })
  })
  await page.reload(); await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '世界协调面板', exact: true })
  await panel.getByLabel('世界协调发送方式', { exact: true }).selectOption('execute')
  await panel.getByLabel('世界协调输入', { exact: true }).fill('[WORLD_TASK_PROBE] 安排甲乙两群并汇总结果')
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await expect(panel.getByLabel('当前世界任务', { exact: true }).locator('option').first()).toContainText('已完成', { timeout: 40000 })
  await panel.getByRole('button', { name: '流程', exact: true }).click()
  await expect(panel.getByLabel('世界任务流程图', { exact: true })).toBeVisible()
  await expect(panel.getByRole('region', { name: '世界任务详情', exact: true })).toContainText('两个群已完成真实流程')
  const shot = info.outputPath('world-task-flow.png')
  await page.screenshot({ path: shot }); await info.attach('跨群任务与真实状态', { path: shot, contentType: 'image/png' })
  await panel.getByLabel('协调详情内容', { exact: true }).selectOption('usage')
  await expect(panel.getByRole('region', { name: '世界任务用量', exact: true })).toContainText('厂商输入 未知')
  await panel.getByRole('button', { name: '世界记忆', exact: true }).click()
  const memory = panel.getByRole('region', { name: '世界记忆', exact: true })
  await memory.getByLabel('世界记忆内容').fill('WORLD_MEMORY_BROWSER_CHECK 优先核对原始结果')
  await memory.getByRole('button', { name: '保存世界记忆', exact: true }).click()
  await expect(memory.getByRole('article')).toContainText('WORLD_MEMORY_BROWSER_CHECK')
  await memory.getByRole('button', { name: '停用', exact: true }).click()
  await expect(memory.getByRole('button', { name: '查看并恢复', exact: true })).toBeVisible()
  await panel.getByLabel('协调详情内容', { exact: true }).selectOption('task')
  await panel.getByRole('button', { name: /世界委派甲群 · 已完成/ }).click()
  await panel.getByRole('button', { name: '打开群流程', exact: true }).click()
  await expect(page.getByRole('region', { name: '工作流画布', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  await expect(panel.getByLabel('世界任务流程图', { exact: true })).toBeVisible()
  await expect(panel.getByRole('region', { name: '世界任务详情', exact: true })).toContainText('世界委派甲群')
})

test('窄屏补充同一任务、刷新历史及停止，额度不会因续办重置', async ({ page }) => {
  test.setTimeout(80000); page.setDefaultTimeout(15000)
  await page.setViewportSize({ width: 390, height: 844 }); await ensureOwnerSession(page)
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '世界协调面板', exact: true })
  await panel.getByLabel('世界协调发送方式', { exact: true }).selectOption('execute')
  await panel.getByLabel('世界协调输入', { exact: true }).fill('WORLD_FOLLOWUP_GOAL 等待补充要求')
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await panel.getByRole('button', { name: '关闭协调详情', exact: true }).click()
  await expect(panel.getByRole('region', { name: '世界协调对话' })).toContainText('收到你的消息：WORLD_FOLLOWUP_GOAL', { timeout: 20000 })
  await expect(panel.getByLabel('当前世界任务', { exact: true }).locator('option').first()).toContainText('待处理')
  const before = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    return (await (await fetch('/api/world-orchestrator/tasks', { headers })).json()).items[0]
  })
  await panel.getByLabel('世界协调发送方式', { exact: true }).selectOption('continue')
  const requestIds: string[] = []
  let dropped = false
  await page.route('**/api/conversations/*/messages', async route => {
    const body = route.request().method() === 'POST' ? route.request().postDataJSON() : null
    if (!body?.parts?.[0]?.text?.startsWith('WORLD_FOLLOWUP_DETAILS')) { await route.continue(); return }
    requestIds.push(body.client_message_id)
    const response = await route.fetch()
    if (!dropped) { dropped = true; await route.abort('failed') } else await route.fulfill({ response })
  })
  await panel.getByLabel('世界协调输入', { exact: true }).fill('WORLD_FOLLOWUP_DETAILS 补充准确要求')
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await expect(panel.getByRole('region', { name: '世界协调对话' })).toContainText('收到你的消息：WORLD_FOLLOWUP_DETAILS', { timeout: 20000 })
  await expect(panel.getByRole('alert')).toBeVisible()
  await panel.getByRole('button', { name: '收起世界协调面板', exact: true }).click()
  await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  await expect(panel.getByLabel('世界协调输入')).toHaveValue('WORLD_FOLLOWUP_DETAILS 补充准确要求')
  await expect(panel.getByRole('button', { name: '发送世界协调消息', exact: true })).toBeEnabled()
  await panel.getByRole('button', { name: '发送世界协调消息', exact: true }).click()
  await expect(panel.getByLabel('世界协调输入')).toHaveValue('')
  expect(requestIds).toHaveLength(2); expect(requestIds[0]).toBe(requestIds[1])
  const after = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    return (await (await fetch('/api/world-orchestrator/tasks', { headers })).json()).items[0]
  })
  expect(after.id).toBe(before.id); expect(after.chain_id).toBe(before.chain_id)
  expect(after.used_decisions).toBeGreaterThan(before.used_decisions)
  await page.reload(); await page.getByRole('button', { name: '打开世界协调面板', exact: true }).click()
  await expect(panel.getByRole('region', { name: '世界协调对话' })).toContainText('WORLD_FOLLOWUP_DETAILS')
  await panel.getByRole('button', { name: '停止本次任务', exact: true }).click()
  await expect(panel.getByLabel('当前世界任务', { exact: true }).locator('option').first()).toContainText('已停止')
  await expect(panel.getByRole('region', { name: '世界协调对话' })).toContainText('WORLD_FOLLOWUP_GOAL')
  await page.keyboard.press('Escape'); await expect(panel).toHaveCount(0)
})
