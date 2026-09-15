import { test, expect } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 Provider 读取版本后一次多片段修改并保留未修改内容', async ({ page }, testInfo) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', 'replacements')
  await mkdir(root)
  const tail = `proof=${randomBytes(12).toString('hex')}\n`
  await writeFile(path.join(root, 'sample.txt'), 'alpha=1\nbeta=2\ngamma=3\n' + tail)
  let cid: number | null = null, stage = '准备', failedStage: string | null = null, cleaned = false
  let observation: Record<string, unknown> = {}
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })
    cid = await page.evaluate(async ({ base, root }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const request = async (route: string, body?: unknown, method = 'POST') => {
        const response = await fetch(base + route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
        if (!response.ok) throw new Error('真实替换准备失败')
        return response.json()
      }
      const seed = (await request('/api/roles')).find((role: { model_config_id: number | null }) => role.model_config_id)
      const workspace = await request('/api/workspaces', { display_name: '真实多片段', root_path: root, acknowledge_existing_content: true })
      await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
      const role = await request('/api/roles', { name: '真实多片段助手', model_config_id: seed.model_config_id, model_name: seed.model_name,
        params: { max_tokens: 1024 }, system_prompt: '依据实际读取版本执行精确编辑，不能猜 hash，不重试失败操作。', builtin_tools: ['workspace_read', 'workspace_edit'] })
      return (await request('/api/conversations', { title: '真实多片段验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
    }, { base, root })
    await page.reload()
    await page.getByRole('button', { name: '打开会话：真实多片段验收', exact: true }).click()
    stage = '真实读取与多片段调用'
    await page.getByLabel('消息输入框').fill('先读取 sample.txt 获得真实 sha256，再仅调用一次 workspace_edit，用顶层 replacements 数组把 alpha=1 改为 alpha=10、beta=2 改为 beta=20、gamma=3 改为 gamma=30。expected_sha256 使用读取结果，保持其余内容与换行不变。不要用 old_text/new_text 顶层旧形式，不要分三次调用，失败就停止。最后简短报告结果，不重复文件正文。')
    await page.getByLabel('发送消息').click()
    await expect.poll(async () => page.evaluate(async ({ base, cid }) => {
      const response = await fetch(`${base}/api/conversations/${cid}/messages`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
      const history = await response.json()
      return history.items.some((item: { sender_type: string; status: string }) => item.sender_type === 'role' && item.status !== 'generating') && history.active_generation_ids.length === 0
    }, { base, cid }), { timeout: 90000 }).toBe(true)
    stage = '文件与凭据核对'
    observation = await page.evaluate(async ({ base, cid }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      const message = history.items.find((item: { sender_type: string }) => item.sender_type === 'role')
      const edits = message.parts_json.filter((part: { tool_name?: string }) => part.tool_name === 'workspace_edit')
      if (edits.length !== 1) return { edit_count: edits.length, verified: false }
      const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${edits[0].call_id}`, { headers })).json()
      const input = JSON.parse(detail.input.text)
      return { edit_count: edits.length, replacement_count: input.replacement_count, one_diff: detail.write?.files?.length === 1,
        verified: edits[0].status === 'success' && input.replacement_count === 3, normal_finish: message.status === 'done' }
    }, { base, cid })
    observation.file_matches = (await readFile(path.join(root, 'sample.txt'), 'utf8')) === 'alpha=10\nbeta=20\ngamma=30\n' + tail
    if (!observation.verified || !observation.file_matches || !observation.one_diff || !observation.normal_finish) throw new Error('真实替换核对失败')
    const calls = (await readRunEvents()).filter(event => event.conversation_id === cid && event.event === 'provider.call_completed')
    observation.provider_calls = calls.length
    observation.model = calls[0]?.model ?? null
    observation.output_tokens = calls.length && calls.every(event => typeof event.output_tokens === 'number') ? calls.reduce((sum,event) => sum + Number(event.output_tokens), 0) : null
  } catch {
    failedStage = stage
  } finally {
    if (cid !== null) cleaned = await page.evaluate(async ({ base, cid }) => (await fetch(`${base}/api/conversations/${cid}/stop`, { method: 'POST', signal: AbortSignal.timeout(5000), headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).ok, { base, cid }).catch(() => false)
    await page.goto('about:blank').catch(() => undefined)
    const report = testInfo.outputPath('replacements-observation.json')
    await writeFile(report, JSON.stringify({ ...observation, failed_stage: failedStage, cleanup_passed: cleaned }))
    await testInfo.attach('真实多片段编辑', { path: report, contentType: 'application/json' })
  }
  if (failedStage || !cleaned) throw new Error(`真实多片段验证失败：${failedStage ?? '清理'}`)
})
