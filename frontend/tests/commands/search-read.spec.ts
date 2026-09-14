import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('搜索真实行号后读取大文件范围，保留混合批次结果并隔离 Guest 详情', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'search-read')
  await mkdir(root)
  await writeFile(path.join(root, 'source.txt'), 'padding\n'.repeat(150000) + 'TARGET_FUNCTION\n<script>window.t2Injected=true</script>\nSECOND_TARGET\n')
  await writeFile(path.join(root, 'small.txt'), 'small-placeholder')
  await writeFile(path.join(root, '.env'), 'TARGET_FUNCTION=sensitive-placeholder')
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('搜索测试准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'search', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '搜索助手', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '用搜索结果定位并读取。', builtin_tools: ['workspace_search', 'workspace_read'] })
    const workspace = await request('/api/workspaces', { display_name: '搜索工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '搜索范围验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByTestId('sidebar-role').filter({ hasText: '搜索助手' }).click()
  await expect(page.getByRole('group', { name: '文件操作', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '搜索工作区文件 (workspace_search)', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByText('搜索范围验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[SEARCH_READ_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('搜索与范围读取完成。', { exact: true })).toBeVisible({ timeout: 30000 })
  const reply = page.getByTestId('chat-message').nth(1)
  const cards = reply.getByTestId('tool-call-card')
  await expect(cards).toHaveCount(3)
  await expect(reply.getByRole('button', { name: '探索记录', exact: true })).toContainText('搜索 1 次')
  await cards.nth(0).getByRole('button', { name: '执行详情：workspace_search', exact: true }).click()
  const search = page.getByRole('region', { name: '本次文件搜索' })
  await expect(search).toContainText('source.txt · 2 处匹配')
  await expect(search).toContainText('TARGET_FUNCTION、SECOND_TARGET')
  await expect(search.locator('summary')).toHaveCount(1)
  await expect(search).not.toContainText('sensitive-placeholder')
  await search.locator('summary').click()
  await expect(search.getByRole('region', { name: '搜索匹配：source.txt' }).first()).toContainText('TARGET_FUNCTION')
  await cards.nth(1).getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  const range = page.getByRole('region', { name: '本次按行读取' })
  await expect(range).toContainText('150001～150002')
  await expect(range.getByRole('region', { name: '行范围内容' })).toContainText('<script>window.t2Injected=true</script>')
  expect(await page.evaluate(() => Boolean((window as any).t2Injected))).toBe(false)
  await cards.nth(2).getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  await expect(page.getByRole('region', { name: '本次批量读取' })).toContainText('missing.txt · 失败')
  const batch = page.getByRole('region', { name: '本次批量读取' })
  await expect(batch.locator('summary').nth(0)).toContainText('150001～150002 行')
  await expect(batch.locator('summary').nth(1)).toContainText('150003～150003 行')
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 850 })
    if (width === 390) await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
    await search.scrollIntoViewIfNeeded()
    await expect.poll(() => search.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    const searchShot = testInfo.outputPath(`search-results-${width}.png`)
    await page.screenshot({ path: searchShot })
    await testInfo.attach(`多关键词搜索 ${width}`, { path: searchShot, contentType: 'image/png' })
    await range.scrollIntoViewIfNeeded()
    await expect.poll(() => range.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    const shot = testInfo.outputPath(`search-read-${width}.png`)
    await page.screenshot({ path: shot })
    await testInfo.attach(`搜索与行范围 ${width}`, { path: shot, contentType: 'image/png' })
  }
  // 当前产品刷新后会重新展开侧栏，桌面尺寸下单独验证历史详情恢复。
  await page.setViewportSize({ width: 1280, height: 850 })
  await page.reload()
  await cards.nth(1).getByRole('button', { name: '执行详情：workspace_read', exact: true }).click()
  await expect(range).toContainText('150001～150002')
  await page.getByLabel('消息输入框').fill('[SEARCH_STALE_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('版本冲突已确认，未修改文件。', { exact: true })).toBeVisible()
  await expect(page.getByTestId('chat-message').nth(3)).toContainText('WORKSPACE_FILE_REVISION_CONFLICT')
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
  let detailRequests = 0
  page.on('request', (request) => { if (/\/tools\//.test(request.url())) detailRequests++ })
  await cards.nth(0).getByRole('button', { name: '执行详情：workspace_search', exact: true }).click()
  await expect(cards.nth(0)).toContainText('详细输入和输出仅 Owner 可见。')
  await expect(page.getByRole('region', { name: '本次文件搜索' })).toHaveCount(0)
  expect(detailRequests).toBe(0)
})
