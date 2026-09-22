import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  return page.evaluate(async name => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    async function post(route: string, body: unknown) {
      const response = await fetch('/api' + route, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`prompt fixture ${response.status}`)
      return response.json()
    }
    const cfg = await post('/model-configs', { name, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/roles', { name: name + '角色', model_config_id: cfg.id, model_name: 'fake-model', system_prompt: 'PROMPT_TEST_ROLE_A',
      skills: [{ name: '旧技能', instructions: '保留原配置' }], mcp_servers: [{ name: '旧 MCP', command: 'placeholder-command', env: { EXAMPLE: 'placeholder' } }] })
    const peer = await post('/roles', { name: name + '同伴', model_config_id: cfg.id, model_name: 'fake-model', system_prompt: '受控同伴' })
    const group = await post('/conversations', { type: 'group', title: name + '群聊', role_ids: [role.id, peer.id] })
    const single = await post('/conversations', { type: 'single', title: name + '单聊', role_ids: [role.id] })
    return { cid: group.id as number, single: single.id as number, role }
  }, name)
}

async function module(page: Page, name = '上下文') {
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name, exact: true }).locator('span').last().click()
}

const context = (page: Page) => page.getByRole('region', { name: '会话提示词配置', exact: true })
const world = (page: Page) => page.getByRole('region', { name: '世界提示词配置', exact: true })

test('平台、世界与单聊群聊提示词保存并进入真实模型输入', async ({ page }, info) => {
  page.setDefaultTimeout(12_000); test.setTimeout(90_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '提示词生效验收', { role } = await fixture(page, name)
  await page.reload()
  await page.getByRole('complementary', { name: '工作区侧栏' }).getByRole('button', { name: '打开设置', exact: true }).click()
  await page.getByRole('tab', { name: '提示词与规则', exact: true }).click()
  await world(page).getByLabel('自定义平台规则', { exact: true }).check()
  await world(page).getByLabel('平台协作规则', { exact: true }).fill('PROMPT_TEST_PLATFORM_A')
  await world(page).getByLabel('世界系统提示词', { exact: true }).fill('PROMPT_TEST_WORLD_A')
  // 后端已经提交但响应丢失时，刷新核对状态，不重复提交或伪装成回滚。
  let written = 0
  await page.route('**/api/prompt-settings', async route => {
    if (route.request().method() !== 'PUT') { await route.continue(); return }
    written++
    const response = await route.fetch()
    expect(response.ok()).toBe(true)
    await route.abort('failed')
  })
  await world(page).getByRole('button', { name: '保存世界提示词', exact: true }).click()
  await expect(world(page).getByRole('status')).toContainText('已保存')
  await expect(world(page).getByRole('alert')).toHaveCount(0)
  expect(written).toBe(1)
  await page.unroute('**/api/prompt-settings')
  const shot = info.outputPath('world-prompt-settings.png')
  await page.screenshot({ path: shot }); await info.attach('平台与当前世界提示词', { path: shot, contentType: 'image/png' })
  await page.getByRole('button', { name: '关闭系统与环境设置', exact: true }).click()
  await page.getByRole('button', { name: `打开会话：${name}群聊`, exact: true }).click()
  await module(page)
  await context(page).getByLabel('会话提示词', { exact: true }).fill('PROMPT_TEST_CONVERSATION_A')
  await context(page).getByRole('button', { name: '保存会话提示词', exact: true }).click()
  await expect(context(page).getByRole('status').first()).toContainText('已保存')
  await page.getByLabel('预览角色', { exact: true }).selectOption(String(role.id))
  await page.getByRole('button', { name: '刷新生效来源', exact: true }).click()
  const sources = page.getByRole('region', { name: '提示词生效来源' })
  await expect(sources).toContainText('平台协作规则')
  const input = page.getByRole('textbox', { name: '消息输入框' })
  await input.fill('@'); await page.getByRole('option', { name: `@${role.name}`, exact: true }).click()
  await input.fill(`@${role.name} [PROMPT_LAYERS_PROBE]`)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('提示词验证：PROMPT_TEST_PLATFORM_A,PROMPT_TEST_WORLD_A,PROMPT_TEST_ROLE_A,PROMPT_TEST_CONVERSATION_A', { exact: true })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: '刷新生效来源', exact: true }).click()
  await expect(sources).toContainText('最近实际采用')
  await page.getByRole('button', { name: `打开会话：${name}单聊`, exact: true }).click()
  await module(page)
  await expect(context(page).getByLabel('会话提示词', { exact: true })).toHaveValue('')
  await input.fill('[PROMPT_LAYERS_PROBE]')
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText('提示词验证：PROMPT_TEST_PLATFORM_A,PROMPT_TEST_WORLD_A,PROMPT_TEST_ROLE_A', { exact: true })).toBeVisible({ timeout: 20_000 })
  await page.reload(); await module(page)
  await expect(context(page).getByLabel('会话提示词', { exact: true })).toHaveValue('')
})

test('冲突保留输入，切换模块和窄屏不丢草稿；Guest 不读取私有提示词', async ({ page }, info) => {
  page.setDefaultTimeout(12_000); await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '提示词冲突验收', { cid } = await fixture(page, name)
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}群聊`, exact: true }).click()
  await module(page)
  await context(page).getByLabel('会话提示词', { exact: true }).fill('当前未提交的规则')
  await module(page, '会话成员'); await module(page)
  await expect(context(page).getByLabel('会话提示词', { exact: true })).toHaveValue('当前未提交的规则')
  await page.evaluate(async cid => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const url = `/api/conversations/${cid}/prompt-settings`
    const revision = (await (await fetch(url, { headers })).json()).revision
    const response = await fetch(url, { method: 'PUT', headers, body: JSON.stringify({ expected_revision: revision, prompt: '远端会话规则' }) })
    if (!response.ok) throw new Error('remote prompt edit failed')
  }, cid)
  await context(page).getByRole('button', { name: '保存会话提示词', exact: true }).click()
  await expect(context(page).getByRole('alert')).toContainText('已更新')
  await expect(context(page).getByLabel('会话提示词', { exact: true })).toHaveValue('当前未提交的规则')
  await page.setViewportSize({ width: 390, height: 850 })
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.getByRole('button', { name: '打开会话详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '会话详情', exact: true })
  await expect(drawer.getByLabel('会话提示词', { exact: true })).toHaveValue('当前未提交的规则')
  await drawer.getByRole('button', { name: '采用服务端配置', exact: true }).click()
  await expect(drawer.getByLabel('会话提示词', { exact: true })).toHaveValue('远端会话规则')
  const shot = info.outputPath('conversation-prompts-mobile.png')
  await page.screenshot({ path: shot }); await info.attach('窄屏会话提示词与来源', { path: shot, contentType: 'image/png' })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '打开会话详情', exact: true })).toBeFocused()
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async stamp => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest login failed')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
  }, process.env.ROLEPLEX_COMMAND_E2E_STAMP)
  await page.reload(); await page.getByRole('button', { name: '打开会话详情', exact: true }).click()
  await module(page)
  await expect(page.getByText('提示词配置由 Owner 管理。', { exact: true })).toBeVisible()
  await expect(page.getByLabel('会话提示词', { exact: true })).toHaveCount(0)
  await expect(page.getByText('远端会话规则', { exact: true })).toHaveCount(0)
})

test('角色编辑保留已有 Skills/MCP，世界规则清空与恢复默认可区分', async ({ page }) => {
  page.setDefaultTimeout(12_000); await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '角色配置保留验收', { role } = await fixture(page, name)
  await page.reload(); await page.getByRole('tab', { name: '角色', exact: true }).click()
  await page.getByTestId('sidebar-role').filter({ hasText: role.name }).getByRole('button').first().click()
  await page.getByLabel('角色系统提示词', { exact: true }).fill('只修改角色提示词')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByLabel('角色系统提示词', { exact: true })).toHaveCount(0)
  const saved = await page.evaluate(async id => (await fetch(`/api/roles/${id}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).json(), role.id)
  expect(saved.skills).toEqual(role.skills); expect(saved.mcp_servers).toEqual(role.mcp_servers)
  expect(saved.system_prompt).toBe('只修改角色提示词')
  await page.getByTestId('sidebar-role').filter({ hasText: role.name }).getByRole('button').first().click()
  await page.getByLabel('角色系统提示词', { exact: true }).fill('角色尚未提交的输入')
  await page.evaluate(async id => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const latest = await (await fetch(`/api/roles/${id}`, { headers })).json()
    const response = await fetch(`/api/roles/${id}`, { method: 'PUT', headers,
      body: JSON.stringify({ ...latest, expected_revision: latest.revision, system_prompt: '角色远端新规则' }) })
    if (!response.ok) throw new Error('remote role update failed')
  }, role.id)
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('角色配置已更新')
  await expect(page.getByLabel('角色系统提示词', { exact: true })).toHaveValue('角色尚未提交的输入')
  await page.getByRole('button', { name: '采用最新角色配置', exact: true }).click()
  await expect(page.getByLabel('角色系统提示词', { exact: true })).toHaveValue('角色远端新规则')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '角色配置', exact: true })).toHaveCount(0)
  await page.getByRole('complementary', { name: '工作区侧栏' }).getByRole('button', { name: '打开设置', exact: true }).click()
  await page.getByRole('tab', { name: '提示词与规则', exact: true }).click()
  await world(page).getByLabel('自定义平台规则', { exact: true }).check()
  await world(page).getByLabel('平台协作规则', { exact: true }).fill('')
  await world(page).getByRole('button', { name: '保存世界提示词', exact: true }).click()
  await expect(world(page).getByRole('status')).toContainText('已保存')
  await world(page).getByRole('button', { name: '恢复平台默认', exact: true }).click()
  await expect(world(page).getByLabel('自定义平台规则', { exact: true })).not.toBeChecked()
  await world(page).getByRole('button', { name: '保存世界提示词', exact: true }).click()
  await expect(world(page).getByRole('status')).toContainText('已保存')
  const configuration = await page.evaluate(async () => (await fetch('/api/prompt-settings', {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).json())
  expect(configuration.platform_override).toBeNull()
})
