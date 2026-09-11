import { expect, test, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from './owner'

/** 通过实际 API 创建两个隔离群聊，默认无 mentions 不生成，方便精确计数。
 * @param page 当前已登录页面。
 * @param title 本用例名称。
 */
async function prepare(page: Page, title: string) {
  await ensureOwnerSession(page)
  const data = await page.evaluate(async (title) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`输入测试准备失败：${response.status}`)
      return response.json()
    }
    const model = await post('/api/model-configs', { name: title, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: `${title}助手`, model_config_id: model.id, model_name: 'fake-model', system_prompt: '确定性输入测试。' })
    const other = await post('/api/roles', { name: `${title}乙`, model_config_id: model.id, model_name: 'fake-model', system_prompt: '确定性输入测试。' })
    const ids: number[] = []
    for (const suffix of ['A', 'B']) ids.push((await post('/api/conversations', { title: title + suffix, type: 'group', role_ids: [role.id, other.id] })).id)
    await (await import('/src/store/app.ts')).useAppStore.getState().loadWorkspace()
    return { ids, role: { id: role.id, name: role.name } }
  }, title)
  await page.getByText(title + 'A', { exact: true }).click()
  await expect.poll(() => page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().subscription)).toBe('ready')
  return data
}

test('多行粘贴和 Shift+Enter 不提交，Enter 仅发送一次并保留原文', async ({ page }) => {
  const { ids } = await prepare(page, '多行原文')
  const input = page.getByRole('textbox', { name: '消息输入框' })
  let posts = 0
  page.on('request', (request) => { if (request.method() === 'POST' && request.url().endsWith(`/conversations/${ids[0]}/messages`)) posts++ })
  await input.fill('    const 示例 = 1;')
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('    const 示例 = 1;\n')
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  const text = '\n    const 示例 = 1;\n\n    // 中文🙂\n  '
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.evaluate((text) => navigator.clipboard.writeText(text), text)
  await input.press('Control+A')
  await input.press('Control+V')
  await expect(input).toHaveValue(text)
  expect(posts).toBe(0)
  const request = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/conversations/${ids[0]}/messages`))
  await input.press('Enter')
  expect((await request).postDataJSON().parts[0].text).toBe(text)
  await expect(input).toHaveValue('')
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  expect(posts).toBe(1)
  await page.reload()
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  const stored = await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().messages[0].parts_json[0].text)
  expect(stored).toBe(text)
})

test('发送失败保留正文与 mentions，修改中的草稿不会被成功响应清除', async ({ page }) => {
  const { ids, role } = await prepare(page, '失败草稿')
  const input = page.getByLabel('消息输入框')
  await input.fill('@')
  await page.getByRole('option', { name: `@${role.name}`, exact: true }).click()
  const text = await input.inputValue() + '\n    待发送代码'
  await input.fill(text)
  await page.route(`**/conversations/${ids[0]}/messages`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({ status: 503, json: { error: { code: 'TEST_SEND_UNAVAILABLE', message: '测试发送失败' } } })
  })
  await input.press('Enter')
  await expect(page.getByText('TEST_SEND_UNAVAILABLE', { exact: true })).toBeVisible()
  await expect(input).toHaveValue(text)
  await expect(page.getByRole('button', { name: `移除 @${role.name}` })).toBeVisible()
  await page.unroute(`**/conversations/${ids[0]}/messages`)
  await page.getByRole('button', { name: `移除 @${role.name}` }).click()
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route(`**/conversations/${ids[0]}/messages`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await gate
    await route.continue()
  })
  await input.press('Enter')
  await expect(page.getByLabel('发送消息')).toBeDisabled()
  await input.fill('新草稿\n    不能被清空')
  release()
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  await expect(page.getByLabel('发送消息')).toBeEnabled()
  await expect(input).toHaveValue('新草稿\n    不能被清空')
})

test('中文组合确认不发送，多行中间补全保留后文且 Enter 先选角色', async ({ page }) => {
  const { role } = await prepare(page, '输入法补全')
  const input = page.getByLabel('消息输入框')
  await input.fill('中文候选')
  await input.dispatchEvent('compositionstart')
  await input.press('Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  await input.dispatchEvent('compositionend', { data: '中文候选' })
  // Safari 的候选确认可能先结束组合，再送出 keyCode=229 的 Enter。
  await input.dispatchEvent('keydown', { key: 'Enter', keyCode: 229 })
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  await input.fill('首行\n@\n尾行保留')
  await input.press('Control+Home')
  for (let index = 0; index < 4; index++) await input.press('ArrowRight')
  await expect(page.getByRole('listbox', { name: '@ 角色补全' })).toBeVisible()
  await input.press('ArrowDown')
  await input.press('Enter')
  await expect(input).toHaveValue(`首行\n@${role.name} \n尾行保留`)
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  await expect(input).toBeFocused()
  await input.press('Shift+Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  await input.press('Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(2, { timeout: 20000 })
})

test('仅明确提交独立 /ps 才打开面板，空白与多行代码不误触发', async ({ page }) => {
  await prepare(page, '本地命令')
  const input = page.getByLabel('消息输入框')
  await input.fill(' \n\t ')
  await input.press('Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(0)
  await input.fill('/ps')
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('/ps\n')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await input.fill('```\n/ps\n```')
  await input.press('Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  await input.fill('/ps')
  await input.press('Enter')
  await expect(input).toHaveValue('')
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  await expect(page.getByRole('dialog', { name: '会话进程与详情' })).toBeVisible()
})

test('旧提交成功或失败的迟到响应均不污染新会话草稿', async ({ page }) => {
  const { ids } = await prepare(page, '跨会话草稿')
  const input = page.getByLabel('消息输入框')
  for (const failed of [false, true]) {
    await page.evaluate((id) => { location.hash = `#/workspace/conversation/${id}` }, ids[0])
    await expect(page.getByRole('heading', { name: '跨会话草稿A', exact: true })).toBeVisible()
    await input.fill('旧会话请求')
    await expect(page.getByLabel('发送消息')).toBeEnabled()
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    let arrived = false
    await page.route(`**/conversations/${ids[0]}/messages`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      arrived = true
      await gate
      if (failed) await route.fulfill({ status: 503, json: { error: { code: 'OLD_SEND_FAILED' } } })
      else await route.continue()
    })
    await input.press('Enter')
    await expect.poll(() => arrived).toBe(true)
    await page.getByText('跨会话草稿B', { exact: true }).click()
    await expect(page.getByRole('heading', { name: '跨会话草稿B', exact: true })).toBeVisible()
    await input.fill('当前会话\n新草稿')
    const response = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/conversations/${ids[0]}/messages`))
    release()
    await response
    await expect(input).toHaveValue('当前会话\n新草稿')
    await expect(page.getByText('OLD_SEND_FAILED', { exact: true })).toHaveCount(0)
    await expect(page.getByTestId('chat-message')).toHaveCount(0)
    await page.unroute(`**/conversations/${ids[0]}/messages`)
  }
})

test('长输入有高度上限并保留历史锚点，窄屏发送按钮可见', async ({ page }, testInfo) => {
  const { ids } = await prepare(page, '输入区布局')
  await page.evaluate(async (id) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    for (let index = 0; index < 18; index++) {
      const response = await fetch(`/api/conversations/${id}/messages`, { method: 'POST', headers,
        body: JSON.stringify({ parts: [{ type: 'text', text: `占位历史 ${index}\n` + '历史正文\n'.repeat(3) }] }) })
      if (!response.ok) throw new Error('历史准备失败')
    }
  }, ids[0])
  await expect(page.getByTestId('chat-message')).toHaveCount(18)
  const feed = page.getByRole('log', { name: '会话消息' })
  await feed.evaluate((element) => { element.scrollTop = 250 })
  await expect.poll(() => page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position.bottom)).toBe(false)
  const before = await page.evaluate(async () => (await import('/src/store/chat.ts')).useChatStore.getState().position)
  const input = page.getByLabel('消息输入框')
  await input.fill('    const longLine = "占位代码";\n'.repeat(40))
  await expect.poll(() => input.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true)
  expect((await input.boundingBox())!.height).toBeLessThanOrEqual(240)
  const delta = await feed.evaluate((element, position) => {
    const anchor = [...element.querySelectorAll<HTMLElement>('[data-reading-anchor]')].find((node) => node.dataset.readingAnchor === position.anchor)!
    return Math.abs(anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - position.offset)
  }, before)
  expect(delta).toBeLessThan(2)
  const desktop = testInfo.outputPath('multiline-desktop.png')
  await page.screenshot({ path: desktop })
  await testInfo.attach('桌面多行输入', { path: desktop, contentType: 'image/png' })
  await page.getByTitle('收起侧边栏', { exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByLabel('发送消息')).toBeInViewport()
  await expect.poll(async () => (await input.boundingBox())!.width).toBeGreaterThan(200)
  const screenshot = testInfo.outputPath('multiline-mobile.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('窄屏多行输入', { path: screenshot, contentType: 'image/png' })
  await input.fill('短草稿')
  await expect.poll(() => input.evaluate((element) => element.clientHeight)).toBeLessThan(60)
})

test('Guest 多行消息可发送但 /ps 不发管理请求，角色失效后禁止提交', async ({ page }) => {
  const { ids } = await prepare(page, '访客多行')
  const database = path.resolve(process.cwd(), process.env.ROLEPLEX_E2E_DATABASE_PATH!)
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(ids[0])], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async ({ stamp, id }) => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    const result = await response.json()
    localStorage.setItem('roleplex_token', result.access_token)
    location.hash = `#/workspace/conversation/${id}`
  }, { stamp: process.env.ROLEPLEX_E2E_STAMP, id: ids[0] })
  await page.reload()
  const input = page.getByLabel('消息输入框')
  await expect(input).toBeEnabled()
  let management = 0
  page.on('request', (request) => { if (request.url().includes('/runtime')) management++ })
  await input.fill('/ps')
  await input.press('Enter')
  await expect(page.getByText('仅 Owner 可查看和管理会话进程。')).toBeVisible()
  expect(management).toBe(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await input.fill('访客\n    多行正文')
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
  // 模拟后端返回的空成员列表；Guest 不加载 Owner 角色配置，不能借测试播种私有角色。
  await page.evaluate(async () => {
    const store = (await import('/src/store/app.ts')).useAppStore
    store.setState({ conversations: store.getState().conversations.map((conversation) => ({ ...conversation, role_ids: [] })) })
  })
  await expect(input).toBeDisabled()
  await expect(page.getByLabel('发送消息')).toBeDisabled()
})

test('触屏软键盘 Enter 换行，明确按钮发送', async ({ browser }, testInfo) => {
  const context = await browser.newContext({ baseURL: String(testInfo.project.use.baseURL), hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  try {
    await prepare(page, '软键盘多行')
    await page.getByTitle('收起侧边栏', { exact: true }).click()
    const input = page.getByLabel('消息输入框')
    await input.fill('触屏正文')
    const prevented = await input.evaluate((element) => {
      const event = new KeyboardEvent('keydown', { key: 'Enter', code: '', bubbles: true, cancelable: true })
      element.dispatchEvent(event)
      return event.defaultPrevented
    })
    expect(prevented).toBe(false)
    // 合成键盘事件不会执行浏览器默认动作，用实际文本插入模拟软键盘随后输入的换行。
    await page.keyboard.insertText('\n下一行')
    await expect(input).toHaveValue('触屏正文\n下一行')
    await expect(page.getByTestId('chat-message')).toHaveCount(0)
    await page.getByLabel('发送消息').click()
    await expect(page.getByTestId('chat-message')).toHaveCount(1)
  } finally { await context.close() }
})

test('历史请求未完成时保留多行草稿，独立 /ps 不依赖发送就绪', async ({ page }) => {
  const { ids } = await prepare(page, '加载草稿')
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  let waiting = false
  await page.route(`**/conversations/${ids[1]}/messages?*`, async (route) => {
    waiting = true
    await gate
    await route.continue()
  })
  await page.getByText('加载草稿B', { exact: true }).click()
  await expect.poll(() => waiting).toBe(true)
  const input = page.getByLabel('消息输入框')
  await input.fill('加载时\n    不丢草稿')
  await input.press('Enter')
  await expect(input).toHaveValue('加载时\n    不丢草稿')
  await expect(page.getByLabel('发送消息')).toBeDisabled()
  await input.fill('/ps')
  await input.press('Enter')
  await expect(page.getByRole('dialog', { name: '会话进程与详情' })).toBeVisible()
  await page.getByRole('button', { name: '关闭进程面板' }).click()
  await input.fill('加载后\n    可以发送')
  release()
  await expect(page.getByLabel('发送消息')).toBeEnabled()
  await input.press('Enter')
  await expect(page.getByTestId('chat-message')).toHaveCount(1)
})

test('空输入和单行文字与发送按钮居中对齐，不显示快捷键说明', async ({ page }, testInfo) => {
  await prepare(page, '输入留白')
  const input = page.getByLabel('消息输入框')
  for (const value of ['', '这是一行消息']) {
    await input.fill(value)
    const delta = await input.evaluate((element) => {
      const box = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      const textCenter = box.top + parseFloat(style.borderTopWidth) + parseFloat(style.paddingTop) + parseFloat(style.lineHeight) / 2
      const send = element.closest('form')!.querySelector('[aria-label="发送消息"]')!.getBoundingClientRect()
      return Math.abs(textCenter - (send.top + send.height / 2))
    })
    expect(delta).toBeLessThan(1)
  }
  await expect(page.getByText('Enter 发送 · Shift+Enter 换行 · 手机请点发送', { exact: true })).toHaveCount(0)
  const screenshot = testInfo.outputPath('composer-alignment.png')
  await input.locator('xpath=ancestor::form').screenshot({ path: screenshot })
  await testInfo.attach('单行输入对齐', { path: screenshot, contentType: 'image/png' })
})
