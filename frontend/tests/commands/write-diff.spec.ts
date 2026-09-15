import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('真实写入按工具位置展示可折叠 diff，刷新恢复且 Guest 不获取私有正文', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'write-diff')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('差异测试准备失败')
      return response.json()
    }
    const model = await post('/api/model-configs', { name: 'diff', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: '差异助手', model_config_id: model.id, model_name: 'fake-model', system_prompt: '确定性写入。', builtin_tools: ['workspace_write'] })
    const workspace = await post('/api/workspaces', { display_name: '差异工作区', root_path: root, acknowledge_existing_content: true })
    await post(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await post('/api/conversations', { title: '写入差异验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('写入差异验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[WRITE_DIFF_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('修改完成。', { exact: true })).toBeVisible()
  const reply = page.getByTestId('chat-message').nth(1)
  await expect(reply.getByRole('region', { name: '系统执行记录' })).toHaveCount(0)
  await expect(reply).not.toContainText('已确认文件提交')
  const sequence = reply.locator('[data-testid="message-text-part"], [data-testid="tool-call-card"]')
  await expect(sequence).toHaveCount(5)
  const card = reply.getByTestId('tool-call-card').nth(1)
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('button', { name: '执行详情：workspace_write', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(card.getByText('demo.ts · 修改', { exact: false })).toBeVisible()
  const diff = card.getByRole('region', { name: '文件差异：demo.ts', exact: true })
  await expect(diff).toContainText("- export const title = '第一版';")
  await expect(diff).toContainText("+ export const title = '第二版🙂';")
  await expect(card.getByText('原始输入与输出', { exact: true })).toHaveCount(0)
  await expect(card.locator('details')).toHaveCount(0)
  await expect(card.getByRole('region', { name: '工具输入' })).toHaveCount(0)
  await expect(diff.locator('.diff-code-insert').first()).toHaveCSS('background-color', 'rgb(18, 51, 34)')
  await expect(diff.locator('.diff-code-delete').first()).toHaveCSS('background-color', 'rgb(64, 27, 27)')
  await expect(diff.locator('.diff-code-insert').first()).toHaveCSS('color', 'rgb(228, 228, 231)')
  const screenshot = testInfo.outputPath('write-diff.png')
  await page.screenshot({ path: screenshot })
  await testInfo.attach('工具原位置的文件差异', { path: screenshot, contentType: 'image/png' })
  await page.reload()
  await page.getByTestId('chat-message').nth(1).getByTestId('tool-call-card').nth(1).scrollIntoViewIfNeeded()
  await expect(card.getByRole('region', { name: '文件差异：demo.ts' })).toContainText('第二版🙂')
  // 只注入展示边界；真实超限和计算失败由后端测试验证，不冒充浏览器重新计算。
  let displayMode = 'partial'
  await page.route('**/tools/**', async (route) => {
    const response = await route.fetch()
    const data = await response.json()
    data.write.availability = displayMode
    if (displayMode === 'partial') {
      data.write.files[0].hunks[0].lines[0].text = '<script>window.diffInjected=true</script>\u202e' + '长行样本'.repeat(120)
    } else {
      data.write.reason = 'input_budget'
      data.write.files[0].hunks = []
      data.write.files[0].added = data.write.files[0].removed = null
    }
    await route.fulfill({ response, json: data })
  })
  const updateButton = page.getByRole('button', { name: '执行详情：workspace_write', exact: true }).nth(1)
  await updateButton.click()
  await updateButton.click()
  await expect(page.getByText('差异仅部分展示；增删统计来自完整计算，不等于当前显示行数。')).toBeVisible()
  await expect(card.getByRole('region', { name: '文件差异：demo.ts' })).toContainText('<script>window.diffInjected=true</script>\\u202e')
  const region = card.getByRole('region', { name: '文件差异：demo.ts' })
  await expect.poll(() => region.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  await expect.poll(() => region.evaluate((element) => {
    const cell = element.querySelector('.diff-code')!
    const span = document.createRange()
    span.selectNodeContents(cell)
    return span.getBoundingClientRect().right <= cell.getBoundingClientRect().right + 1
  })).toBe(true)
  const wrappedScreenshot = testInfo.outputPath('write-diff-wrapped.png')
  await card.screenshot({ path: wrappedScreenshot })
  await testInfo.attach('长行保持在差异底色内', { path: wrappedScreenshot, contentType: 'image/png' })
  expect(await page.evaluate(() => Boolean((window as unknown as { diffInjected?: boolean }).diffInjected))).toBe(false)
  displayMode = 'unavailable'
  await updateButton.click()
  await updateButton.click()
  await expect(page.getByText('修改成功，差异超出 256 KiB 计算输入预算。')).toBeVisible()
  await expect(page.getByText('文件内容没有变化。')).toHaveCount(0)
  await page.unroute('**/tools/**')
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
  await page.getByRole('button', { name: '执行详情：workspace_write', exact: true }).nth(1).click()
  await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
  await expect(page.getByRole('region', { name: '文件差异：demo.ts' })).toHaveCount(0)
  expect(detailRequests).toBe(0)
})
