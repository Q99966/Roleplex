import { test, expect } from '@playwright/test'
import { openWorkflowTemplate, saveWorkflow, startWorkflow, workflowInspector, workflowMore, workflowToolbar } from '../workflow-ui'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import { orchestrationFixture, orchestrationSnapshot } from '../orchestration-fixture'

test('群任命、任务工具与循环配置、协调并行返工和刷新历史', async ({ page }) => {
  test.setTimeout(150_000)
  page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1600, height: 1000 })
  await ensureOwnerSession(page)
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'orchestration')
  await mkdir(root, { recursive: true })
  const { cid, coordinator } = await orchestrationFixture(page, root)
  await page.reload()
  await page.getByRole('button', { name: '打开会话：并行返工协调验收', exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '会话成员', exact: true }).locator('span').last().click()
  await page.getByLabel('任命群协调者').selectOption(String(coordinator))
  await expect(page.getByLabel('任命群协调者')).toBeEnabled()
  await openWorkflowTemplate(page, '并行返工')
  await workflowMore(page, '高级设置')
  await page.getByText('并行与循环设置', { exact: true }).click()
  await page.getByLabel('工作流并发容量').fill('2')
  await page.getByLabel('循环次数上限 1').fill('3')
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await canvas.getByRole('button', { name: '节点细节', exact: true }).click()
  await canvas.getByRole('button', { name: '节点 1：开发', exact: true }).click()
  await workflowInspector(page).getByText('工具与结果规则', { exact: true }).click()
  await expect(workflowInspector(page).getByText('本任务工具分配', { exact: true })).toBeVisible()
  await expect(workflowInspector(page).getByLabel('workspace_write', { exact: true })).toBeChecked()
  await saveWorkflow(page)
  await expect(workflowToolbar(page).getByText('版本 2', { exact: true })).toBeVisible()
  await startWorkflow(page, true)
  await expect.poll(async () => (await orchestrationSnapshot(page, cid)).runs[0]?.status, { timeout: 110_000 }).toBe('completed')
  expect(await readFile(path.join(root, 'workflow-round.txt'), 'utf8')).toBe('round-2')
  const run = (await orchestrationSnapshot(page, cid)).runs[0]
  expect(run.loop_states.revision.iteration).toBe(1)
  expect(run.attempts.filter((a: { node_id: string }) => ['a','b'].includes(a.node_id))).toHaveLength(4)
  await page.reload()
  await expect(workflowToolbar(page).getByLabel('工作流对象')).toHaveValue(`run:${run.id}`)
    await expect(workflowToolbar(page).getByRole('status')).toContainText('本次执行结束')
})

test('未任命时禁止协调入口，人工等待期间取消任命封闭旧流程', async ({ page }) => {
  page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1600, height: 1000 })
  await ensureOwnerSession(page)
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'orchestration-revoke')
  await mkdir(root, { recursive: true })
  const { cid, did, coordinator } = await orchestrationFixture(page, root, false, '取消任命验收')
  await page.evaluate(async ({ cid, did, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const data = await (await fetch(`${base}/api/conversations/${cid}/workflows`, { headers })).json()
    const d = data.definitions.find((d: { id: string }) => d.id === did)
    d.graph.nodes.unshift({ id: 'human', title: '开始前确认', kind: 'approval', inputs: [] })
    d.graph.edges.unshift(['human', 'build'])
    const saved = await fetch(`${base}/api/conversations/${cid}/workflows/definitions/${did}`, { method: 'PUT', headers,
      body: JSON.stringify({ name: d.name, graph: d.graph, expected_revision: d.revision }) })
    if (!saved.ok) throw new Error('人工确认夹具保存失败')
  }, { cid, did, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
  await page.reload()
  await page.getByRole('button', { name: '打开会话：取消任命验收', exact: true }).click()
  async function module(name: string) {
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name, exact: true }).locator('span').last().click()
  }
  await module('工作流')
  await page.getByRole('button', { name: '打开模板：并行返工', exact: true }).click()
  await expect(workflowToolbar(page).getByLabel('运行方式').locator('option[value=coordinated]')).toBeDisabled()
  await module('会话成员')
  await page.getByLabel('任命群协调者').selectOption(String(coordinator))
  await expect(page.getByLabel('任命群协调者')).toBeEnabled()
  await module('工作流')
  await startWorkflow(page, true)
  await expect(workflowToolbar(page).getByRole('status')).toContainText('等待人工确认')
  await module('会话成员')
  await page.getByLabel('任命群协调者').selectOption('')
  await module('工作流')
  await expect(workflowToolbar(page).getByRole('status')).toContainText('受阻')
  const run = (await orchestrationSnapshot(page, cid)).runs[0]
  expect(run.status).toBe('blocked')
  expect(run.attempts.some((a: { node_id: string }) => a.node_id === 'build')).toBe(false)
  expect(await readFile(path.join(root, 'workflow-round.txt'), 'utf8').then(() => true, () => false)).toBe(false)
})
