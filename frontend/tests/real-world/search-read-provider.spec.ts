import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import path from 'node:path'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 Provider 搜索未知行号并按版本读取大文件的小范围', async ({ page }, testInfo) => {
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', 'search-read')
  await mkdir(root)
  const proof = 'T2-' + randomBytes(12).toString('hex')
  const name = `source-${randomBytes(4).toString('hex')}.txt`
  const count = 20000 + randomBytes(2).readUInt16BE(0) % 1000
  await writeFile(path.join(root, name), ('padding-' + 'x'.repeat(55) + '\n').repeat(count)
    + 'FIND_THIS_FUNCTION CHECKPOINT\n' + 'padding\n'.repeat(20) + `verification=${proof}\nend\n`, { flag: 'wx', mode: 0o600 })
  let stage = '准备', cid: number | null = null
  let failedStage: string | null = null
  let cleaned = true
  let observation: Record<string, unknown> = {}
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })
    cid = await page.evaluate(async ({ base, root, stamp }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const request = async (route: string, body?: unknown, method = 'POST') => {
        const response = await fetch(base + route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
        if (!response.ok) throw new Error('T2 real fixture failed')
        return response.json()
      }
      const seed = (await request('/api/roles')).find((role: { model_config_id: number | null }) => role.model_config_id)
      const workspace = await request('/api/workspaces', { display_name: 'T2 真实搜索', root_path: root, acknowledge_existing_content: true })
      await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
      const role = await request('/api/roles', { name: `T2 真实助手 ${stamp}`, model_config_id: seed.model_config_id,
        model_name: seed.model_name, system_prompt: '按实际工具结果执行，不能猜行号、hash 或校验值。', params: { max_tokens: 1024 },
        builtin_tools: ['workspace_search', 'workspace_read'] })
      return (await request('/api/conversations', { title: `T2 搜索验证 ${stamp}`, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id
    }, { base, root, stamp })
    await page.reload()
    await page.getByText(`T2 搜索验证 ${stamp}`, { exact: true }).click()
    stage = '搜索定位与范围读取'
    await page.getByLabel('消息输入框').fill('先用 workspace_search 的 queries=["FIND_THIS_FUNCTION","CHECKPOINT"] 和 match="all" 一次搜索同一行包含两个词的位置，找到路径和行号后，用 workspace_read 行模式读取从该行到之后30行，将搜索结果的 sha256 作为 expected_sha256 校验版本。其中有 verification= 后面的校验值，最后只回复该值。不要读取整文件，不使用字节模式，不猜路径、行号或校验值。')
    await page.getByLabel('发送消息').click()
    const reply = page.getByTestId('chat-message').nth(1)
    await expect(reply).toBeVisible()
    await expect(reply.getByText('生成中…', { exact: true })).toBeHidden({ timeout: 120000 })
    expect(await reply.evaluate((element, proof) => element.textContent?.includes(proof) === true, proof)).toBe(true)
    stage = '真实工具详情与版本核对'
    observation = await page.evaluate(async ({ base, cid, proof, count }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      const message = history.items.find((item: { sender_type: string }) => item.sender_type === 'role')
      let searches = 0, ranges = 0, verified = false, matchedHash = false, scanLarge = false, multiQuery = false
      let hash: string | null = null
      for (const call of message.parts_json.filter((part: { type: string }) => part.type === 'tool_call')) {
        const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
        if (detail.search?.matches?.length) {
          searches++; hash = detail.search.matches[0].sha256
          const input = JSON.parse(detail.input.text)
          multiQuery ||= input.queries?.length === 2 && input.match === 'all' && detail.search.matches[0].matched_queries?.length === 2
        }
        const values = detail.read_range ? [detail.read_range] : (detail.read_batch?.items.map((item: { result?: unknown }) => item.result) ?? [])
        for (const value of values) if (value?.mode === 'lines') {
          ranges++
          verified ||= value.start_line === count + 1 && value.text.includes(proof) && value.bytes < 65536
          matchedHash ||= value.sha256 === hash
          scanLarge ||= value.scanned_bytes > 1024 * 1024
        }
      }
      return { searches, ranges, verified, matched_hash: matchedHash, scanned_large_file: scanLarge, multi_query: multiQuery }
    }, { base, cid, proof, count })
    expect(observation.multi_query === true && observation.verified === true && observation.matched_hash === true && observation.scanned_large_file === true).toBe(true)
  } catch {
    failedStage = stage
  } finally {
    if (cid !== null) {
      cleaned = await page.evaluate(async ({ base, cid }) => {
        return (await fetch(`${base}/api/conversations/${cid}/stop`, { method: 'POST', signal: AbortSignal.timeout(5000),
          headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).ok
      }, { base, cid }).catch(() => false)
    }
    await page.goto('about:blank').catch(() => undefined)
    const report = testInfo.outputPath('search-read-observation.json')
    await writeFile(report, JSON.stringify({ ...observation, failed_stage: failedStage, normal_cleanup_passed: cleaned }))
    await testInfo.attach('T2 真实工具路径', { path: report, contentType: 'application/json' })
  }
  if (failedStage || !cleaned) throw new Error(`真实 T2 验证失败：${failedStage ?? '正常清理'}`)
})
