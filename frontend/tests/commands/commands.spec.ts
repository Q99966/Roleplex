import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import { readRunEvents, waitForRunEvents } from '../e2e-log-assertions'

/** 确認受控父子进程不再运行；Linux 的待收割 zombie 不算活进程。
 * @param pid 本轮测试 fixture 写出的进程身份。
 */
async function processStopped(pid: number): Promise<boolean> {
  if (process.platform === 'linux') {
    try { return (await readFile(`/proc/${pid}/stat`, 'utf-8')).split(') ')[1].startsWith('Z') }
    catch { return true }
  }
  try { process.kill(pid, 0); return false } catch { return true }
}

test('工作区命令的输出、失败、超时与停止均有真实工具卡', async ({ page }) => {
  await ensureOwnerSession(page)
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  await page.getByRole('tab', { name: '工作区', exact: true }).click()
  await page.getByLabel('显示名称').fill('命令验收工作区')
  await page.getByLabel('工作区绝对路径').fill(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!)
  await page.getByLabel(/我确认该目录的文件内容可能/).check()
  await page.getByRole('button', { name: '添加工作区', exact: true }).click()
  await page.getByRole('button', { name: /结构化命令 · 关闭/ }).click()
  await expect(page.getByRole('button', { name: /结构化命令 · 已开启/ })).toBeVisible()
  await page.getByRole('button', { name: '关闭系统与环境设置' }).click()
  await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const config = await (await fetch('/api/model-configs', { method: 'POST', headers, body: JSON.stringify({
      name: '命令模型', provider_type: 'openai_compatible', api_key: 'sk-placeholder',
    }) })).json()
    await fetch('/api/roles', { method: 'POST', headers, body: JSON.stringify({
      name: '命令助手', system_prompt: '使用结构化命令。', model_config_id: config.id,
      model_name: 'fake-model', builtin_tools: ['workspace_run_command'],
    }) })
  })
  await page.reload()
  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByLabel('会话名称 (必填)').fill('W1b 命令验收')
  await page.getByRole('button', { name: /命令助手/ }).click()
  const select = page.getByLabel('会话工作区')
  const value = await select.locator('option').filter({ hasText: '命令验收工作区' }).getAttribute('value')
  await select.selectOption(value!)
  await page.getByRole('button', { name: '确认开启会话' }).click()
  for (const [marker, expected] of [
    ['[W1B_FAKE_E2E]', 'W1b 结构化命令流程结束。'],
    ['[W1B_OUTPUT]', '输出已截断'], ['[W1B_EXIT]', '退出码 7'],
    ['[W1B_TIMEOUT]', '命令超时，进程已回收'], ['[W1B_DENIED]', 'WORKSPACE_PATH_INVALID'],
  ]) {
    await page.getByLabel('消息输入框').fill(marker)
    await page.getByLabel('发送消息').click()
    await expect(page.getByText(expected, { exact: true }).last()).toBeVisible({ timeout: 20_000 })
    await expect(page.getByLabel('发送消息')).toBeVisible({ timeout: 20_000 })
  }
  await page.getByLabel('消息输入框').fill('[W1B_CANCEL]')
  await page.getByLabel('发送消息').click()
  const pidFile = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'cancel-pids.txt')
  await expect.poll(async () => readFile(pidFile, 'utf-8').then(() => true).catch(() => false)).toBe(true)
  await page.getByRole('button', { name: '停止生成', exact: true }).click()
  await expect(page.getByText('已取消', { exact: true })).toBeVisible()
  for (const profile of ['cancel', 'timeout']) {
    const pids = (await readFile(path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, `${profile}-pids.txt`), 'utf-8'))
      .split(',').map(Number)
    await expect.poll(async () => (await Promise.all(pids.map(processStopped))).every(Boolean)).toBe(true)
  }
  await page.reload()
  await expect(page.getByText('已取消', { exact: true })).toBeVisible()
  await expect(page.getByText('命令超时，进程已回收', { exact: true })).toBeVisible()
  const completed = await waitForRunEvents((event) => event.event === 'tool.call_completed', 9)
  expect(completed.map((event) => event.status)).toEqual([
    'success', 'success', 'success', 'success', 'success', 'failed', 'timeout', 'rejected', 'cancelled',
  ])
  const events = await readRunEvents()
  const starts = events.filter((event) => event.event === 'tool.call_started')
  expect(starts.map((event) => event.tool_call_id)).toEqual(completed.map((event) => event.tool_call_id))
  const serialized = JSON.stringify(events)
  for (const forbidden of [process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, '受控测试文本', '测试输出', '受控诊断']) {
    expect(serialized).not.toContain(forbidden)
  }
  await page.screenshot({ path: 'test-results/commands-completed.png', fullPage: true })
})
