import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('连续探索保留真实顺序、折叠选择、失败和私有详情，重连刷新及 Guest 降级', async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    const Original = window.WebSocket
    const sockets: WebSocket[] = []
    ;(window as any).__explorationSockets = sockets
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) { super(url, protocols); sockets.push(this) }
    }
  })
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'exploration')
  await mkdir(root)
  await writeFile(path.join(root, 'explore.txt'), 'EXPLORATION-PRIVATE-PLACEHOLDER')
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('探索测试准备失败')
      return response.json()
    }
    const config = await request('/api/model-configs', { name: 'exploration', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '探索助手', model_config_id: config.id, model_name: 'fake-model',
      system_prompt: '使用原生只读工具。', builtin_tools: ['workspace_read', 'workspace_list'] })
    const workspace = await request('/api/workspaces', { display_name: '探索工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '探索归组验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('探索归组验收', { exact: true }).click()
  let requests = 0
  page.on('request', (request) => { if (request.url().includes('/tools/')) requests++ })
  await page.getByLabel('消息输入框').fill('[EXPLORE_FAKE]')
  await page.getByLabel('发送消息').click()
  const reply = page.getByTestId('chat-message').nth(1)
  const group = reply.getByTestId('exploration-group')
  const toggle = group.getByRole('button', { name: '探索记录', exact: true })
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()
  await page.evaluate(() => (window as any).__explorationSockets.at(-1).close())
  await expect(reply.getByText('探索验收完成。', { exact: true })).toBeVisible()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(toggle).toContainText('读取 3 次 · 列目录 1 次 · 1 项失败')
  expect(requests).toBe(0)
  await toggle.click()
  await expect(group.getByTestId('tool-call-card')).toHaveCount(4)
  await expect(reply.getByTestId('tool-call-card')).toHaveCount(5)
  const order = await reply.locator('[data-testid="tool-call-card"], [data-testid="message-text-part"]').evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute('data-testid')))
  expect(order).toEqual(['tool-call-card', 'tool-call-card', 'tool-call-card', 'tool-call-card',
    'message-text-part', 'tool-call-card', 'message-text-part'])
  const read = group.getByRole('button', { name: '执行详情：workspace_read', exact: true }).first()
  await read.click()
  await expect(group.getByRole('region', { name: '工具输出' })).toContainText('EXPLORATION-PRIVATE-PLACEHOLDER')
  await toggle.click()
  await toggle.click()
  await expect(read).toHaveAttribute('aria-expanded', 'true')
  await read.click()
  const shot = testInfo.outputPath('exploration.png')
  await group.screenshot({ path: shot })
  await testInfo.attach('探索归组与独立失败', { path: shot, contentType: 'image/png' })
  await page.reload()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(toggle).toContainText('1 项失败')
  await expect(reply.getByTestId('tool-call-card')).toHaveCount(5)
  // 用真实组内锚点模拟正在阅读子项，验证折叠不会拿隐藏元素的零矩形跳到页首。
  const groupAnchor = await group.getAttribute('data-reading-anchor')
  await group.evaluate(async (element, cid) => {
    const anchor = element.querySelector<HTMLElement>('[data-reading-anchor]')!
    // @ts-expect-error Vite 浏览器源码入口，仅用于断言现有阅读状态。
    const store = (await import('/src/store/chat.ts')).useChatStore
    store.getState().setPosition(cid, { anchor: anchor.dataset.readingAnchor, offset: -12, bottom: false })
    element.querySelector<HTMLButtonElement>('button[aria-label="探索记录"]')!.click()
  }, cid)
  await expect.poll(() => page.evaluate(async () => {
    // @ts-expect-error Vite 浏览器源码入口。
    return (await import('/src/store/chat.ts')).useChatStore.getState().position.anchor
  })).toBe(groupAnchor)
  await toggle.click()
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await toggle.scrollIntoViewIfNeeded()
  await expect.poll(() => group.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const narrow = testInfo.outputPath('exploration-narrow.png')
  await group.screenshot({ path: narrow })
  await testInfo.attach('窄屏探索归组', { path: narrow, contentType: 'image/png' })
  await page.setViewportSize({ width: 1280, height: 720 })
  // 新一轮产生独立消息，不与前一轮的最后一个 List 合并。
  await page.getByLabel('消息输入框').fill('[EXPLORE_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('探索验收完成。', { exact: true })).toHaveCount(2)
  await expect(page.getByTestId('exploration-group')).toHaveCount(2)
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ cid, stamp }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
    location.hash = `#/workspace/conversation/${cid}`
  }, { cid, stamp: process.env.ROLEPLEX_COMMAND_E2E_STAMP })
  requests = 0
  await page.reload()
  await expect(page.getByTestId('exploration-group')).toHaveCount(2)
  await page.getByRole('button', { name: '执行详情：workspace_read', exact: true }).first().click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '工具输出' })).toHaveCount(0)
  expect(requests).toBe(0)
})
