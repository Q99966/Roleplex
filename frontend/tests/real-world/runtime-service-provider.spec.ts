import { expect, test } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'
import { isExpectedHeadingEdit } from '../controlled-page'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

/** 生成完成或明确图预算停止都可进入独立产物验收；错误不能被视作完成。
 * @param item 服务器消息及可选系统摘要。
 */
function isFinishedReply(item: { sender_type: string; status: string; stop_reason?: string; parts_json?: Array<{ type: string; stop_reason?: string }> }) {
  return item.sender_type === 'role' && (item.status === 'done' || (item.status === 'stopped'
    && (item.stop_reason === 'graph_budget' || item.parts_json?.some((part) => part.type === 'execution_summary' && part.stop_reason === 'graph_budget'))))
}


test('完整工具集两轮开发：页面创建、回答后服务存续、局部修改与正常回收', async ({ page }, testInfo) => {
  test.setTimeout(300_000)
  const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base = process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'default', 'runtime-service')
  await mkdir(root)
  const reservation = createServer()
  await new Promise<void>((resolve) => reservation.listen(0, '127.0.0.1', resolve))
  const port = (reservation.address() as { port: number }).port
  await new Promise<void>((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()))
  const proof = 'SERVICE-' + randomBytes(12).toString('hex')
  await writeFile(path.join(root, 'proof.txt'), proof, { flag: 'wx', mode: 0o600 })
  await writeFile(path.join(root, 'package.json'), JSON.stringify({ private: true, scripts: { dev: 'node server.mjs' } }))
  await writeFile(path.join(root, 'npm-user.cfg'), '')
  await writeFile(path.join(root, 'npm-global.cfg'), '')
  await writeFile(path.join(root, 'server.mjs'), [
    "import http from 'node:http'; import fs from 'node:fs';",
    "const proof = fs.readFileSync('proof.txt','utf8');",
    "const port = Number(process.argv[process.argv.indexOf('--port') + 1]);",
    "const server = http.createServer((_req,res) => { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}); res.end(fs.readFileSync('index.html')); });",
    "server.listen(port,'127.0.0.1',()=>console.log('verification='+proof));",
  ].join('\n'))
  const script = `npm --userconfig ./npm-user.cfg --globalconfig ./npm-global.cfg --cache ./.npm-cache run dev -- --port ${port}`
  const checkScript = `curl --fail --silent --show-error --max-time 5 http://127.0.0.1:${port}/`
  let stage = '登录与准备'
  let cid: number | null = null
  let failedStage: string | null = null
  let normalCleanup = false
  let toolPaths: string[][] = []
  let nativeDiffObserved = false
  let editObservation = { attempted: 0, succeeded: 0, diff_verified: false }
  let readManyObservation = { attempted: 0, succeeded: 0, detail_verified: false }
  let mutationItemsObservation = { attempted: 0, succeeded: 0, detail_verified: false }
  let discoveryObservation = { attempted: 0, succeeded: 0, detail_verified: false, matched_initial_service: false }
  let denialObservation = { observed: 0, detail_verified: false }
  const title = `真实后台服务 ${stamp}`
  const preview = await page.context().newPage()
  await preview.route('**/*', (route) => new URL(route.request().url()).origin === `http://127.0.0.1:${port}` ? route.continue() : route.abort())
  /** 仅用当前 Owner 会话访问本轮资源；Token 不传回测试进程。
   * @param route 本轮 API 路径。
   * @param method 读取或显式停止。
   */
  async function api(route: string, method = 'GET') {
    return page.evaluate(async ({ base, route, method }) => {
      const response = await fetch(base + route, { method, signal: AbortSignal.timeout(5000), headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
      if (!response.ok) throw new Error('受控 API 失败')
      return response.status === 204 ? null : response.json()
    }, { base, route, method })
  }
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })
    cid = await page.evaluate(async ({ base, root, title, stamp }) => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const request = async (route: string, body?: unknown, method = 'POST') => {
        const response = await fetch(base + route, { headers, ...(body ? { method, body: JSON.stringify(body) } : {}) })
        if (!response.ok) throw new Error('Runtime real fixture failed')
        return response.json()
      }
      if (!(await request('/api/health')).world_managed) throw new Error('Expected managed world')
      const roles = await request('/api/roles')
      const seed = roles.find((role: { model_config_id: number | null; deleted_at: string | null }) => role.model_config_id && !role.deleted_at)
      const workspace = await request('/api/workspaces', { display_name: title, root_path: root, acknowledge_existing_content: true })
      await request('/api/runtime/config', { scope: 'workspace', scope_id: workspace.id, limit: 5, expected_revision: 0, services_enabled: true }, 'PUT')
      await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true, file_tools_enabled: true, basic_commands_enabled: true }, 'PATCH')
      const role = await request('/api/roles', { name: `服务助手 ${stamp}`, system_prompt: '根据用户目标和实际工具结果完成开发。尊重审批与服务占用规则，不换工具规避拒绝。不猜测文件或页面内容。',
        model_config_id: seed.model_config_id, model_name: seed.model_name, params: { max_tokens: 4096 },
        builtin_tools: ['workspace_list', 'workspace_read', 'workspace_write', 'workspace_edit', 'workspace_run_command', 'workspace_start_service', 'workspace_service_status', 'workspace_service_logs', 'workspace_stop_service', 'workspace_run_shell'] })
      return (await request('/api/conversations', { title, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
    }, { base, root, title, stamp })
    await page.reload()
    await page.getByText(title, { exact: true }).click()
    stage = '限定服务脚本审批'
    await page.getByLabel('消息输入框').fill(`先检查 proof.txt、package.json 和 server.mjs，可自行选择单次或批量读取。请创建 index.html，包含一个 h1 标题 HelloWorld，以及 id="keep" 的 p，其文本原样为 proof.txt 内容。同时创建 note.txt，内容精确为 note old（无末尾换行）。不要修改其他预置文件，不安装依赖。运行设施已准备，启动服务的 script 必须精确为：${script}；port=${port}，health_path="/"，lifetime_seconds=300。本轮只批准该服务脚本和精确的只读检查脚本 ${checkScript}，其余操作按原生工具能力自行选择。确认页面后结束本轮回复，保留服务运行。`)
    await page.getByLabel('发送消息').click()
    await expect(page.getByRole('button', { name: '批准后台服务启动', exact: true })).toBeVisible({ timeout: 90000 })
    const valid = await page.evaluate(async ({ base, cid, script, port }) => {
      const rows = await (await fetch(`${base}/api/conversations/${cid}/tool-approvals`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json()
      return rows.length === 1 && rows[0].script.trim() === script && rows[0].port === port && rows[0].health_path === '/' && rows[0].lifetime_seconds === 300
    }, { base, cid, script, port })
    if (!valid) {
      await page.getByRole('button', { name: '拒绝后台服务启动', exact: true }).click()
      throw new Error('Model request outside smoke approval')
    }
    const [, startupDecision] = await Promise.all([
      page.getByRole('button', { name: '批准后台服务启动', exact: true }).click({ timeout: 8000 }),
      page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/decision'), { timeout: 10000 }),
    ])
    if (!startupDecision.ok()) throw new Error('启动审批失败')
    stage = '真实状态、日志与回答'
    let firstFinished = false
    let firstChecks = 0
    const firstDeadline = Date.now() + 90_000
    while (Date.now() < firstDeadline) {
      for (const row of await api(`/api/conversations/${cid}/tool-approvals`)) {
        const allowed = row.tool_name === 'workspace_run_shell' && row.script.trim() === checkScript && firstChecks < 3
        const [, decision] = await Promise.all([
          page.getByRole('button', { name: `${allowed ? '批准' : '拒绝'}${row.runtime_id ? '后台服务启动' : '本次 Shell'}`, exact: true }).click({ timeout: 8000 }),
          page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/tool-approvals/${row.id}/decision`), { timeout: 10000 }),
        ])
        if (!decision.ok()) throw new Error('附加审批失败')
        if (!allowed) throw new Error('第一轮附加请求超出范围')
        firstChecks++
      }
      const history = await api(`/api/conversations/${cid}/messages`)
      if (!history.active_generation_ids.length && history.items.some((item: { sender_type: string; status: string }) => isFinishedReply(item))) {
        firstFinished = true
        break
      }
      await page.waitForTimeout(150)
    }
    if (!firstFinished) throw new Error('第一轮未完成')
    expect((await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
    const initial = await readFile(path.join(root, 'index.html'), 'utf8')
    if (await readFile(path.join(root, 'note.txt'), 'utf8') !== 'note old') throw new Error('第二个受控文件不符')
    if (!initial.includes(proof)) throw new Error('受控文件缺少保留内容')
    const firstService = (await api(`/api/conversations/${cid}/processes`)).items.find((row: { state: string }) => row.state === 'ready')
    if (!firstService) throw new Error('generation 结束后服务未存续')
    await preview.goto(`http://127.0.0.1:${port}/?checkpoint=first`)
    await expect(preview.getByRole('heading', { name: 'HelloWorld', exact: true })).toBeVisible()
    if (await preview.locator('#keep').textContent() !== proof) throw new Error('实际页面保留内容不符')
    stage = '第二轮局部修改与按实际路径审批'
    await page.getByLabel('消息输入框').fill(`现在将 index.html 的 h1 从 HelloWorld 改为 HelloWorld Updated，其余文本、结构及 #keep 内容原样保留；同时把 note.txt 精确改为 note new（无末尾换行）。按实际权限与服务占用规则操作，不擅停其他会话服务；需要重启时重新申请相同脚本、端口及 300 秒寿命。只读检查可用 ${checkScript}，除此和前轮服务脚本外不批准其他 Shell。工具按需要选择，不要求使用某个编辑工具，也不强制单文件或 items 形式。确认后结束回复并保留预览。`)
    const sentResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/conversations/${cid}/messages`))
    await page.getByLabel('发送消息').click()
    const secondUserId = (await (await sentResponse).json()).message.id
    let finished = false
    let successfulMutation = false
    let decisions = 0
    const deadline = Date.now() + 110_000
    while (Date.now() < deadline) {
      const approvals = await api(`/api/conversations/${cid}/tool-approvals`)
      for (const row of approvals) {
        const allowed = row.runtime_id ? row.script.trim() === script && row.port === port && row.health_path === '/' && row.lifetime_seconds === 300
          : row.script.trim() === checkScript
        const [, decision] = await Promise.all([
          page.getByRole('button', { name: `${allowed ? '批准' : '拒绝'}${row.runtime_id ? '后台服务启动' : '本次 Shell'}`, exact: true }).click({ timeout: 8000 }),
          page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/tool-approvals/${row.id}/decision`), { timeout: 10000 }),
        ])
        if (!decision.ok()) throw new Error('第二轮审批失败')
        if (!allowed || ++decisions > 3) throw new Error('超出本轮脚本或执行次数范围')
      }
      const history = await api(`/api/conversations/${cid}/messages`)
      if (!history.active_generation_ids.length && history.items.some((item: { id: number; sender_type: string; status: string }) => item.id > secondUserId && isFinishedReply(item))) {
        toolPaths = [history.items.filter((item: { id: number }) => item.id < secondUserId), history.items.filter((item: { id: number }) => item.id > secondUserId)]
          .map((items) => items.flatMap((item: { parts_json: Array<{ type: string; tool_name?: string }> }) => item.parts_json.filter((part) => part.type === 'tool_call').map((part) => part.tool_name ?? 'unknown')))
        const secondCalls = history.items.filter((item: { id: number }) => item.id > secondUserId)
          .flatMap((item: { parts_json: Array<{ type: string; tool_name?: string; status?: string }> }) => item.parts_json.filter((part) => part.type === 'tool_call'))
        editObservation.attempted = secondCalls.filter((part: { tool_name?: string }) => part.tool_name === 'workspace_edit').length
        editObservation.succeeded = secondCalls.filter((part: { tool_name?: string; status?: string }) => part.tool_name === 'workspace_edit' && part.status === 'success').length
        successfulMutation = secondCalls.some((part: { tool_name?: string; status?: string }) => ['workspace_write', 'workspace_edit'].includes(part.tool_name ?? '') && part.status === 'success')
        finished = true
        break
      }
      await page.waitForTimeout(150)
    }
    if (!finished) throw new Error('第二轮未完成')
    stage = '服务发现路径观察'
    discoveryObservation = await page.evaluate(async ({ base, cid, initialId }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      let attempted = 0, succeeded = 0, verified = 0, matched = false
      for (const message of history.items) {
        for (const call of message.parts_json.filter((part: { tool_name?: string }) => part.tool_name === 'workspace_service_status')) {
          const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
          if (!detail.input || Object.hasOwn(JSON.parse(detail.input.text), 'runtime_id')) continue
          attempted++
          if (call.status !== 'success') continue
          succeeded++
          const value = detail.output && !detail.output.truncated ? JSON.parse(detail.output.text) : null
          if (Array.isArray(value?.items) && typeof value.has_more === 'boolean'
            && value.items.every((item: { runtime_id: string; state: string }) => /^[a-f0-9]{32}$/.test(item.runtime_id) && typeof item.state === 'string')) {
            verified++
            matched ||= value.items.some((item: { runtime_id: string }) => item.runtime_id === initialId)
          }
        }
      }
      return { attempted, succeeded, detail_verified: succeeded > 0 && succeeded === verified, matched_initial_service: matched }
    }, { base, cid, initialId: firstService.id })
    if (discoveryObservation.succeeded && !discoveryObservation.detail_verified) throw new Error('服务发现详情不完整')
    stage = '写入拒绝诊断路径观察'
    denialObservation = await page.evaluate(async ({ base, cid }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      let observed = 0, verified = 0
      for (const message of history.items) {
        for (const call of message.parts_json.filter((part: { tool_name?: string }) => ['workspace_write', 'workspace_edit'].includes(part.tool_name ?? ''))) {
          const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
          const values = [detail.diagnostic, ...(detail.write_batch?.items.map((item: { diagnostic?: unknown }) => item.diagnostic) ?? [])].filter(Boolean)
          for (const value of values) {
            observed++
            if (value.version === 1 && value.executed === false && typeof value.reason === 'string'
              && Array.isArray(value.next_steps) && value.next_steps.length > 0) verified++
          }
        }
      }
      return { observed, detail_verified: observed > 0 && observed === verified }
    }, { base, cid })
    if (denialObservation.observed && !denialObservation.detail_verified) throw new Error('写入拒绝诊断详情不完整')
    stage = '批量读取路径观察'
    readManyObservation = await page.evaluate(async ({ base, cid }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      let attempted = 0, succeeded = 0, verified = 0
      for (const message of history.items) {
        for (const call of message.parts_json.filter((part: { tool_name?: string }) => part.tool_name === 'workspace_read')) {
          const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
          if (!('read_batch' in detail)) continue
          attempted++
          if (call.status !== 'success') continue
          succeeded++
          const value = detail.read_batch
          if (value?.version === 1 && value.status === 'success' && value.items.length >= 1 && value.items.length <= 8
            && value.items.every((item: { id: string; result?: { sha256: string } }, index: number) => item.id === `item-${index}`
              && /^[a-f0-9]{64}$/.test(item.result?.sha256 ?? ''))) verified++
        }
      }
      return { attempted, succeeded, detail_verified: succeeded > 0 && verified === succeeded }
    }, { base, cid })
    if (readManyObservation.succeeded && !readManyObservation.detail_verified) throw new Error('已触发批量读取但详情不完整')
    const updated = await readFile(path.join(root, 'index.html'), 'utf8')
    if (await readFile(path.join(root, 'note.txt'), 'utf8') !== 'note new') throw new Error('第二个受控文件修改不符')
    stage = '独立文件与第二轮页面核对'
    if (!isExpectedHeadingEdit(initial, updated)) throw new Error('修改或保留内容不符')
    await preview.goto(`http://127.0.0.1:${port}/?checkpoint=second`)
    await expect(preview.getByRole('heading', { name: 'HelloWorld Updated', exact: true })).toBeVisible()
    if (await preview.locator('#keep').textContent() !== proof) throw new Error('实际页面未保留内容')
    const instances = (await api(`/api/conversations/${cid}/processes`)).items
    if (successfulMutation && (instances.find((row: { id: string }) => row.id === firstService.id)?.state !== 'stopped'
      || !instances.some((row: { id: string; state: string }) => row.id !== firstService.id && row.state === 'ready'))) throw new Error('原生编辑停服重启未闭环')
    if (successfulMutation) {
      stage = '第二轮原生差异核对'
      const diffObservation = await page.evaluate(async ({ base, cid, secondUserId }) => {
        const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
        const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
        let observed = false, editVerified = false
        for (const message of history.items.filter((item: { id: number }) => item.id > secondUserId)) {
          for (const call of message.parts_json.filter((part: { type: string; tool_name?: string; status?: string }) => part.type === 'tool_call' && ['workspace_write', 'workspace_edit'].includes(part.tool_name ?? '') && part.status === 'success')) {
            const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
            const changes = detail.write_batch ? detail.write_batch.items.map((node: { write: unknown }) => node.write) : [detail.write]
            if (changes.some((write: any) => write?.availability === 'recorded' && write.files.some((file: any) => file.applied && file.operation === 'modified'
              && file.hunks.some((hunk: { lines: Array<{ kind: string; text: string }> }) => hunk.lines.some((line) => line.kind === 'insert' && line.text.includes('HelloWorld Updated')))))) {
              observed = true
              if (call.tool_name === 'workspace_edit') editVerified = true
            }
          }
        }
        return { observed, editVerified }
      }, { base, cid, secondUserId })
      nativeDiffObserved = diffObservation.observed
      editObservation.diff_verified = diffObservation.editVerified
      if (!nativeDiffObserved) throw new Error('已触发原生修改但差异未记录')
    }
    stage = '批量修改路径观察'
    mutationItemsObservation = await page.evaluate(async ({ base, cid }) => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
      const history = await (await fetch(`${base}/api/conversations/${cid}/messages`, { headers })).json()
      let attempted = 0, succeeded = 0, verified = 0
      for (const message of history.items) {
        for (const call of message.parts_json.filter((part: { tool_name?: string }) => ['workspace_write', 'workspace_edit'].includes(part.tool_name ?? ''))) {
          const detail = await (await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${call.call_id}`, { headers })).json()
          if (!('write_batch' in detail)) continue
          attempted++
          if (call.status !== 'success') continue
          succeeded++
          const value = detail.write_batch
          if (value?.status === 'success' && value.items.length >= 1 && value.items.length <= 8 && value.items.every((node: any, index: number) =>
            node.id === `item-${index}` && node.applied && node.result?.sha256 && ['recorded', 'partial'].includes(node.write?.availability))) verified++
        }
      }
      return { attempted, succeeded, detail_verified: succeeded > 0 && succeeded === verified }
    }, { base, cid })
    if (mutationItemsObservation.succeeded && !mutationItemsObservation.detail_verified) throw new Error('批量修改详情未确认')
    expect((await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
    await page.reload()
    await page.getByLabel('消息输入框').fill('/ps')
    await page.getByLabel('消息输入框').press('Enter')
    const panel = page.getByRole('dialog', { name: '会话进程与详情' })
    await expect(panel.getByText('就绪', { exact: true })).toBeVisible()
    stage = '停止及日志隔离'
    await panel.getByRole('button', { name: /^停止 / }).first().click()
    await expect(panel.getByText('就绪', { exact: true })).toHaveCount(0)
    const serialized = JSON.stringify(await readRunEvents())
    expect([proof, script, checkScript, root].every((value) => !serialized.includes(value))).toBe(true)
  } catch {
    failedStage = stage
  } finally {
    try {
      if (cid !== null) {
        await api(`/api/conversations/${cid}/stop`, 'POST')
        const rows = (await api(`/api/conversations/${cid}/processes`)).items
        for (const row of rows) if (!['stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted'].includes(row.state)) {
          const result = await api(`/api/conversations/${cid}/processes/${row.id}/stop`, 'POST')
          if (!['stopped', 'expired', 'rejected'].includes(result.state)) throw new Error('正常回收未确认')
        }
        const remaining = (await api(`/api/conversations/${cid}/processes`)).items
        if (remaining.some((row: { state: string }) => !['stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted'].includes(row.state))) throw new Error('本轮资源未收口')
        await expect.poll(() => page.request.get(`http://127.0.0.1:${port}`, { timeout: 1000 }).then(() => false).catch(() => true)).toBe(true)
      }
      normalCleanup = true
    } catch { normalCleanup = false }
    await preview.close().catch(() => undefined)
    await page.goto('about:blank').catch(() => undefined)
    const reportPath = testInfo.outputPath('tool-path-observation.json')
    await writeFile(reportPath, JSON.stringify({ functional_passed: failedStage === null, failed_stage: failedStage,
      normal_cleanup_passed: normalCleanup, turn_tools: toolPaths, workspace_edit: editObservation, workspace_read_items: readManyObservation,
      workspace_mutation_items: mutationItemsObservation,
      service_discovery_lists: discoveryObservation,
      write_denial_diagnostics: denialObservation,
      native_diff: nativeDiffObserved ? 'verified' : 'not_observed',
      emergency_cleanup: 'not_performed_by_case; wrapper teardown is separate' }))
    await testInfo.attach('两轮工具路径观察', { path: reportPath, contentType: 'application/json' })
  }
  if (failedStage || !normalCleanup) throw new Error(`真实两轮验收失败：${failedStage ?? '正常回收'}；正常回收=${normalCleanup}`)
})
