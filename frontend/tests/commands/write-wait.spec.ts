import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import { readRunEvents } from '../e2e-log-assertions'

test('Shell 审批等待不阻塞同轮写入，拒绝审批不回滚已提交文件', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'write-wait')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { headers, method, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('写入等待测试准备失败')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: 'write-wait', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '并发写入助手', system_prompt: '执行受控工具。',
      model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_run_shell', 'workspace_write'] })
    const workspace = await request('/api/workspaces', { display_name: '写入等待工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true, file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '并发写入验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('并发写入验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[SHELL_WRITE_WAIT_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible()
  await expect.poll(() => readFile(path.join(root, 'pending/proof.txt'), 'utf8').catch(() => ''), { timeout: 5000 }).toBe('written-before-approval')
  const card = page.getByTestId('tool-call-card').filter({ has: page.getByRole('button', { name: '执行详情：workspace_write', exact: true }) })
  await expect(card).toContainText('已完成')
  await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible()
  const screenshot = testInfo.outputPath('write-while-approval-pending.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('审批未决定时写入已完成', { path: screenshot, contentType: 'image/png' })
  await page.getByRole('button', { name: '拒绝本次 Shell', exact: true }).click()
  await expect(page.getByText('审批与写入验证完成。', { exact: true })).toBeVisible()
  expect(await readFile(path.join(root, 'pending/proof.txt'), 'utf8')).toBe('written-before-approval')
  const events = await readRunEvents()
  const waits = events.filter((event) => event.event === 'tool.write_wait_completed' && event.conversation_id === cid)
  expect(waits).toHaveLength(1)
  expect(waits[0].status).toBe('success')
  expect(JSON.stringify(events)).not.toContain('written-before-approval')
  expect(JSON.stringify(events)).not.toContain('pending/proof.txt')
})
