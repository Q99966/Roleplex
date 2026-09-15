import { test, expect } from '@playwright/test'
import { ensureOwnerSession } from './owner'

const base = process.env.ROLEPLEX_E2E_API_ORIGIN!

test('清爽侧栏切换与双搜索保留位置，不打断当前会话', async ({ page }, testInfo) => {
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (base) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, value: unknown) => {
      const response = await fetch(base + route, { method: 'POST', headers, body: JSON.stringify(value) })
      if (!response.ok) throw new Error('侧栏测试准备失败')
      return response.json()
    }
    const config = await post('/api/model-configs', { name: '侧栏测试模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const roles = []
    for (let i = 0; i < 14; i++) roles.push(await post('/api/roles', { name: `清新助手 ${i}`, description: i === 0 ? '侧栏专用旅行灵感' : '协助日常创作', tags: i === 0 ? ['侧栏专用灵感'] : ['日常'],
      model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控测试助手。' }))
    let cid = 0
    for (let i = 0; i < 14; i++) {
      const value = await post('/api/conversations', { title: `周末计划 ${i}`, type: 'single', role_ids: [roles[0].id] })
      if (i === 0) cid = value.id
    }
    return cid
  }, base)
  await page.reload()
  const sidebar = page.getByRole('complementary', { name: '工作区侧栏' })
  const conversations = sidebar.getByRole('tab', { name: '会话', exact: true })
  const roles = sidebar.getByRole('tab', { name: '角色', exact: true })
  await expect(conversations).toHaveAttribute('aria-selected', 'true')
  await sidebar.getByLabel('搜索会话', { exact: true }).fill('周末计划 0')
  await sidebar.getByRole('button', { name: '打开会话：周末计划 0', exact: true }).click()
  await expect(page.getByLabel('消息输入框')).toBeVisible()
  await page.getByLabel('消息输入框').fill('尚未发送的草稿')
  await roles.click()
  await expect(page).toHaveURL(new RegExp(`/conversation/${cid}$`))
  await expect(page.getByLabel('消息输入框')).toHaveValue('尚未发送的草稿')
  await expect(sidebar.getByTitle('定制 Agent 角色')).toBeVisible()
  await sidebar.getByLabel('搜索角色', { exact: true }).fill('侧栏专用灵感')
  await expect(sidebar.getByTestId('sidebar-role')).toHaveCount(1)
  await sidebar.getByLabel('搜索角色', { exact: true }).fill('侧栏专用旅行')
  await expect(sidebar.getByTestId('sidebar-role')).toHaveCount(1)
  await sidebar.getByLabel('搜索角色', { exact: true }).fill('没有这样的角色')
  await expect(sidebar.getByText('未找到相关角色')).toBeVisible()
  await sidebar.getByLabel('搜索角色', { exact: true }).fill('清新')
  await conversations.click()
  await expect(sidebar.getByLabel('搜索会话', { exact: true })).toHaveValue('周末计划 0')
  await roles.click()
  await expect(sidebar.getByLabel('搜索角色', { exact: true })).toHaveValue('清新')
  const panel = sidebar.getByRole('tabpanel')
  await panel.evaluate(el => { el.scrollTop = 150 })
  const position = await panel.evaluate(el => el.scrollTop)
  expect(position).toBeGreaterThan(0)
  await conversations.click()
  await roles.click()
  expect(await panel.evaluate(el => el.scrollTop)).toBe(position)
  await panel.evaluate(el => { el.scrollTop = 0 })
  await sidebar.getByLabel('搜索角色', { exact: true }).fill('侧栏专用旅行')
  await sidebar.getByRole('button', { name: '更多角色操作：清新助手 0', exact: true }).click()
  await sidebar.getByRole('button', { name: '编辑角色', exact: true }).click()
  await expect(page.getByLabel('最大输出 tokens')).toBeVisible()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByLabel('消息输入框')).toHaveValue('尚未发送的草稿')
  for (const tab of [conversations, roles]) {
    await tab.click()
    if (tab === conversations) await sidebar.getByLabel('搜索会话').fill('')
    else await sidebar.getByLabel('搜索角色').fill('')
    const shot = testInfo.outputPath(tab === conversations ? 'sky-conversations.png' : 'mint-roles.png')
    await page.screenshot({ path: shot })
    await testInfo.attach(tab === conversations ? '浅蓝会话侧栏' : '薄荷角色侧栏', { path: shot, contentType: 'image/png' })
  }
  await roles.focus()
  await page.keyboard.press('ArrowLeft')
  await expect(conversations).toBeFocused()
  await expect(conversations).toHaveAttribute('aria-selected', 'true')
  await page.setViewportSize({ width: 390, height: 850 })
  const shot = testInfo.outputPath('fresh-sidebar-mobile.png')
  await page.screenshot({ path: shot })
  await testInfo.attach('窄屏侧栏', { path: shot, contentType: 'image/png' })
  await sidebar.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await expect(page.getByLabel('消息输入框')).toBeVisible()
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await expect(conversations).toBeVisible()
})
