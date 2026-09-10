import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { ensureOwnerSession } from '../owner'
import { readRunEvents } from '../e2e-log-assertions'

test('Shell 等待 Owner，刷新恢复、批准、拒绝和停止均不重复执行', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'shell-approvals')
  await mkdir(root)
  await ensureOwnerSession(page)
  const id = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { headers, method, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('Shell 测试准备失败')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: 'Shell 模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: 'Shell 助手', system_prompt: '请求批准后执行。',
      model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_run_shell'] })
    const workspace = await request('/api/workspaces', { display_name: 'Shell 工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: 'Shell 审批验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('Shell 审批验收', { exact: true }).click()
  const proof = path.join(root, 'shell-proof.txt')
  for (const action of ['approve', 'reject', 'stop', 'expire']) {
    await page.getByLabel('消息输入框').fill(action === 'expire' ? '[SHELL_EXPIRY_FAKE]' : '[SHELL_APPROVAL_FAKE]')
    await page.getByLabel('发送消息').click()
    await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible()
    if (action === 'approve') {
      expect(await readFile(proof).then(() => true).catch(() => false)).toBe(false)
      await page.reload()
      await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible()
      await expect(page.getByText(/审批不是系统沙箱/)).toBeVisible()
      await page.getByRole('button', { name: '批准本次 Shell', exact: true }).click()
      await expect.poll(() => readFile(proof, 'utf-8').catch(() => '')).toBe('approved\n')
    } else if (action === 'reject') await page.getByRole('button', { name: '拒绝本次 Shell', exact: true }).click()
    else if (action === 'stop') await page.getByRole('button', { name: '停止生成', exact: true }).click()
    await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '停止生成', exact: true })).toHaveCount(0)
    expect(await readFile(proof, 'utf-8')).toBe('approved\n')
    const cards = page.getByTestId('tool-call-card')
    const card = cards.last()
    await card.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).click()
    await expect(card.getByRole('region', { name: '执行脚本', exact: true })).toBeVisible()
    if (action === 'approve') {
      await expect(card.getByRole('region', { name: '标准输出', exact: true })).toContainText('（空内容）')
      await expect(card.getByText(/^实际执行耗时：\d+ms$/)).toBeVisible()
      // 模拟早期 Shell 消息没有能力标记；不得因此阻止 Owner 查询已有审批。
      const historyPattern = `**/api/conversations/${id}/messages?window=recent`
      await page.route(historyPattern, async (route) => {
        const response = await route.fetch()
        const body = await response.json()
        for (const message of body.items) for (const part of message.parts_json) {
          if (part.type === 'tool_call' && part.tool_name === 'workspace_run_shell') delete part.detail_available
        }
        await route.fulfill({ json: body })
      })
      await page.reload()
      await page.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).click()
      await expect(page.getByRole('region', { name: '执行脚本', exact: true })).toBeVisible()
      await expect(page.getByRole('region', { name: '标准输出', exact: true })).toContainText('（空内容）')
      await page.unroute(historyPattern)
    } else if (action === 'reject' || action === 'expire') {
      await expect(card.getByText('本次调用未执行，无进程输出。', { exact: true })).toBeVisible()
    }
  }
  await page.getByLabel('消息输入框').fill('[SHELL_DETAILS_FAKE]')
  await page.getByLabel('发送消息').click()
  await page.getByRole('button', { name: '批准本次 Shell', exact: true }).click()
  await expect(page.getByRole('button', { name: '停止生成', exact: true })).toHaveCount(0)
  const outputCard = page.getByTestId('tool-call-card').last()
  await outputCard.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).click()
  await expect(outputCard.getByRole('region', { name: '标准输出', exact: true })).toContainText('shell-stdout-placeholder')
  await expect(outputCard.getByRole('region', { name: '标准错误', exact: true })).toContainText('shell-stderr-placeholder')
  const outputScreenshot = testInfo.outputPath('shell-output-details.png')
  await page.screenshot({ path: outputScreenshot })
  await testInfo.attach('Shell 双流详情', { path: outputScreenshot, contentType: 'image/png' })
  const logs = await readRunEvents()
  const requests = logs.filter((event) => event.event === 'tool.approval_requested' && event.conversation_id === id)
  expect(requests).toHaveLength(5)
  expect(new Set(requests.map((event) => event.tool_call_id)).size).toBe(5)
  expect(logs.some((event) => event.event === 'tool.approval_resolved' && event.conversation_id === id && event.reason === 'expired')).toBe(true)
  const serialized = JSON.stringify(logs)
  expect(serialized.includes(root) || serialized.includes('shell-proof.txt')).toBe(false)
  expect(serialized.includes('shell-stdout-placeholder') || serialized.includes('shell-stderr-placeholder')).toBe(false)
  const detailRoute = await page.evaluate(async () => {
    const state = (await import('/src/store/chat.ts')).useChatStore.getState()
    const message = state.messages.find((item) => item.sender_type === 'role')!
    const call = message.parts_json.find((part) => part.type === 'tool_call')!
    return `/api/conversations/${message.conversation_id}/messages/${message.id}/tools/${call.call_id}`
  })
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(id)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.getByRole('button', { name: '退出登录', exact: true }).first().click()
  await page.goto('/#/auth')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByPlaceholder('owner').fill(`test${process.env.ROLEPLEX_E2E_STAMP}_toolviewer`)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Test-1234')
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
  await page.getByText('Shell 审批验收', { exact: true }).click()
  let requestsForDetails = 0
  page.on('request', (request) => { if (request.url().includes('/tools/')) requestsForDetails++ })
  await page.getByRole('button', { name: '执行详情：workspace_run_shell', exact: true }).first().click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '执行脚本', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '标准输出', exact: true })).toHaveCount(0)
  expect(requestsForDetails).toBe(0)
  expect(await page.evaluate(async (route) => (await fetch(route, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).status, detailRoute)).toBe(403)
})
