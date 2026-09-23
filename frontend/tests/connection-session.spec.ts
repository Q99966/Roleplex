import { expect, test, type Page } from '@playwright/test'
import { ensureOwnerSession, OWNER } from './owner'

/** 建立独立的两个空会话，用实际 API 准备可切换资源。
 * @param page 已登录测试页面。
 * @param prefix 本用例独占的资源名称前缀。
 */
async function seedPair(page: Page, prefix: string): Promise<number[]> {
  const ids = await page.evaluate(async (prefix) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown) => {
      const response = await fetch(route, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('Connection fixture failed: ' + response.status)
      return response.json()
    }
    const model = await post('/api/model-configs', { name: prefix, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: prefix, system_prompt: '验证会话连接。', model_config_id: model.id, model_name: 'fake-model' })
    const ids = []
    for (const suffix of ['A', 'B']) ids.push((await post('/api/conversations', { type: 'single', title: prefix + suffix, role_ids: [role.id] })).id)
    return ids
  }, prefix)
  await page.evaluate(async () => (await import('/src/store/app.ts')).useAppStore.getState().loadWorkspace())
  return ids
}

test('业务事件出现缺口时在原连接补齐，重复回放不重复正文', async ({ page }) => {
  let dropped = false
  let repeated = false
  let originalSubscription = ''
  const subscriptions = new Set<string>()
  let sockets = 0
  page.on('websocket', (socket) => { if (new URL(socket.url()).pathname === '/api/ws') sockets += 1 })
  await page.routeWebSocket('**/api/ws', (ws) => {
    const server = ws.connectToServer()
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      if (frame.type === 'subscribed') subscriptions.add(frame.subscription_id)
      if (frame.type === 'message_delta' && !dropped) {
        dropped = true
        originalSubscription = frame.subscription_id
        return
      }
      ws.send(raw)
      if (dropped && !repeated && frame.type === 'message_delta' && frame.subscription_id !== originalSubscription) {
        ws.send(raw)
        repeated = true
      }
    })
  })
  await ensureOwnerSession(page)
  await seedPair(page, '事件补齐')
  await page.getByText('事件补齐A', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('事件补齐验证')
  await page.getByLabel('发送消息').click()
  const reply = page.getByTestId('chat-message').nth(1)
  await expect(reply).toContainText('已收到你的消息：事件补齐验证')
  await expect(page.getByText('生成中…', { exact: true })).toHaveCount(0)
  await expect(page.getByText('正在同步会话', { exact: true })).toHaveCount(0)
  expect(dropped && repeated).toBe(true)
  expect(subscriptions.size).toBeGreaterThan(1)
  expect((await reply.textContent())?.match(/已收到你的消息/g)?.length).toBe(1)
  expect(sockets).toBe(1)
})

test('只在后端确认水位后就绪，旧订阅确认和错误不会改变新选择', async ({ page }, testInfo) => {
  const ready: Array<{ frame: any; send: (frame: any) => void }> = []
  await page.routeWebSocket('**/api/ws', (ws) => {
    const server = ws.connectToServer()
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      if (frame.type === 'sync_complete') ready.push({ frame, send: (value) => ws.send(JSON.stringify(value)) })
      else ws.send(raw)
    })
  })
  await ensureOwnerSession(page)
  const ids = await seedPair(page, '同步确认')
  await page.getByText('同步确认A', { exact: true }).click()
  await expect.poll(() => ready.filter((item) => item.frame.conversation_id === ids[0]).length).toBe(1)
  await page.getByLabel('消息输入框').fill('等待后端确认')
  await page.getByLabel('消息输入框').press('Enter')
  await expect(page.getByLabel('消息输入框')).toHaveValue('等待后端确认')
  await expect(page.getByText('正在同步会话', { exact: true })).toBeVisible()
  await expect(page.getByLabel('发送消息')).toBeDisabled()
  await page.getByText('同步确认B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '同步确认B', exact: true })).toBeVisible()
  await expect.poll(() => ready.filter((item) => item.frame.conversation_id === ids[1]).length).toBe(1)
  const old = ready.find((item) => item.frame.conversation_id === ids[0])!
  old.send({ ...old.frame, type: 'error', payload: { code: 'CONVERSATION_NOT_FOUND' } })
  old.send(old.frame)
  old.send({ ...old.frame, type: 'snapshot', payload: { conversation_id: ids[0], event_seq: 999,
    messages: [{ id: 999, parts_json: [{ type: 'text', text: '不应出现的旧会话' }] }] } })
  await page.getByLabel('消息输入框').fill('当前会话草稿')
  await expect(page.getByLabel('发送消息')).toBeDisabled()
  await expect(page.getByText('正在同步会话', { exact: true })).toBeVisible()
  await expect(page.getByText('CONVERSATION_NOT_FOUND', { exact: true })).toHaveCount(0)
  await expect(page.getByText('不应出现的旧会话')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/session-syncing.png', fullPage: true })
  const current = ready.find((item) => item.frame.conversation_id === ids[1])!
  current.send(current.frame)
  current.send(current.frame)
  await expect(page.getByLabel('发送消息')).toBeEnabled()
  await expect(page.getByText('正在同步会话', { exact: true })).toHaveCount(0)
  await testInfo.attach('订阅同步状态', { path: 'test-results/session-syncing.png', contentType: 'image/png' })
})

test('真实掉线恢复当前生成，正常切换不重连，退出登录关闭传输', async ({ page }) => {
  await page.addInitScript(() => {
    const Original = WebSocket
    ;(window as any).__sessionSockets = []
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        if (new URL(String(url), location.href).pathname === '/api/ws') (window as any).__sessionSockets.push(this)
      }
    }
  })
  await ensureOwnerSession(page)
  await seedPair(page, '掉线恢复')
  await page.getByText('掉线恢复A', { exact: true }).click()
  const prompt = '持续生成恢复验证'.repeat(15)
  await page.getByLabel('消息输入框').fill(prompt)
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('生成中…', { exact: true })).toBeVisible()
  await page.evaluate(() => (window as any).__sessionSockets.at(-1).close())
  await expect(page.getByText('正在重新连接', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as any).__sessionSockets.length)).toBe(2)
  await expect(page.getByText('生成中…', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('chat-message').nth(1)).toContainText(prompt)
  await page.getByText('掉线恢复B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '掉线恢复B', exact: true })).toBeVisible()
  await page.getByText('掉线恢复A', { exact: true }).first().click()
  await expect(page.getByTestId('chat-message').nth(1)).toContainText(prompt)
  expect(await page.evaluate(() => (window as any).__sessionSockets.length)).toBe(2)
  await page.getByRole('button', { name: '退出登录', exact: true }).first().click()
  await expect.poll(() => page.evaluate(() => (window as any).__sessionSockets.every((socket: WebSocket) => socket.readyState === WebSocket.CLOSED))).toBe(true)
})

test('历史超时独立收尾并可重试，不误报传输断线', async ({ page }) => {
  await ensureOwnerSession(page)
  const ids = await seedPair(page, '历史超时')
  await page.evaluate(() => {
    const original = window.setTimeout.bind(window)
    ;(window as any).__restoreTimeout = () => { window.setTimeout = original }
    // 仅缩短故障兜底计时以验证超时分支；不按时间伪造成功响应。
    window.setTimeout = ((handler: TimerHandler, ms?: number, ...args: any[]) => original(handler, ms === 30_000 ? 150 : ms, ...args)) as typeof window.setTimeout
  })
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  const pattern = `**/api/conversations/${ids[0]}/messages?window=recent`
  await page.route(pattern, async (route) => { await held; await route.continue().catch(() => undefined) })
  await page.getByText('历史超时A', { exact: true }).click()
  await expect(page.getByText('会话历史加载超时，请重试。', { exact: true })).toBeVisible()
  await expect(page.getByText('正在加载会话历史…')).toHaveCount(0)
  await expect(page.getByText('实时连接失败', { exact: true })).toHaveCount(0)
  await page.evaluate(() => (window as any).__restoreTimeout())
  release()
  await page.unroute(pattern)
  await page.getByRole('button', { name: '重试连接与加载', exact: true }).click()
  await expect(page.getByText('还没有消息，发送第一条消息开始对话。')).toBeVisible()
  await page.getByLabel('消息输入框').fill('重试完成')
  await expect(page.getByLabel('发送消息')).toBeEnabled()
})

test('持久连接的 Token 被撤销后退出认证上下文', async ({ page }) => {
  await page.addInitScript(() => {
    const Original = WebSocket
    ;(window as any).__authSocket = null
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        if (new URL(String(url), location.href).pathname === '/api/ws') (window as any).__authSocket = this
      }
    }
  })
  await ensureOwnerSession(page)
  const username = OWNER.username + '_session_guest'
  const registered = await page.evaluate(async (username) => (await fetch('/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: 'Roleplex-Test-1234', nickname: '连接 Guest' }),
  })).status, username)
  expect(registered).toBe(201)
  await page.getByRole('button', { name: '退出登录', exact: true }).first().click()
  await page.goto('/#/auth')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByPlaceholder('owner').fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Test-1234')
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
  await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
  await expect.poll(() => page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().connection)).toBe('open')
  const changed = await page.evaluate(async () => (await fetch('/api/auth/password', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
    body: JSON.stringify({ current_password: 'Roleplex-Test-1234', new_password: 'Roleplex-Changed-Test-1234' }),
  })).status)
  expect(changed).toBe(200)
  // 忽略新 Token，模拟另一个客户端改密撤销本连接的旧 Token。
  await page.evaluate(() => (window as any).__authSocket.send(JSON.stringify({ type: 'ping' })))
  await expect.poll(() => page.evaluate(() => localStorage.getItem('roleplex_token') === null)).toBe(true)
  await expect(page).toHaveURL(/#\/auth$/)
  await expect.poll(() => page.evaluate(() => (window as any).__authSocket.readyState)).toBe(3)
  await page.evaluate(() => localStorage.setItem('roleplex_token', 'invalid-test-placeholder'))
  await page.reload()
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByText('正在加载 Roleplex…', { exact: true })).toHaveCount(0)
})

test('退出登录后迟到的 Owner 配置请求不能写回工作台', async ({ page }) => {
  await ensureOwnerSession(page)
  await seedPair(page, '旧身份配置')
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let captured = false
  await page.route(/\/api\/roles(?:\?.*)?$/, async (route) => {
    const response = await route.fetch()
    captured = true
    await held
    await route.fulfill({ response }).catch(() => undefined)
  })
  await page.evaluate(async () => {
    const store = (await import('/src/store/app.ts')).useAppStore
    ;(window as any).__lateWorkspaceLoad = store.getState().loadWorkspace()
  })
  await expect.poll(() => captured).toBe(true)
  await page.getByRole('button', { name: '退出登录', exact: true }).first().click()
  release()
  await page.evaluate(() => (window as any).__lateWorkspaceLoad)
  const visible = await page.evaluate(async () => {
    const state = (await import('/src/store/app.ts')).useAppStore.getState()
    return { anonymous: state.user === null, roles: state.roles.length, configs: state.modelConfigs.length, conversations: state.conversations.length }
  })
  expect(visible).toEqual({ anonymous: true, roles: 0, configs: 0, conversations: 0 })
})

test('旧后端协议能力不足时明确失败，切换不隐藏失败且可手动重试', async ({ page }) => {
  let supported = false
  await page.routeWebSocket('**/api/ws', (ws) => {
    const server = ws.connectToServer()
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      if (!supported && frame.type === 'auth_ok') ws.send(JSON.stringify({ ...frame, capabilities: [] }))
      else ws.send(raw)
    })
  })
  await ensureOwnerSession(page)
  await seedPair(page, '协议兼容')
  await page.getByText('协议兼容A', { exact: true }).click()
  await expect(page.getByText('后端尚不支持当前订阅协议，请更新并重启后端。')).toBeVisible()
  await page.getByText('协议兼容B', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '协议兼容B', exact: true })).toBeVisible()
  await expect(page.getByText('后端尚不支持当前订阅协议，请更新并重启后端。')).toBeVisible()
  supported = true
  await page.getByRole('button', { name: '重试连接与加载', exact: true }).click()
  await page.getByLabel('消息输入框').fill('协议恢复')
  await expect(page.getByLabel('发送消息')).toBeEnabled()
})

test('会话切换和回到空工作台复用登录连接，慢历史不显示断线', async ({ page }) => {
  const sockets: string[] = []
  page.on('websocket', (socket) => { if (new URL(socket.url()).pathname === '/api/ws') sockets.push(socket.url()) })
  await ensureOwnerSession(page)
  await expect.poll(() => sockets.length).toBe(1)
  const ids = await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown) => (await fetch(route, { method: 'POST', headers, body: JSON.stringify(body) })).json()
    const config = await post('/api/model-configs', { name: '连接验收模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: '连接验收角色', system_prompt: '测试连接。', model_config_id: config.id, model_name: 'fake-model' })
    const ids = []
    for (const label of ['A', 'B']) ids.push((await post('/api/conversations', { type: 'single', title: `连接验收${label}`, role_ids: [role.id] })).id)
    return ids as number[]
  })
  // 更新列表而不刷新页面，确保整个用例的物理连接身份保持一致。
  await page.evaluate(async () => (await import('/src/store/app.ts')).useAppStore.getState().loadWorkspace())
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  await page.route(`**/api/conversations/${ids[0]}/messages?window=recent`, async (route) => {
    await held
    await route.continue().catch(() => undefined)
  })
  await page.getByText('连接验收A', { exact: true }).click()
  await expect(page.getByText('正在加载会话历史…')).toBeVisible()
  await expect(page.getByText('连接已断开', { exact: true })).toHaveCount(0)
  await page.getByText('连接验收B', { exact: true }).click()
  await expect(page.getByText('还没有消息，发送第一条消息开始对话。')).toBeVisible()
  release()
  await page.getByText('连接验收A', { exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '连接验收A', exact: true })).toBeVisible()
  await expect(page.getByText('还没有消息，发送第一条消息开始对话。')).toBeVisible()
  await page.getByLabel('消息输入框').fill('连接复用验收')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText(/已收到你的消息：连接复用验收/)).toBeVisible()
  await page.evaluate(async () => (await import('/src/store/app.ts')).useAppStore.setState({ loading: true }))
  await expect(page.getByText('正在加载 Roleplex…', { exact: true })).toBeVisible()
  await page.evaluate(async () => (await import('/src/store/app.ts')).useAppStore.setState({ loading: false }))
  await expect(page.getByRole('heading', { name: '连接验收A', exact: true })).toBeVisible()
  for (const id of [ids[1], ids[0], ids[1], ids[0]]) {
    await page.evaluate((id) => { location.hash = `#/workspace/conversation/${id}` }, id)
    await expect(page.getByRole('heading', { name: id === ids[0] ? '连接验收A' : '连接验收B', exact: true })).toBeVisible()
    await expect(page.getByText('正在加载会话历史…')).toHaveCount(0)
  }
  await page.evaluate(() => { location.hash = '#/workspace' })
  await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
  expect(sockets.length).toBe(1)
})
