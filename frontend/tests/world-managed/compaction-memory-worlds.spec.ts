import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function api(page: Page, path: string, body?: unknown) {
  return page.evaluate(async ({ path, body }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const response = await fetch('/api' + path, body === undefined ? { headers } : { method: 'POST', headers, body: JSON.stringify(body) })
    return { status: response.status, value: await response.json() }
  }, { path, body })
}

async function seed(page: Page, marker: string) {
  const cfg = (await api(page, '/model-configs', { name: marker, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })).value
  const a = (await api(page, '/roles', { name: marker + '甲', model_config_id: cfg.id, model_name: 'fake-model', system_prompt: '受控角色', builtin_tools: ['memory_search', 'memory_read'] })).value
  const b = (await api(page, '/roles', { name: marker + '乙', model_config_id: cfg.id, model_name: 'fake-model', system_prompt: '受控角色' })).value
  const conversation = (await api(page, '/conversations', { title: marker, type: 'group', role_ids: [a.id, b.id] })).value
  for (let i = 0; i < 4; i++) {
    expect((await api(page, `/conversations/${conversation.id}/messages`, { parts: [{ type: 'text', text: marker + '：约定使用邮件登录。' + '受控历史。'.repeat(80) }], mentions: [] })).status).toBe(202)
  }
  return { cid: conversation.id as number, rid: a.id as number }
}

async function switchWorld(page: Page, target: string) {
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByLabel('切换世界', { exact: true }).selectOption(target)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  await ensureOwnerSession(page)
}

async function setPolicy(page: Page, instructions: string, enabled: boolean) {
  return page.evaluate(async ({ instructions, enabled }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const value = await (await fetch('/api/context-policy', { headers })).json()
    const response = await fetch('/api/context-policy', { method: 'PUT', headers, body: JSON.stringify({ expected_revision: value.revision,
      policy: { ...value.policy, enabled, trigger_tokens: 2000, target_tokens: 1000, keep_recent: 1, summary_tokens: 700, instructions } }) })
    if (!response.ok) throw new Error('controlled world policy update failed')
    return response.json()
  }, { instructions, enabled })
}

test('自动策略与摘要在物理重启后保留，历史引用跨 World 被拒绝', async ({ page }) => {
  test.setTimeout(100_000); page.setDefaultTimeout(15_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  const alpha = await seed(page, 'ALPHA_COMPRESSION_SOURCE')
  const base = `/conversations/${alpha.cid}/context`
  await setPolicy(page, 'ALPHA_AUTO_POLICY', true)
  const sent = await api(page, `/conversations/${alpha.cid}/messages`, { parts: [{ type: 'text', text: '继续当前会话目标' }], mentions: [alpha.rid] })
  expect(sent.status).toBe(202)
  await expect.poll(async () => (await api(page, base + '/compressions')).value.jobs[0]?.status).toBe('completed')
  expect((await api(page, base + '/compressions')).value.jobs[0].trigger).toBe('automatic')
  await expect.poll(async () => (await api(page, `/conversations/${alpha.cid}/messages`)).value.active_generation_ids.length).toBe(0)
  const original = (await api(page, base)).value
  const search = (await api(page, `/conversations/${alpha.cid}/memory/search`, { role_id: alpha.rid, query: 'ALPHA_COMPRESSION_SOURCE', kinds: ['message'] })).value
  const reference = search.results[0].reference
  await switchWorld(page, 'beta')
  expect((await api(page, '/context-policy')).value.policy.enabled).toBe(false)
  await setPolicy(page, 'BETA_AUTO_POLICY', false)
  const beta = await seed(page, 'BETA_COMPRESSION_SOURCE')
  const invalid = await api(page, `/conversations/${beta.cid}/memory/read`, { role_id: beta.rid, reference })
  expect(invalid.status).toBe(404)
  expect(JSON.stringify(invalid.value)).not.toContain('ALPHA_COMPRESSION_SOURCE')
  await switchWorld(page, 'alpha')
  const restoredPolicy = (await api(page, '/context-policy')).value.policy
  expect(restoredPolicy.enabled).toBe(true)
  expect(restoredPolicy.instructions).toBe('ALPHA_AUTO_POLICY')
  const restored = (await api(page, base)).value
  expect(restored.active_summary.id).toBe(original.active_summary.id)
  expect(restored.active_summary.text).toBe(original.active_summary.text)
  expect(restored.revision).toBe(original.revision)
  const read = await api(page, `/conversations/${alpha.cid}/memory/read`, { role_id: alpha.rid, reference })
  expect(read.status).toBe(200); expect(read.value.text).toContain('ALPHA_COMPRESSION_SOURCE')
})
