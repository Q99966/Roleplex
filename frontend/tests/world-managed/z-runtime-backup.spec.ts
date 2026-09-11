import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

/** 只分配测试端口，不接管或停止其他宿主资源。 */
async function availablePort() {
  const server = createServer()
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as { port: number }).port
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  return port
}

test('World 备份回收实际服务，下载后可重新审批启动，切换前再次回收', async ({ page }, testInfo) => {
  await ensureOwnerSession(page)
  const health = await (await page.request.get('/api/health')).json()
  const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, health.world_name, 'runtime-backup')
  await mkdir(root)
  await writeFile(path.join(root, 'index.html'), '<h1>World backup fixture</h1>')
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('Runtime world fixture failed')
      return response.json()
    }
    const config = await post('/api/model-configs', { name: '备份测试模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await post('/api/roles', { name: '备份服务助手', system_prompt: '只启动受控服务。',
      model_config_id: config.id, model_name: 'fake-model', builtin_tools: ['workspace_start_service'] })
    const workspace = await post('/api/workspaces', { display_name: '备份服务工作区', root_path: root, acknowledge_existing_content: true })
    await post('/api/runtime/config', { scope: 'workspace', scope_id: workspace.id, limit: 5, expected_revision: 0, services_enabled: true }, 'PUT')
    return (await post('/api/conversations', { title: 'World 服务备份', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id
  }, root)
  await page.reload()
  await page.getByText('World 服务备份', { exact: true }).click()
  const port = await availablePort()
  for (const action of ['backup', 'switch']) {
    await page.getByLabel('消息输入框').fill(`[SERVICE_FAKE:${port}]`)
    await page.getByLabel('发送消息').click()
    await page.getByRole('button', { name: '批准后台服务启动', exact: true }).click()
    await expect.poll(async () => page.request.get(`http://127.0.0.1:${port}`, { timeout: 500 }).then((result) => result.status()).catch(() => 0)).toBe(200)
    await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
    if (action === 'backup') {
      page.once('dialog', (dialog) => void dialog.accept())
      const download = page.waitForEvent('download')
      await page.getByRole('button', { name: '回收进程并备份当前世界', exact: true }).click()
      const file = await download
      expect(file.suggestedFilename()).toBe(`${health.world_name}-backup.zip`)
      expect(await file.failure()).toBeNull()
      // 不将含世界密钥的 ZIP 附加到报告或保留到仓库；Playwright 关闭上下文时清理下载。
      await expect(page.getByRole('status')).toContainText('备份已生成')
      const screenshot = testInfo.outputPath('world-backup.png')
      await page.screenshot({ path: screenshot })
      await testInfo.attach('世界协调备份', { path: screenshot, contentType: 'image/png' })
      await page.getByRole('button', { name: '关闭系统与环境设置' }).click()
      const states = await page.evaluate(async (cid) => (await (await fetch(`/api/conversations/${cid}/processes`,
        { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })).json()).items.map((row: { state: string }) => row.state), cid)
      expect(states).toEqual(['stopped'])
    } else {
      page.once('dialog', (dialog) => void dialog.accept())
      await page.getByLabel('切换世界').selectOption(health.world_name === 'alpha' ? 'beta' : 'alpha')
      await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30000 })
    }
    await expect.poll(async () => page.request.get(`http://127.0.0.1:${port}`, { timeout: 500 }).then(() => false).catch(() => true)).toBe(true)
  }
})
