import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function setup(page: Page) {
  page.setDefaultTimeout(10_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await ensureOwnerSession(page)
  const fixture = await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown, method = 'POST') => {
      const r = await fetch(url, { method, headers, body: JSON.stringify(body) }); if (!r.ok) throw new Error(`fixture ${r.status}`); return r.json()
    }
    const cfg = await post('/api/model-configs', { name: `草稿测试-${crypto.randomUUID()}`, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: `草稿角色-${crypto.randomUUID()}`, model_config_id: cfg.id, model_name: 'fake-model', system_prompt: '受控角色' })
    const didTitle = crypto.randomUUID()
    const conv = await post('/api/conversations', { type: 'single', title: `本地草稿验收-${didTitle}`, role_ids: [role.id] })
    const did = crypto.randomUUID()
    await post(`/api/conversations/${conv.id}/workflows/definitions/${did}`, { name: '原始流程', expected_revision: 0,
      graph: { runtime_version: 2, nodes: [{ id: 'a', kind: 'approval', title: '人工确认', inputs: [], position: { x: 0, y: 0 } }], edges: [] } }, 'PUT')
    return { cid: conv.id as number, did, title: conv.title as string }
  })
  await page.reload()
  await page.getByRole('button', { name: `打开会话：${fixture.title}`, exact: true }).click()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: '原始流程 v1' }).click()
  return fixture
}

async function listing(page: Page, cid: number) {
  return page.evaluate(async cid => (await fetch(`/api/conversations/${cid}/workflows`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json(), cid)
}

test('立即刷新恢复完整编辑；远端更新保留本地冲突；显式保存后不恢复旧脏状态', async ({ page }) => {
  const { cid, did } = await setup(page)
  await page.getByRole('button', { name: '添加角色任务', exact: true }).click()
  await page.getByLabel('本步任务', { exact: true }).fill('尚未提交的任务')
  await page.getByLabel('节点颜色', { exact: true }).fill('#8040c0')
  await page.getByLabel('本次运行补充要求', { exact: true }).fill('保留补充要求')
  await page.getByLabel('流程名称', { exact: true }).fill('立即刷新草稿')
  await page.reload()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('立即刷新草稿')
  await expect(page.getByLabel('本次运行补充要求', { exact: true })).toHaveValue('保留补充要求')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await page.getByRole('button', { name: '节点 2：角色任务', exact: true }).click()
  await expect(page.getByLabel('本步任务', { exact: true })).toHaveValue('尚未提交的任务')
  await expect(page.getByLabel('节点颜色', { exact: true })).toHaveValue('#8040c0')
  let remote = await listing(page, cid)
  expect(remote.definitions[0].revision).toBe(1); expect(remote.definitions[0].graph.nodes).toHaveLength(1); expect(remote.runs).toHaveLength(0)
  await page.evaluate(async ({ cid, did }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const r = await fetch(`/api/conversations/${cid}/workflows/definitions/${did}`, { method: 'PUT', headers, body: JSON.stringify({ name: '远端更新', expected_revision: 1, graph: { runtime_version: 2, nodes: [{ id: 'a', kind: 'approval', title: '远端节点' }], edges: [] } }) })
    if (!r.ok) throw new Error(`remote ${r.status}`)
  }, { cid, did })
  await page.reload()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('立即刷新草稿')
  await expect(page.getByRole('button', { name: '保存流程', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '另存本地草稿', exact: true }).click()
  await page.getByRole('button', { name: '保存流程', exact: true }).click()
  await expect(page.getByText('已保存版本 1', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('已保存版本 1', { exact: true })).toBeVisible()
  remote = await listing(page, cid)
  expect(remote.definitions).toHaveLength(2); expect(remote.runs).toHaveLength(0)
})

test('多标签页编辑互不覆盖，存储失败可备份', async ({ page, context }) => {
  await setup(page)
  await page.getByLabel('流程名称', { exact: true }).fill('标签页甲')
  await expect(page.getByText('草稿已保存到此浏览器', { exact: true })).toBeVisible()
  const second = await context.newPage()
  await second.goto(page.url())
  await expect(second.getByLabel('流程名称', { exact: true })).toHaveValue('标签页甲')
  await second.getByLabel('流程名称', { exact: true }).fill('标签页乙')
  await expect(second.getByText('草稿已保存到此浏览器', { exact: true })).toBeVisible()
  await page.reload(); await second.reload()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('标签页甲')
  await expect(second.getByLabel('流程名称', { exact: true })).toHaveValue('标签页乙')
  await second.close()
  await page.addInitScript(() => { IDBFactory.prototype.open = () => { throw new DOMException('受控不可用', 'QuotaExceededError') } })
  await page.reload()
  // IDB 不可用仍允许编辑，但不能假装已经持久保存。
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await expect(page.getByText('本地草稿保存失败，请下载备份或重试', { exact: true })).toBeVisible()
  await page.getByLabel('流程名称', { exact: true }).fill('存储失败仍可编辑')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载草稿备份', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('workflow-draft.json')
})


test('运行图草稿刷新后仍属于原运行，不启动新流程；损坏副本跳过', async ({ page }) => {
  const { cid } = await setup(page)
  await page.getByRole('button', { name: '启动流程', exact: true }).click()
  await expect(page.getByText(/等待人工确认 · 定义快照/)).toBeVisible()
  await page.getByRole('button', { name: '编辑运行图', exact: true }).click()
  await page.getByRole('button', { name: '添加人工确认', exact: true }).click()
  await page.getByLabel('节点名称', { exact: true }).fill('运行中待提交节点')
  await page.reload()
  await expect(page.getByText('正在编辑本次运行图 · 不修改流程模板', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '节点 2：运行中待提交节点', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '启动流程', exact: true })).toBeDisabled()
  const remote = await listing(page, cid)
  expect(remote.runs).toHaveLength(1); expect(remote.runs[0].graph.nodes).toHaveLength(1)
  expect(remote.definitions[0].graph.nodes).toHaveLength(1)
  await expect(page.getByText('草稿已保存到此浏览器', { exact: true })).toBeVisible()
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => { const request = indexedDB.open('roleplex-workflow-drafts'); request.onsuccess = () => resolve(request.result); request.onerror = reject })
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('drafts', 'readwrite'), store = tx.objectStore('drafts'), request = store.getAll()
      request.onsuccess = () => { for (const record of request.result) { record.draft.definition.graph.nodes = null; store.put(record) } }
      tx.oncomplete = () => resolve(); tx.onerror = reject
    })
    db.close()
    for (const key of Object.keys(sessionStorage)) if (key.startsWith('roleplex:workflow-draft-journal:')) sessionStorage.removeItem(key)
  })
  await page.reload()
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await expect(page.getByText('部分本地副本损坏，已跳过；原始副本仍保留。', { exact: true })).toBeVisible()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('新工作流')
})


test('手动恢复保留被替换的编辑，同一 Owner 重新登录后继续恢复', async ({ page }) => {
  const { title } = await setup(page)
  await page.getByLabel('流程名称', { exact: true }).fill('恢复点甲')
  await page.reload()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('恢复点甲')
  await page.getByLabel('流程名称', { exact: true }).fill('恢复点乙')
  await page.getByText(/恢复副本（/).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('region', { name: '本地草稿', exact: true }).locator('div').filter({ has: page.locator('span', { hasText: '恢复点甲' }) }).getByRole('button', { name: '恢复', exact: true }).click()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('恢复点甲')
  await page.reload()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('恢复点甲')
  await page.getByText(/恢复副本（/).click()
  await expect(page.getByRole('region', { name: '本地草稿', exact: true }).locator('span', { hasText: '恢复点乙' })).toBeVisible()
  await expect(page.getByText('草稿已保存到此浏览器', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await ensureOwnerSession(page)
  await page.getByRole('button', { name: `打开会话：${title}`, exact: true }).click()
  await expect(page.getByLabel('流程名称', { exact: true })).toHaveValue('恢复点甲')
})
