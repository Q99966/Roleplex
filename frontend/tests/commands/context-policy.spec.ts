import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  return page.evaluate(async name => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch('/api' + path, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`controlled policy fixture ${response.status}`)
      return response.json()
    }
    const config = await post('/model-configs', { name, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const roles = []
    for (const window of [200000, 1000000, 100000]) roles.push(await post('/roles', { name: `${name}-${window}`, model_config_id: config.id,
      model_name: 'fake-model', system_prompt: '受控角色', context_window_tokens: window }))
    const group = await post('/conversations', { title: name, type: 'group', role_ids: roles.map(role => role.id) })
    for (let i = 0; i < 8; i++) await post(`/conversations/${group.id}/messages`, { parts: [{ type: 'text', text: `已确认目标 ${i}。` + '受控讨论。'.repeat(100) }], mentions: [] })
    return { cid: group.id as number, roles }
  }, name)
}

async function openPolicy(page: Page, name: string) {
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '上下文', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await page.locator('summary').filter({ hasText: /^自动压缩设置$/ }).click()
  return page.getByRole('region', { name: '会话自动压缩设置', exact: true })
}

test('会话阈值建议、世界继承、窗口变化和冲突保留草稿', async ({ page }, info) => {
  test.setTimeout(80_000); page.setDefaultTimeout(12000)
  await page.setViewportSize({ width: 1680, height: 1050 }); await ensureOwnerSession(page)
  const name = '压缩阈值规则验收', { cid, roles } = await fixture(page, name)
  const panel = await openPolicy(page, name)
  const limits = panel.getByLabel('会话阈值建议')
  await expect(limits).toContainText('100,000 − 50,000 = 50,000')
  await panel.getByLabel('为当前会话单独设置', { exact: true }).check()
  await panel.getByLabel('自动压缩触发阈值').fill('100001')
  await expect(panel.getByRole('button', { name: '保存压缩设置' })).toBeDisabled()
  await expect(panel.getByRole('alert')).toContainText('不能高于')
  await panel.getByLabel('自动压缩触发阈值').fill('90000')
  await panel.getByRole('button', { name: '保存压缩设置' }).click()
  await expect(panel.getByRole('status')).toHaveText('压缩设置已同步')
  await panel.getByLabel('自动压缩提示词').fill('本地尚未保存的要求')
  await page.evaluate(async ({ cid, role }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const changed = await fetch(`/api/roles/${role.id}`, { method: 'PUT', headers, body: JSON.stringify({ ...role,
      expected_revision: role.revision, context_window_tokens: 32000 }) })
    if (!changed.ok) throw new Error('controlled role window update failed')
    const url = `/api/conversations/${cid}/context/policy`, current = await (await fetch(url, { headers })).json()
    const saved = await fetch(url, { method: 'PUT', headers, body: JSON.stringify({ expected_revision: current.revision,
      policy: { ...current.policy, trigger_tokens: 30000, instructions: '另一个窗口的新版本' } }) })
    if (!saved.ok) throw new Error('controlled policy concurrent update failed')
  }, { cid, role: roles[2] })
  await panel.getByRole('button', { name: '刷新压缩设置' }).click()
  await expect(limits).toContainText('32,000 − 16,000 = 16,000')
  await expect(panel.getByLabel('自动压缩提示词')).toHaveValue('本地尚未保存的要求')
  await panel.getByRole('button', { name: '采用服务端压缩设置' }).click()
  await expect(panel.getByLabel('自动压缩提示词')).toHaveValue('另一个窗口的新版本')
  await limits.scrollIntoViewIfNeeded()
  const shot = info.outputPath('context-threshold-policy.png')
  await page.screenshot({ path: shot }); await info.attach('最小角色窗口与阈值建议', { path: shot, contentType: 'image/png' })
  await page.getByRole('complementary', { name: '工作区侧栏' }).getByRole('button', { name: '打开设置', exact: true }).click()
  await page.getByRole('tab', { name: '提示词与规则', exact: true }).click()
  const world = page.getByRole('region', { name: '世界自动压缩设置' })
  await world.getByLabel('自动压缩提示词').fill('来自世界默认的保留要求')
  await world.getByRole('button', { name: '保存压缩设置' }).click()
  await expect(world.getByRole('status')).toHaveText('压缩设置已同步')
  await page.getByRole('button', { name: '关闭系统与环境设置', exact: true }).click()
  await panel.getByLabel('为当前会话单独设置', { exact: true }).uncheck()
  await panel.getByRole('button', { name: '保存压缩设置' }).click()
  await expect(panel.getByLabel('自动压缩提示词')).toHaveValue('来自世界默认的保留要求')
})

test('设置自动压缩后继续原会话，自动提示词和本次要求独立', async ({ page }, info) => {
  test.setTimeout(70_000); page.setDefaultTimeout(15000)
  await page.setViewportSize({ width: 1680, height: 1050 }); await ensureOwnerSession(page)
  const name = '自动压缩继续任务验收', { roles } = await fixture(page, name)
  const panel = await openPolicy(page, name)
  await panel.getByLabel('为当前会话单独设置', { exact: true }).check()
  await panel.getByLabel('启用自动压缩', { exact: true }).check()
  await panel.getByLabel('自动压缩触发阈值').fill('4000')
  await panel.getByLabel('自动压缩目标规模').fill('2000')
  await panel.getByLabel('自动压缩提示词').fill('保留所有角色已经确认的目标')
  await panel.getByText('保留与频率', { exact: true }).click()
  await panel.getByLabel('自动压缩最近保留条数').fill('1')
  await panel.getByLabel('自动摘要长度上限').fill('700')
  await panel.getByRole('button', { name: '保存压缩设置' }).click()
  await expect(panel.getByRole('status')).toHaveText('压缩设置已同步')
  await page.getByLabel('压缩保留重点', { exact: true }).fill('这个要求仅用于主动压缩')
  const input = page.getByRole('textbox', { name: '消息输入框', exact: true })
  await input.fill('@'); await page.getByRole('option', { name: `@${roles[0].name}`, exact: true }).click()
  await input.fill(`@${roles[0].name} 继续已确认的目标`)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  const record = page.getByRole('region', { name: '压缩执行记录', exact: true })
  await expect(record).toContainText('已采用摘要', { timeout: 20000 })
  await expect(record).toContainText('自动触发 · 共用原任务预算')
  await expect(page.getByLabel('压缩保留重点', { exact: true })).toHaveValue('这个要求仅用于主动压缩')
  await expect(page.getByRole('button', { name: '停止生成', exact: true })).toHaveCount(0)
  await record.getByText('本次范围与要求', { exact: true }).click()
  await expect(record).toContainText('保留所有角色已经确认的目标')
  await expect(record).not.toContainText('这个要求仅用于主动压缩')
  const shot = info.outputPath('automatic-compaction-result.png')
  await page.screenshot({ path: shot }); await info.attach('自动压缩并继续原任务', { path: shot, contentType: 'image/png' })
})
