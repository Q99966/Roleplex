import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'
import { execFileSync } from 'node:child_process'
import path from 'node:path'

async function fixture(page: Page, title: string) {
  return page.evaluate(async ({ title, base }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(base + '/api' + route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`workbench fixture ${response.status}`)
      return response.json()
    }
    const config = await request('/model-configs', { name: title, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/roles', { name: title + '角色', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控工作台角色', builtin_tools: [] })
    const conv = await request('/conversations', { type: 'single', title, role_ids: [role.id] })
    const definitions = []
    for (const name of ['模板甲', '模板乙']) {
      const id = crypto.randomUUID()
      await request(`/conversations/${conv.id}/workflows/definitions/${id}`, { name, expected_revision: 0, graph: {
        runtime_version: 2, nodes: [{ id: 'a', kind: 'approval', title: '开始确认', position: { x: 0, y: 80 } },
          { id: 'b', kind: 'approval', title: '结束确认', position: { x: 320, y: 80 } }], edges: [['a', 'b']],
      } }, 'PUT')
      definitions.push(id)
    }
    return { cid: conv.id as number, definitions }
  }, { title, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}
async function snapshot(page: Page, cid: number) {
  return page.evaluate(async ({ cid, base }) => (await fetch(`${base}/api/conversations/${cid}/workflows`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
  })).json(), { cid, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}
async function open(page: Page, title: string) {
  await page.reload(); await page.getByRole('button', { name: `打开会话：${title}`, exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: '打开模板：模板甲', exact: true }).click()
}

test('主操作与唯一上下文面板；切换保留草稿，模板和本次运行分别提交', async ({ page }, info) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1720, height: 1080 }); await ensureOwnerSession(page)
  const title = '工作流对象与操作验收', { cid, definitions } = await fixture(page, title)
  await open(page, title)
  const toolbar = page.getByRole('toolbar', { name: '工作流操作', exact: true })
  const inspector = page.getByRole('region', { name: '工作流上下文', exact: true })
  const canvas = page.getByRole('region', { name: '工作流画布', exact: true })
  await expect(toolbar).toContainText('流程模板')
  await canvas.getByRole('button', { name: '节点 1：开始确认', exact: true }).click()
  await expect(inspector).toHaveCount(1)
  await expect(canvas.getByLabel('节点名称', { exact: true })).toHaveCount(0)
  await inspector.getByLabel('节点名称', { exact: true }).fill('待人工检查')
  await toolbar.getByLabel('工作流对象').selectOption('template:' + definitions[1])
  await toolbar.getByLabel('工作流对象').selectOption('template:' + definitions[0])
  await expect(canvas.getByRole('button', { name: '节点 1：待人工检查', exact: true })).toBeVisible()
  expect((await snapshot(page, cid)).definitions.find((item: { id: string }) => item.id === definitions[0]).graph.nodes[0].title).toBe('开始确认')
  await toolbar.getByRole('button', { name: '保存模板', exact: true }).click()
  await expect(toolbar).toContainText('已提交')
  await toolbar.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(toolbar).toContainText('等待人工确认')
  const rid = (await snapshot(page, cid)).runs[0].id
  await toolbar.getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name: '编辑本次运行图', exact: true }).click()
  await expect(toolbar).toContainText('本次运行调整')
  await canvas.getByRole('button', { name: '节点 2：结束确认', exact: true }).click()
  await inspector.getByLabel('节点名称', { exact: true }).fill('只改本次运行')
  await toolbar.getByRole('button', { name: '提交本次运行调整', exact: true }).click()
  await expect(toolbar).toContainText('已提交')
  await toolbar.getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name: '编辑原模板', exact: true }).click()
  await expect(toolbar).toContainText('流程模板')
  await canvas.getByRole('button', { name: '节点 2：结束确认', exact: true }).click()
  await inspector.getByLabel('节点名称', { exact: true }).fill('以后模板使用')
  await toolbar.getByRole('button', { name: '保存模板', exact: true }).click()
  await expect(toolbar).toContainText('已提交')
  const after = await snapshot(page, cid)
  expect(after.definitions.find((item: { id: string }) => item.id === definitions[0]).graph.nodes[1].title).toBe('以后模板使用')
  expect(after.runs.find((item: { id: string }) => item.id === rid).graph.nodes[1].title).toBe('只改本次运行')
  const shot = info.outputPath('workflow-toolbar-context.png')
  await page.screenshot({ path: shot }); await info.attach('统一工具栏与节点上下文', { path: shot, contentType: 'image/png' })
  await toolbar.getByLabel('工作流对象').selectOption('run:' + rid)
  await toolbar.getByRole('button', { name: '停止运行', exact: true }).click()
  await expect(toolbar).toContainText('已停止')
})

test('历史视图刷新仍只读，记录按需打开，窄屏详情可通过键盘返回', async ({ page }, info) => {
  test.setTimeout(100_000); await page.setViewportSize({ width: 1720, height: 1080 }); await ensureOwnerSession(page)
  const title = '工作流历史与窄屏验收', { cid } = await fixture(page, title)
  await open(page, title)
  const toolbar = page.getByRole('toolbar', { name: '工作流操作', exact: true })
  await toolbar.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(toolbar).toContainText('等待人工确认')
  await toolbar.getByRole('button', { name: '记录', exact: true }).click()
  await page.getByLabel('查看运行图版本').selectOption('1')
  await expect(toolbar).toContainText('历史只读')
  await expect(toolbar.getByRole('button', { name: /保存模板|启动流程|停止运行|提交本次运行调整/ })).toHaveCount(0)
  await page.route('**/workflows/graphs/run/*?graph_revision=1', route => route.fulfill({ status: 503, json: { detail: { code: 'CONTROLLED_UNAVAILABLE' } } }))
  await page.reload()
  await expect(toolbar).toContainText('历史只读')
  await expect(toolbar.getByRole('alert')).toContainText('历史版本未能恢复')
  await expect(toolbar.getByRole('button', { name: /保存模板|启动流程|停止运行|提交本次运行调整/ })).toHaveCount(0)
  await page.unroute('**/workflows/graphs/run/*?graph_revision=1')
  await page.reload()
  await expect(toolbar).toContainText('历史只读')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await toolbar.getByRole('button', { name: '返回当前运行', exact: true }).click()
  await expect(toolbar).toContainText('本次运行')
  await page.setViewportSize({ width: 430, height: 900 })
  if (await page.getByRole('button', { name: '收起侧边栏', exact: true }).isVisible()) await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.getByLabel('定位节点', { exact: true }).selectOption('a')
  await toolbar.getByRole('button', { name: '详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '会话详情', exact: true })
  await expect(drawer.getByRole('region', { name: '工作流上下文' })).toBeVisible()
  await drawer.getByRole('button', { name: '查看结果与执行事实', exact: true }).click()
  const attempt = page.getByRole('dialog', { name: '节点尝试详情', exact: true })
  await expect(attempt).toBeVisible()
  await page.keyboard.press('Tab')
  await expect.poll(() => attempt.evaluate(element => element.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Escape')
  await expect(attempt).not.toBeVisible()
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('button', { name: '查看结果与执行事实', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(drawer).not.toBeVisible()
  await expect(toolbar.getByRole('button', { name: '详情', exact: true })).toBeFocused()
  const shot = info.outputPath('workflow-toolbar-mobile.png')
  await page.screenshot({ path: shot }); await info.attach('窄屏主操作', { path: shot, contentType: 'image/png' })
  await toolbar.getByRole('button', { name: '停止运行', exact: true }).click()
  await expect(toolbar).toContainText('已停止')
  expect((await snapshot(page, cid)).runs).toHaveLength(1)
})

test('同一浏览器切换 Guest 不恢复 Owner 工作流草稿或开放编辑入口', async ({ page }) => {
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const title = '工作流身份隔离验收', { cid } = await fixture(page, title)
  await open(page, title)
  await page.getByLabel('流程名称', { exact: true }).fill('Owner 私有未提交草稿')
  await expect(page.getByRole('toolbar', { name: '工作流操作' }).getByRole('status')).toContainText('本地已保存')
  const database = path.resolve(process.cwd(), '..', process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0], 'roleplex.db')
  execFileSync('python', ['tests/seed_tool_viewer.py', database, String(cid)], { cwd: path.resolve(process.cwd(), '../backend') })
  await page.evaluate(async stamp => {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: `test${stamp}_toolviewer`, password: 'Roleplex-Test-1234' }) })
    if (!response.ok) throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token', (await response.json()).access_token)
  }, process.env.ROLEPLEX_COMMAND_E2E_STAMP)
  await page.reload()
  await page.getByRole('button', { name: '打开会话：工作流身份隔离验收', exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await expect(page.getByText('流程由 Owner 管理，可在对话中查看允许公开的执行摘要。', { exact: true })).toBeVisible()
  await expect(page.getByRole('toolbar', { name: '工作流操作' })).toHaveCount(0)
  await expect(page.getByText('Owner 私有未提交草稿', { exact: true })).toHaveCount(0)
  const statuses = await page.evaluate(async cid => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    return Promise.all(['', '/draft-scope'].map(async suffix => (await fetch(`/api/conversations/${cid}/workflows${suffix}`, { headers })).status))
  }, cid)
  expect(statuses).toEqual([403, 403])
})


test('完成后准备新运行使用新的请求身份，不误返回上次结果', async ({ page }) => {
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const title = '工作流重复运行验收', { cid } = await fixture(page, title)
  await open(page, title)
  const toolbar = page.getByRole('toolbar', { name: '工作流操作', exact: true })
  const inspector = page.getByRole('region', { name: '工作流上下文', exact: true })
  await toolbar.getByRole('button', { name: '启动流程', exact: true }).click()
  for (const [index, title] of ['开始确认', '结束确认'].entries()) {
    await expect(toolbar.getByRole('status')).toContainText('等待人工确认')
    await page.getByRole('button', { name: `节点 ${index + 1}：${title}`, exact: true }).click()
    await inspector.getByRole('button', { name: '确认继续', exact: true }).click()
  }
  await expect(toolbar.getByRole('status')).toContainText('本次执行结束')
  const first = (await snapshot(page, cid)).runs[0]
  await toolbar.getByRole('button', { name: '准备新运行', exact: true }).click()
  await expect(toolbar.getByLabel('工作流对象')).toHaveValue(`template:${first.definition_id}`)
  await toolbar.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(toolbar.getByRole('status')).toContainText('等待人工确认')
  const after = (await snapshot(page, cid)).runs
  expect(after).toHaveLength(2)
  expect(after[0].id).not.toBe(first.id)
  expect(after[0].chain_id).not.toBe(first.chain_id)
  await toolbar.getByRole('button', { name: '停止运行', exact: true }).click()
  await expect(toolbar.getByRole('status')).toContainText('已停止')
})
