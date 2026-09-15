import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('隐藏执行摘要但保留工具卡与后台事实，断线刷新及 Guest 权限不变', async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    const Original = window.WebSocket
    const sockets: WebSocket[] = []
    ;(window as any).__factSockets = sockets
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) { super(url, protocols); sockets.push(this) }
    }
  })
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'execution-summary')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('执行记录测试准备失败')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: 'facts', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '执行记录助手', model_config_id: config.id, model_name: 'fake-model',
      system_prompt: '按实际执行事实回答。', builtin_tools: ['workspace_write', 'workspace_list'] })
    const workspace = await request('/api/workspaces', { display_name: '执行记录工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '可信执行验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('可信执行验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[EXECUTION_FACTS_FAKE]')
  await page.getByLabel('发送消息').click()
  const summary = page.getByRole('region', { name: '系统执行记录', exact: true })
  await expect(summary).toHaveCount(0)
  const reply = page.getByTestId('chat-message').nth(1)
  await expect(reply.getByTestId('tool-call-card').first()).toBeVisible()
  await page.evaluate(() => (window as any).__factSockets.at(-1).close())
  await expect(reply.getByText('达到本轮决策上限', { exact: true })).toBeVisible({ timeout: 20000 })
  await expect(summary).toHaveCount(0)
  await expect(reply).not.toContainText('已确认文件提交')
  await expect(reply).not.toContainText('未确认不等于未执行')
  expect(await readFile(path.join(root, 'facts-proof.txt'), 'utf8')).toBe('controlled-facts')
  const saved = await page.evaluate(async (cid) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const history = await (await fetch(`/api/conversations/${cid}/messages`, { headers })).json()
    const message = history.items.find((item: { sender_type: string }) => item.sender_type === 'role')
    const hasSummary = message.parts_json.some((part: { type: string }) => part.type === 'execution_summary')
    const call = message.parts_json.find((part: { tool_name?: string }) => part.tool_name === 'workspace_write')
    return { messageId: message.id, callId: call.call_id, revision: message.revision, stopReason: message.stop_reason, hasSummary, applied: call.confirmed_applied_items }
  }, cid)
  expect(saved.stopReason).toBe('decision_budget')
  expect(saved.applied).toBe(1)
  expect(saved.hasSummary).toBe(false)
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 850 })
    if (width === 390) await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
    await reply.scrollIntoViewIfNeeded()
    await expect.poll(() => reply.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    const shot = testInfo.outputPath(`execution-summary-${width}.png`)
    await page.screenshot({ path: shot })
    await testInfo.attach(`移除摘要后的聊天 ${width}`, { path: shot, contentType: 'image/png' })
  }
  await page.reload()
  await expect(summary).toHaveCount(0)
  await expect(reply).not.toContainText('已确认文件提交')
  await expect(reply).not.toContainText('未确认不等于未执行')
  await expect(summary).toHaveCount(0)
  // 经真实 store 消费旧 revision 的事件，验证摘要不被迟到状态覆盖。
  await page.evaluate(async ({ cid, saved }) => {
    // @ts-expect-error Vite 源码入口用于检查与产品相同的事件 reducer。
    const { useChatStore, applyEvent } = await import('/src/store/chat.ts')
    const current = useChatStore.getState().messages.find((message: { id: number }) => message.id === saved.messageId)
    applyEvent(useChatStore.setState, useChatStore.getState, { type: 'message_part_update', conversation_id: cid,
      revision: saved.revision - 1, payload: { message: { ...current, revision: saved.revision - 1, parts_json: [] } } })
  }, { cid, saved })
  await expect(reply.getByText('达到本轮决策上限', { exact: true })).toBeVisible()
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  await page.reload()
  await expect(summary).toHaveCount(0)
  const denied = await page.evaluate(async ({ cid, saved }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const detail = await fetch(`/api/conversations/${cid}/messages/${saved.messageId}/tools/${saved.callId}`, { headers })
    const forged = await fetch(`/api/conversations/${cid}/messages`, { method: 'POST', headers,
      body: JSON.stringify({ parts: [{ type: 'text', text: 'probe' }, { type: 'execution_summary', version: 1 }] }) })
    return { detail: detail.status, forged: forged.status }
  }, { cid, saved })
  expect(denied).toEqual({ detail: 403, forged: 422 })
})
