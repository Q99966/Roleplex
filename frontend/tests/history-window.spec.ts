import { expect, test, type Page } from '@playwright/test'
import { ensureOwnerSession } from './owner'

/** 经真实 API 产生连续群聊历史，无 mentions 不触发模型。
 * @param page 已登录浏览器。
 * @param title 本用例会话标题。
 */
async function seedHistory(page: Page, title: string) {
  const id = await page.evaluate(async (title) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('窗口测试准备失败')
      return response.json()
    }
    const config = await post('/api/model-configs', { name: title, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: `助手-${title}`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '测试。' })
    const second = await post('/api/roles', { name: `助手乙-${title}`, model_config_id: config.id, model_name: 'fake-model', system_prompt: '测试。' })
    const conversation = await post('/api/conversations', { title, type: 'group', role_ids: [role.id, second.id] })
    for (let index = 0; index < 72; index++) await post(`/api/conversations/${conversation.id}/messages`, {
      parts: [{ type: 'text', text: `历史编号-${index}\n` + '确定性历史段落。'.repeat(100) }], mentions: [],
    })
    return conversation.id as number
  }, title)
  await page.evaluate(async () => (await import('/src/store/app.ts')).useAppStore.getState().loadWorkspace())
  return id
}

test('最近窗口、前插锚点、局部重试与缓存切回', async ({ page }, testInfo) => {
  await ensureOwnerSession(page)
  const id = await seedHistory(page, '历史窗口验收')
  let reads = 0
  page.on('request', (request) => { if (request.method() === 'GET' && request.url().includes(`/conversations/${id}/messages?`)) reads++ })
  await page.getByText('历史窗口验收', { exact: true }).click()
  await expect(page.getByRole('button', { name: '加载更早消息', exact: true })).toBeAttached()
  const initial = await page.getByTestId('chat-message').count()
  expect(initial).toBeGreaterThan(0)
  expect(initial).toBeLessThan(50)
  const feed = page.getByRole('log', { name: '会话消息' })
  await feed.evaluate((element) => { element.scrollTop = 100 })
  await expect.poll(() => page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position.bottom)).toBe(false)
  const before = await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position)
  let fail = true
  await page.route(`**/conversations/${id}/messages?*before=*`, (route) => fail
    ? route.fulfill({ status: 503, json: { error: { code: 'TEST_UNAVAILABLE', message: '历史暂不可用' } } }) : route.continue())
  await page.getByRole('button', { name: '加载更早消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试加载更早消息', exact: true })).toBeVisible()
  await expect(page.getByTestId('chat-message')).toHaveCount(initial)
  fail = false
  await page.getByRole('button', { name: '重试加载更早消息', exact: true }).click()
  await expect.poll(() => page.getByTestId('chat-message').count()).toBeGreaterThan(initial)
  expect(before.bottom).toBe(false)
  const restored = await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position)
  const readCount = reads
  await page.evaluate(() => { location.hash = '#/workspace' })
  await expect(page.getByText('欢迎来到 Roleplex')).toBeVisible()
  await page.getByText('历史窗口验收', { exact: true }).click()
  await expect.poll(() => page.getByTestId('chat-message').count()).toBeGreaterThan(initial)
  expect(reads).toBe(readCount)
  expect(await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position)).toEqual(restored)
  const delta = await page.getByRole('log', { name: '会话消息' }).evaluate((element, position) => {
    const anchor = [...element.querySelectorAll<HTMLElement>('[data-reading-anchor]')].find((node) => node.dataset.readingAnchor === position.anchor)
    return anchor ? Math.abs(anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - position.offset) : Infinity
  }, restored)
  expect(delta).toBeLessThan(2)
  const screenshot = testInfo.outputPath('history-window.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('分页阅读窗口', { path: screenshot, contentType: 'image/png' })
})

test('按字节和数量淘汰非活动缓存，超大窗口不缓存', async ({ page }) => {
  await ensureOwnerSession(page)
  const result = await page.evaluate(async () => {
    const { HistoryCache } = await import('/src/store/history-cache.ts')
    const cache = new HistoryCache(1024, 2)
    const entry = { messages: [], nextCursor: null, eventSeq: 0, streamEpoch: 'test', activeGenerationIds: [],
      position: { anchor: null, offset: 0, bottom: true }, oversized: false }
    cache.put(1, entry); cache.put(2, entry); cache.put(3, entry)
    const countEviction = cache.take(1) === undefined
    cache.put(4, { ...entry, streamEpoch: 'x'.repeat(1100) })
    const oversizedSkipped = cache.take(4) === undefined
    const byteCache = new HistoryCache(500, 8)
    byteCache.put(1, { ...entry, streamEpoch: 'x'.repeat(100) })
    byteCache.put(2, { ...entry, streamEpoch: 'x'.repeat(100) })
    const byteEviction = byteCache.take(1) === undefined && byteCache.take(2) !== undefined
    cache.clear()
    return { countEviction, oversizedSkipped, byteEviction, cleared: cache.take(2) === undefined && cache.take(3) === undefined }
  })
  expect(result).toEqual({ countEviction: true, oversizedSkipped: true, byteEviction: true, cleared: true })
})

test('阅读旧内容不被新消息拉到底部，前插和布局变化保持锚点', async ({ page }) => {
  await ensureOwnerSession(page)
  const id = await seedHistory(page, '阅读位置验收')
  await page.getByText('阅读位置验收', { exact: true }).click()
  const feed = page.getByRole('log', { name: '会话消息' })
  await expect(page.getByTestId('chat-message').first()).toBeAttached()
  await feed.evaluate((node) => { node.scrollTop = 600 })
  await expect.poll(() => page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position.bottom)).toBe(false)
  const position = await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position)
  await page.evaluate(async (id) => {
    await fetch(`/api/conversations/${id}/messages`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      body: JSON.stringify({ parts: [{ type: 'text', text: '阅读期间的新内容' }], mentions: [] }) })
  }, id)
  await expect(page.getByRole('button', { name: '有新内容，回到底部' })).toBeVisible()
  // 受控改变锚点之前的布局，模拟 Markdown 或工具详情延迟展开。
  await page.getByTestId('chat-message').first().evaluate((node) => { node.style.paddingTop = '240px' })
  await expect.poll(() => feed.evaluate((element, position) => {
    const anchor = [...element.querySelectorAll<HTMLElement>('[data-reading-anchor]')].find((node) => node.dataset.readingAnchor === position.anchor)
    return anchor ? Math.abs(anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - position.offset) : Infinity
  }, position)).toBeLessThan(2)
  await page.getByRole('button', { name: '有新内容，回到底部' }).click()
  await expect.poll(() => feed.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(2)
  // 回到顶部实际滚动触发前插，不依赖点击后改变的滚动位置。
  const countBeforePrepend = await page.getByTestId('chat-message').count()
  await feed.evaluate((node) => { node.scrollTop = 0 })
  await expect.poll(() => page.getByTestId('chat-message').count()).toBeGreaterThan(countBeforePrepend)
})

test('单条超过 64 KiB 时保持完整气泡，仍能向前翻页', async ({ page }) => {
  await ensureOwnerSession(page)
  const id = await seedHistory(page, '超长单条验收')
  await page.evaluate(async (id) => {
    const response = await fetch(`/api/conversations/${id}/messages`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      body: JSON.stringify({ parts: [{ type: 'text', text: '超长正文'.repeat(7000) }], mentions: [] }) })
    if (!response.ok) throw new Error('超长测试准备失败')
  }, id)
  await page.getByText('超长单条验收', { exact: true }).click()
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  expect(await page.getByTestId('chat-message').evaluate((node) => node.textContent?.includes('超长正文'.repeat(7000)))).toBe(true)
  await expect(page.getByText('包含超出单页预算的完整消息，加载和显示可能较慢。')).toBeAttached()
  await page.getByRole('button', { name: '加载更早消息', exact: true }).click()
  await expect.poll(() => page.getByTestId('chat-message').count()).toBeGreaterThan(1)
})

test('真实窗口快照作废已缓存旧页和迟到翻页响应', async ({ page }) => {
  let forceSnapshot = false
  let injectGap: (() => void) | undefined
  await page.routeWebSocket('**/api/ws', (ws) => {
    const server = ws.connectToServer()
    ws.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      if (forceSnapshot && frame.type === 'subscribe') server.send(JSON.stringify({ ...frame, stream_epoch: 'expired-test-epoch' }))
      else server.send(raw)
    })
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      ws.send(raw)
      if (frame.type === 'sync_complete') injectGap = () => ws.send(JSON.stringify({ ...frame, type: 'message_delta',
        event_seq: frame.through_event_seq + 2, payload: { message_id: 1, text: '应被缺口恢复丢弃' } }))
    })
  })
  await ensureOwnerSession(page)
  const id = await seedHistory(page, '窗口快照验收')
  await page.getByText('窗口快照验收', { exact: true }).click()
  await expect.poll(() => Boolean(injectGap)).toBe(true)
  const initial = await page.getByTestId('chat-message').count()
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let captured = false
  await page.route(`**/conversations/${id}/messages?*before=*`, async (route) => {
    const response = await route.fetch()
    captured = true
    await held
    await route.fulfill({ response }).catch(() => undefined)
  })
  await page.getByRole('button', { name: '加载更早消息', exact: true }).click()
  await expect.poll(() => captured).toBe(true)
  forceSnapshot = true
  injectGap!()
  await expect(page.getByRole('button', { name: '加载更早消息', exact: true })).toBeEnabled()
  release()
  await expect(page.getByTestId('chat-message')).toHaveCount(initial)
  expect(await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().loadingOlder)).toBe(false)
})

test('未加载区域的增量不拼半条消息，翻页重读消除旧 revision 竞态', async ({ page }) => {
  let emit: ((id: number) => void) | undefined
  await page.routeWebSocket('**/api/ws', (ws) => {
    const server = ws.connectToServer()
    server.onMessage((raw) => {
      const frame = JSON.parse(String(raw))
      ws.send(raw)
      if (frame.type === 'sync_complete') emit = (id) => ws.send(JSON.stringify({ ...frame, type: 'message_delta',
        event_seq: frame.through_event_seq + 1, revision: 1, payload: { message_id: id, text: '新版增量' } }))
    })
  })
  await ensureOwnerSession(page)
  const id = await seedHistory(page, '翻页版本竞态')
  await page.getByText('翻页版本竞态', { exact: true }).click()
  await expect.poll(() => Boolean(emit)).toBe(true)
  const initial = await page.getByTestId('chat-message').count()
  let attempts = 0
  await page.route(`**/conversations/${id}/messages?*before=*`, async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    attempts++
    if (attempts === 1) {
      emit!(body.items.at(-1).id)
      await expect(page.getByTestId('chat-message')).toHaveCount(initial)
      // 等事件消费者记录 revision，再释放已经读出的旧页。
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)))
    } else {
      body.items.at(-1).revision = 1
      body.items.at(-1).parts_json = [{ type: 'text', text: '完整新版历史' }]
    }
    await route.fulfill({ json: body })
  })
  await page.getByRole('button', { name: '加载更早消息', exact: true }).click()
  await expect(page.getByText('完整新版历史', { exact: true })).toBeAttached()
  expect(attempts).toBe(2)
  await expect(page.getByText('新版增量', { exact: true })).toHaveCount(0)
})
