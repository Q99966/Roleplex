import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { readRunEvents } from '../e2e-log-assertions'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

for (const limit of [1, 4]) {
  test(`真实 World 群聊共享决策预算 ${limit}，刷新保留结果`, async ({ page }, testInfo) => {
    const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
    const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
    const title = limit === 1 ? '真实预算验收：额度不足（1 次）' : '真实预算验收：额度充足（4 次）'
    let cid: number | null = null, stage = 'World 与登录', failedStage: string | null = null, cleaned = false
    let observation: Record<string, unknown> = { limit }
    try {
      const health = await (await page.request.get(`${base}/api/health`)).json()
      if (health.world_managed !== true || health.world_name !== 'default') throw new Error('需要正常测试 World')
      await page.goto('/#/auth')
      await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
      await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
      await page.getByRole('button', { name: '进入工作台' }).click()
      await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })

      stage = '界面设置共享额度'
      await page.getByRole('complementary', { name: '工作区侧栏' }).getByRole('button', { name: '打开设置' }).click()
      await page.getByRole('tab', { name: /运行世界与存储/ }).click()
      const budget = page.getByRole('region', { name: '任务决策预算' })
      await expect(budget.getByRole('button', { name: '保存任务预算' })).toBeEnabled()
      await page.getByLabel('每个任务的决策上限', { exact: true }).fill(String(limit))
      await budget.getByRole('button', { name: '保存任务预算' }).click()
      await expect(budget.getByRole('status')).toHaveText('已保存，仅新任务生效。')
      await page.getByRole('button', { name: '关闭系统与环境设置' }).click()

      stage = '创建两个真实角色'
      cid = await page.evaluate(async ({ base, title, limit }) => {
        const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
        const request = async (route: string, body?: unknown) => {
          const response = await fetch(base + route, { headers, ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}) })
          if (!response.ok) throw new Error('真实群聊准备失败')
          return response.json()
        }
        const seed = (await request('/api/roles')).find((role: { model_config_id: number | null }) => role.model_config_id)
        const roles = []
        for (const name of ['甲', '乙']) {
          roles.push(await request('/api/roles', { name: `真实预算${limit}·角色${name}`, model_config_id: seed.model_config_id,
            model_name: seed.model_name, params: { max_tokens: 512 }, builtin_tools: [],
            system_prompt: '你是共享预算验收助手。请仅简短回答用户，不调用工具，不重复他人的回答。' }))
        }
        return (await request('/api/conversations', { title, type: 'group', role_ids: roles.map(role => role.id) })).id as number
      }, { base, title, limit })
      await page.reload()
      await page.getByRole('button', { name: `打开会话：${title}`, exact: true }).click()
      stage = '真实群聊请求'
      const input = page.getByLabel('消息输入框')
      await input.fill('@')
      await page.getByRole('option', { name: '@全部 · 按成员顺序回复', exact: true }).click()
      await input.fill(`${await input.inputValue()}请各自仅回复“收到”，不要调用工具。`)
      await page.getByLabel('发送消息').click()
      await expect.poll(async () => page.evaluate(async ({ base, cid }) => {
        const response = await fetch(`${base}/api/conversations/${cid}/messages`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
        const history = await response.json()
        const replies = history.items.filter((item: { sender_type: string }) => item.sender_type === 'role')
        return replies.length === 2 && replies.every((item: { status: string }) => ['done', 'stopped', 'error'].includes(item.status)) && history.active_generation_ids.length === 0
      }, { base, cid }), { timeout: 90000 }).toBe(true)

      stage = '终态与刷新核对'
      await page.reload()
      observation = await page.evaluate(async ({ base, cid, limit }) => {
        const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
        const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
        const replies = history.items.filter((item: { sender_type: string }) => item.sender_type === 'role')
        return { limit, statuses: replies.map((item: { status: string }) => item.status).sort(),
          stopped_reasons: replies.filter((item: { status: string }) => item.status === 'stopped').map((item: { stop_reason: string }) => item.stop_reason),
          shared_chain: new Set(replies.map((item: { chain_id: string }) => item.chain_id)).size === 1,
          active: history.active_generation_ids.length }
      }, { base, cid, limit })
      expect(observation.statuses).toEqual(limit === 1 ? ['done', 'stopped'] : ['done', 'done'])
      expect(observation.stopped_reasons).toEqual(limit === 1 ? ['decision_budget'] : [])
      expect(observation.shared_chain).toBe(true)
      await expect(page.getByText('达到本轮决策上限', { exact: true })).toHaveCount(limit === 1 ? 1 : 0)
      const calls = (await readRunEvents()).filter(event => event.conversation_id === cid && event.event === 'provider.call_completed')
      expect(calls).toHaveLength(limit === 1 ? 1 : 2)
      expect(calls.every(event => event.provider_mode === 'real')).toBe(true)
      observation.provider_calls = calls.length
      observation.model = calls[0]?.model ?? null
      observation.output_tokens = calls.length && calls.every(event => typeof event.output_tokens === 'number')
        ? calls.reduce((sum, event) => sum + Number(event.output_tokens), 0) : null
      observation.passed = true
    } catch {
      failedStage = stage
    } finally {
      if (cid !== null) cleaned = await page.evaluate(async ({ base, cid }) => (await fetch(`${base}/api/conversations/${cid}/stop`, {
        method: 'POST', signal: AbortSignal.timeout(5000), headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      })).ok, { base, cid }).catch(() => false)
      await page.goto('about:blank').catch(() => undefined)
      const report = testInfo.outputPath(`workflow-budget-${limit}.json`)
      await writeFile(report, JSON.stringify({ ...observation, failed_stage: failedStage, cleanup_passed: cleaned }))
      await testInfo.attach('真实 World 共享预算', { path: report, contentType: 'application/json' })
    }
    if (failedStage || !cleaned) throw new Error(`真实 World 预算验证失败：${failedStage ?? '清理'}`)
  })
}
