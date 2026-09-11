import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import { randomBytes } from 'node:crypto'
import path from 'node:path'
import { readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实模型启动 npm HelloWorld 服务，查询状态日志，回答后仍存活并可停止', async ({ page }) => {
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
    "const server = http.createServer((_req,res) => { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); res.end('<h1>HelloWorld</h1><p>'+proof+'</p>'); });",
    "server.listen(port,'127.0.0.1',()=>console.log('verification='+proof));",
  ].join('\n'))
  const script = `npm --userconfig ./npm-user.cfg --globalconfig ./npm-global.cfg --cache ./.npm-cache run dev -- --port ${port}`
  let stage = '登录与准备'
  const title = `真实后台服务 ${stamp}`
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20000 })
    const cid = await page.evaluate(async ({ base, root, title, stamp }) => {
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
      await request(`/api/workspaces/${workspace.id}`, { shell_enabled: true }, 'PATCH')
      const role = await request('/api/roles', { name: `服务助手 ${stamp}`, system_prompt: '精确执行用户限定的服务启动脚本。启动完成后必须使用服务状态和日志工具，依据真实日志回答验证值。不猜测，不使用其他脚本或端口。',
        model_config_id: seed.model_config_id, model_name: seed.model_name, params: { max_tokens: 2048 },
        builtin_tools: ['workspace_start_service', 'workspace_service_status', 'workspace_service_logs', 'workspace_stop_service', 'workspace_run_shell'] })
      return (await request('/api/conversations', { title, type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
    }, { base, root, title, stamp })
    await page.reload()
    await page.getByText(title, { exact: true }).click()
    stage = '限定服务脚本审批'
    await page.getByLabel('消息输入框').fill(`预置程序已准备，不需要安装依赖。调用 workspace_start_service，script 精确为：${script}；port=${port}，health_path="/"，lifetime_seconds=60。等待 Owner 批准后，用返回的 runtime_id 查询状态和日志。最终只回复日志中的 verification 值，保留服务运行，不使用其他命令。`)
    await page.getByLabel('发送消息').click()
    await expect(page.getByRole('button', { name: '批准后台服务启动', exact: true })).toBeVisible({ timeout: 90000 })
    const valid = await page.evaluate(async ({ base, cid, script, port }) => {
      const rows = await (await fetch(`${base}/api/conversations/${cid}/tool-approvals`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json()
      return rows.length === 1 && rows[0].script.trim() === script && rows[0].port === port && rows[0].health_path === '/' && rows[0].lifetime_seconds === 60
    }, { base, cid, script, port })
    if (!valid) {
      await page.getByRole('button', { name: '拒绝后台服务启动', exact: true }).click()
      throw new Error('Model request outside smoke approval')
    }
    await page.getByRole('button', { name: '批准后台服务启动', exact: true }).click()
    stage = '真实状态、日志与回答'
    await expect.poll(() => page.evaluate(async (proof) => {
      const state = (await import('/src/store/chat.ts')).useChatStore.getState()
      return !state.generating && state.messages.some((item) => item.sender_type === 'role' && item.status === 'done' && item.parts_json.some((part) => part.type === 'text' && part.text?.includes(proof)))
    }, proof), { timeout: 90000 }).toBe(true)
    expect((await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
    stage = '服务存活期间的真实 Shell 审批与 HTTP 检查'
    const checkScript = `curl --fail --silent --show-error --max-time 5 http://127.0.0.1:${port}/`
    await page.getByLabel('消息输入框').fill(`保留刚才的服务。现在只调用 workspace_run_shell，script 精确为：${checkScript}。等待 Owner 批准后执行。成功后简短确认 HTTP 检查完成，不要停止服务，不要启动其他服务。`)
    await page.getByLabel('发送消息').click()
    await expect(page.getByRole('button', { name: '批准本次 Shell', exact: true })).toBeVisible({ timeout: 90000 })
    const checkValid = await page.evaluate(async ({ base, cid, checkScript }) => {
      const rows = await (await fetch(`${base}/api/conversations/${cid}/tool-approvals`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` },
      })).json()
      return rows.length === 1 && rows[0].tool_name === 'workspace_run_shell' && rows[0].script.trim() === checkScript && rows[0].active_service_count === 1
    }, { base, cid, checkScript })
    if (!checkValid) {
      await page.getByRole('button', { name: '拒绝本次 Shell', exact: true }).click()
      throw new Error('Model Shell outside smoke approval')
    }
    await expect(page.getByRole('note')).toContainText('该工作区有 1 个未结束的服务实例')
    await page.getByRole('button', { name: '批准本次 Shell', exact: true }).click()
    await expect.poll(() => page.evaluate(async () => {
      const state = (await import('/src/store/chat.ts')).useChatStore.getState()
      return !state.generating && state.messages.some((message) => message.parts_json.some((part) =>
        part.type === 'tool_call' && part.tool_name === 'workspace_run_shell' && part.status === 'success' && part.exit_code === 0))
    }), { timeout: 90000 }).toBe(true)
    expect((await page.request.get(`http://127.0.0.1:${port}`)).status()).toBe(200)
    await page.reload()
    await page.getByLabel('消息输入框').fill('/ps')
    await page.getByLabel('消息输入框').press('Enter')
    const panel = page.getByRole('dialog', { name: '会话进程与详情' })
    await expect(panel.getByText('就绪', { exact: true })).toBeVisible()
    await panel.getByRole('article').filter({ hasText: 'workspace_start_service' }).getByRole('button', { name: /^查看日志 / }).click()
    expect(await panel.getByRole('region', { name: '服务日志' }).evaluate((node, proof) => node.textContent?.includes(proof), proof)).toBe(true)
    stage = '停止及日志隔离'
    await panel.getByRole('button', { name: /^停止 / }).first().click()
    await expect(panel.getByText('已停止', { exact: true })).toBeVisible()
    const tools = await waitForRunEvents((event) => event.event === 'tool.call_completed' && event.conversation_id === cid, 3)
    expect(['workspace_start_service', 'workspace_service_status', 'workspace_service_logs'].every((name) => tools.some((event) => event.tool_name === name))).toBe(true)
    const serialized = JSON.stringify(await readRunEvents())
    expect([proof, script, checkScript, root].every((value) => !serialized.includes(value))).toBe(true)
  } catch {
    await page.goto('about:blank').catch(() => undefined)
    throw new Error(`真实后台服务验收失败：${stage}`)
  }
})
