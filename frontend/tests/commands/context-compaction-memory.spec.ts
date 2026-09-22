import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function fixture(page: Page, name: string) {
  return page.evaluate(async name => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (path: string, body: unknown) => {
      const response = await fetch('/api' + path, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`controlled context fixture ${response.status}`)
      return response.json()
    }
    const config = await post('/model-configs', { name, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/roles', { name: name + '检索者', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控检索角色', builtin_tools: ['memory_search', 'memory_read'] })
    const peer = await post('/roles', { name: name + '同伴', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控同伴' })
    const group = await post('/conversations', { title: name, type: 'group', role_ids: [role.id, peer.id] })
    let sourceId = 0
    for (let i = 0; i < 8; i++) {
      const text = `项目约定 ${i}：采用邮箱登录。` + '受控讨论记录。'.repeat(70) + (i === 0 ? '\n历史校验编号：CONTEXT_MEMORY_REPLAY_73' : '')
      const sent = await post(`/conversations/${group.id}/messages`, { parts: [{ type: 'text', text }], mentions: [] })
      if (!i) sourceId = sent.message.id
    }
    return { cid: group.id as number, sourceId, role, peer }
  }, name)
}

async function openContext(page: Page) {
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '上下文', exact: true }).locator('span').last().click()
  await expect(page.getByRole('region', { name: '会话上下文管理', exact: true })).toBeVisible()
}

test('压缩对象固定为当前会话，摘要模型与查看角色独立且草稿按会话保留', async ({ page }, info) => {
  test.setTimeout(70_000); page.setDefaultTimeout(15_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '会话压缩范围验收', { cid, role, peer } = await fixture(page, name)
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await openContext(page)
  await page.getByLabel('上下文角色', { exact: true }).selectOption(String(peer.id))
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await expect(page.getByLabel('上下文角色', { exact: true })).toHaveCount(0)
  const scope = page.getByRole('region', { name: '压缩对象', exact: true })
  await expect(scope).toContainText(name)
  await expect(scope).toContainText('所有角色')
  await expect(page.getByLabel('生成摘要的模型', { exact: true })).toHaveValue(String(role.id))
  await page.getByLabel('生成摘要的模型', { exact: true }).selectOption(String(peer.id))
  await page.getByLabel('压缩保留重点').fill('保留整个会话的约定与待办。')
  await page.getByLabel('最近保留消息数').fill('2')
  await page.getByRole('button', { name: '占用与输入', exact: true }).click()
  await page.getByLabel('上下文角色', { exact: true }).selectOption(String(role.id))
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await expect(page.getByLabel('生成摘要的模型', { exact: true })).toHaveValue(String(peer.id))
  await expect(page.getByLabel('压缩保留重点')).toHaveValue('保留整个会话的约定与待办。')
  await expect(page.getByLabel('最近保留消息数')).toHaveValue('2')
  const shot = info.outputPath('conversation-compression-scope.png')
  await page.screenshot({ path: shot }); await info.attach('会话压缩对象与独立摘要模型', { path: shot, contentType: 'image/png' })
  const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith(`/conversations/${cid}/context/compressions`))
  await page.getByRole('button', { name: '开始压缩', exact: true }).click()
  expect((await sent).postDataJSON().role_id).toBe(peer.id)
  await expect(page.getByRole('region', { name: '压缩执行记录', exact: true })).toContainText('已采用摘要')
  const ids = await page.evaluate(async ({ cid, roles }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    return Promise.all(roles.map(async role_id => {
      const response = await fetch(`/api/conversations/${cid}/context/preview`, { method: 'POST', headers, body: JSON.stringify({ role_id }) })
      if (!response.ok) throw new Error('controlled shared summary preview failed')
      return (await response.json()).material.summary_id
    }))
  }, { cid, roles: [role.id, peer.id] })
  expect(ids[0]).toBeTruthy(); expect(ids[0]).toBe(ids[1])
})

test('主动压缩发布摘要，丢失响应找回同一任务，Agent 检索回读被省略原文并可回退', async ({ page }, info) => {
  test.setTimeout(100_000); page.setDefaultTimeout(15_000)
  await page.setViewportSize({ width: 1680, height: 1050 }); await ensureOwnerSession(page)
  const name = '压缩与回读验收', { cid, sourceId, role } = await fixture(page, name)
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await openContext(page)
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await page.getByLabel('最近保留消息数', { exact: true }).fill('2')
  await page.getByLabel('目标摘要长度', { exact: true }).fill('900')
  await page.getByLabel('压缩保留重点', { exact: true }).fill('保留登录约定和未完成事项。')
  let submissions = 0
  await page.route('**/context/compressions', async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return }
    submissions++
    expect((await route.fetch()).ok()).toBe(true)
    await route.abort('failed')
  })
  await page.getByRole('button', { name: '开始压缩', exact: true }).click()
  const record = page.getByRole('region', { name: '压缩执行记录', exact: true })
  await expect(record).toContainText('已采用摘要')
  expect(submissions).toBe(1)
  await page.unroute('**/context/compressions')
  await expect(record).toContainText('厂商累计输入 未知 · 输出 未知')
  await page.getByRole('button', { name: '会话材料', exact: true }).click()
  const material = page.getByRole('region', { name: '共享会话材料', exact: true })
  await expect(material).toContainText('当前摘要 · 覆盖 6 条原消息')
  await material.getByText('当前摘要 · 覆盖 6 条原消息', { exact: true }).click()
  await expect(material.locator('pre')).not.toContainText('CONTEXT_MEMORY_REPLAY_73')
  await page.getByRole('button', { name: '历史检索', exact: true }).click()
  const input = page.getByRole('textbox', { name: '消息输入框', exact: true })
  await input.fill('@'); await page.getByRole('option', { name: `@${role.name}`, exact: true }).click()
  await input.fill(`@${role.name} [MEMORY_PROBE] 历史校验编号`)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await expect(page.getByText(/^历史原文核对：/).first()).toContainText('CONTEXT_MEMORY_REPLAY_73', { timeout: 25_000 })
  await page.getByText('角色的工具来源记录', { exact: true }).click()
  const references = page.getByRole('region', { name: '工具历史来源记录', exact: true })
  await expect(references).toContainText('回读原文')
  const stored = await page.evaluate(async ({ cid, rid }) => (await fetch(`/api/conversations/${cid}/memory/references?role_id=${rid}`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json(), { cid, rid: role.id })
  expect(stored.items.filter((item: { source?: { message_id: number } }) => item.source?.message_id === sourceId)).toHaveLength(2)
  await page.getByLabel('历史搜索关键词', { exact: true }).fill('历史校验编号')
  await page.getByRole('button', { name: '搜索历史', exact: true }).click()
  await page.getByRole('article', { name: `历史来源 消息 #${sourceId}`, exact: true }).getByRole('button', { name: '读取来源原文', exact: true }).click()
  await expect(page.getByRole('region', { name: '历史原文', exact: true })).toContainText('CONTEXT_MEMORY_REPLAY_73')
  const shot = info.outputPath('compressed-memory-readback.png')
  await page.screenshot({ path: shot }); await info.attach('压缩后人工和角色回读原文', { path: shot, contentType: 'image/png' })
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await page.getByRole('button', { name: '恢复为原文', exact: true }).click()
  await expect(page.getByRole('region', { name: '主动压缩上下文' })).toContainText('已恢复使用原文')
  await page.reload(); await openContext(page)
  await page.getByRole('button', { name: '会话材料', exact: true }).click()
  await expect(material).not.toContainText('当前摘要 ·')
  await expect(material).toContainText('稳定 10')
})

test('窄屏压缩可停止，原消息与一次性要求保留，返回视图可继续检索', async ({ page }, info) => {
  test.setTimeout(70_000); page.setDefaultTimeout(15_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '停止压缩验收'
  await fixture(page, name)
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await openContext(page)
  await page.getByRole('button', { name: '主动压缩', exact: true }).click()
  await page.getByLabel('最近保留消息数', { exact: true }).fill('2')
  await page.getByLabel('压缩保留重点', { exact: true }).fill('保留既定约定。[COMPACT_SLOW]')
  await page.setViewportSize({ width: 390, height: 850 })
  await page.getByRole('button', { name: '收起侧边栏', exact: true }).click()
  await page.getByRole('button', { name: '打开会话详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '会话详情', exact: true })
  await expect(drawer.getByLabel('压缩保留重点')).toHaveValue('保留既定约定。[COMPACT_SLOW]')
  await drawer.getByRole('button', { name: '开始压缩', exact: true }).click()
  await drawer.getByRole('button', { name: '停止压缩任务', exact: true }).click()
  await expect(drawer.getByRole('region', { name: '压缩执行记录', exact: true })).toContainText('已取消')
  await drawer.getByRole('button', { name: '会话材料', exact: true }).click()
  await expect(drawer.getByRole('region', { name: '共享会话材料' })).toContainText('稳定 8')
  await drawer.getByRole('button', { name: '历史检索', exact: true }).click()
  await drawer.getByLabel('历史搜索关键词').fill('CONTEXT_MEMORY_REPLAY_73')
  await drawer.getByRole('button', { name: '搜索历史', exact: true }).click()
  await expect(drawer.getByRole('region', { name: '历史搜索结果' })).toContainText('CONTEXT_MEMORY_REPLAY_73')
  const shot = info.outputPath('context-maintenance-mobile.png')
  await page.screenshot({ path: shot }); await info.attach('窄屏停止压缩与检索', { path: shot, contentType: 'image/png' })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '打开会话详情', exact: true })).toBeFocused()
})

test('关联检索排除单聊私有资料，来源撤权后旧结果无法回读', async ({ page }) => {
  page.setDefaultTimeout(15_000); test.setTimeout(70_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const name = '检索共享边界验收', { cid, role } = await fixture(page, name)
  const source = await page.evaluate(async ({ cid, role }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown) => {
      const response = await fetch('/api' + url, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('controlled sharing fixture failed')
      return response.json()
    }
    const list = await (await fetch('/api/conversations', { headers })).json()
    const current = list.find((item: { id: number }) => item.id === cid)
    const third = await post('/roles', { name: '共享范围第三角色', model_config_id: role.model_config_id, model_name: 'fake-model', system_prompt: '受控角色' })
    const privateChat = await post('/conversations', { title: '不可向群共享的单聊', type: 'single', role_ids: [role.id] })
    await post(`/conversations/${privateChat.id}/messages`, { parts: [{ type: 'text', text: 'PRIVATE_GROUP_EXCLUDED_VALUE' }], mentions: [] })
    const shared = await post('/conversations', { title: '可共享的原始来源', type: 'group', role_ids: [...current.role_ids, third.id] })
    await post(`/conversations/${shared.id}/messages`, { parts: [{ type: 'text', text: 'SHARED_READ_REVOCATION_VALUE' }], mentions: [] })
    return { cid: shared.id, remaining: [...current.role_ids.filter((id: number) => id !== role.id), third.id] }
  }, { cid, role })
  await page.reload(); await page.getByRole('button', { name: `打开会话：${name}`, exact: true }).click()
  await openContext(page); await page.getByRole('button', { name: '历史检索', exact: true }).click()
  await page.getByLabel('历史搜索范围').selectOption('related')
  await page.getByLabel('历史搜索关键词').fill('PRIVATE_GROUP_EXCLUDED_VALUE')
  await page.getByRole('button', { name: '搜索历史', exact: true }).click()
  await expect(page.getByRole('region', { name: '历史搜索结果' })).toContainText('没有匹配结果')
  await page.getByLabel('历史搜索关键词').fill('SHARED_READ_REVOCATION_VALUE')
  await page.getByRole('button', { name: '搜索历史', exact: true }).click()
  const result = page.getByRole('region', { name: '历史搜索结果' })
  await expect(result).toContainText('可共享的原始来源')
  await page.evaluate(async source => {
    const response = await fetch(`/api/conversations/${source.cid}/members`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      body: JSON.stringify({ expected_revision: 0, role_ids: source.remaining }) })
    if (!response.ok) throw new Error('controlled source revocation failed')
  }, source)
  await result.getByRole('button', { name: '读取来源原文', exact: true }).click()
  const original = page.getByRole('region', { name: '历史原文', exact: true })
  await expect(original.getByRole('alert')).toContainText('目前不可读取')
  await expect(original).not.toContainText('SHARED_READ_REVOCATION_VALUE')
  await original.getByRole('button', { name: '返回搜索结果', exact: true }).click()
  await page.getByRole('button', { name: '搜索历史', exact: true }).click()
  await expect(result).toContainText('没有匹配结果')
})
