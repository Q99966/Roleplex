import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function seed(page: Page, marker: string) {
  return page.evaluate(async marker => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown) => {
      const response = await fetch('/api' + url, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('controlled context fixture failed')
      return response.json()
    }
    const config = await post('/model-configs', { name: marker, provider_type: 'openai_compatible', api_key: 'fake-world-context' })
    const role = await post('/roles', { name: marker, model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控角色' })
    const peer = await post('/roles', { name: marker + '同伴', model_config_id: config.id, model_name: 'fake-model', system_prompt: '受控同伴' })
    const conversation = await post('/conversations', { title: marker, type: 'group', role_ids: [role.id, peer.id] })
    await post(`/conversations/${conversation.id}/messages`, { parts: [{ type: 'text', text: marker }], mentions: [] })
    return { cid: conversation.id as number, rid: role.id as number }
  }, marker)
}

async function material(page: Page, cid: number) {
  return page.evaluate(async cid => {
    const response = await fetch(`/api/conversations/${cid}/context`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
    return { status: response.status, body: await response.json() }
  }, cid)
}

async function switchWorld(page: Page, target: string) {
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByLabel('切换世界', { exact: true }).selectOption(target)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  await ensureOwnerSession(page)
}

test('物理 World 切换和后端重启保留上下文，来源不会跨世界混入', async ({ page }) => {
  test.setTimeout(100_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const alpha = await seed(page, 'ALPHA_CONTEXT_SOURCE')
  const original = await material(page, alpha.cid)
  expect(original.status).toBe(200)
  expect(original.body.counts.included).toBe(1)
  await switchWorld(page, 'beta')
  expect((await material(page, alpha.cid)).status).toBe(404)
  const beta = await seed(page, 'BETA_CONTEXT_SOURCE')
  const other = await material(page, beta.cid)
  expect(other.body.entries.map((entry: { text: string }) => entry.text)).toEqual(['BETA_CONTEXT_SOURCE'])
  await switchWorld(page, 'alpha')
  const restored = await material(page, alpha.cid)
  expect(restored.status).toBe(200)
  expect(restored.body.revision).toBe(original.body.revision)
  expect(restored.body.entries).toEqual(original.body.entries)
})
