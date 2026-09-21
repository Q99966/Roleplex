import { test, expect } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { orchestrationFixture, orchestrationSnapshot } from '../orchestration-fixture'

test.use({ screenshot: 'off', trace: 'off', video: 'off' })

test('真实 World 群协调者规划、并行审查与两轮条件返工', async ({ page }) => {
  test.setTimeout(300_000)
  page.setDefaultTimeout(15_000)
  let stage = '登录'
  try {
    const stamp = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
    const root = path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!, process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!, 'orchestration')
    await mkdir(root, { recursive: true })
    await page.setViewportSize({ width: 1600, height: 1000 })
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码', { exact: true }).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`, { exact: true })).toBeVisible({ timeout: 20_000 })
    stage = '准备隔离群与流程'
    const { cid, coordinator } = await orchestrationFixture(page, root, true)
    await page.reload()
    await page.getByRole('button', { name: '打开会话：并行返工协调验收', exact: true }).click()
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name: '会话成员', exact: true }).locator('span').last().click()
    await page.getByLabel('任命群协调者').selectOption(String(coordinator))
    await expect(page.getByLabel('任命群协调者')).toBeEnabled()
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
    await page.getByRole('button', { name: /并行返工 v1/ }).click()
    stage = '真实协调执行'
    await page.getByLabel('本次运行补充要求').fill('保持已有流程结构、任务、角色、工具和循环配置不变；读图检查后启动当前流程。')
    await page.getByRole('button', { name: '协调执行', exact: true }).click()
    await expect.poll(async () => {
      const run = (await orchestrationSnapshot(page, cid)).runs[0]
      if (run && ['failed','blocked','stopped','interrupted'].includes(run.status)) throw new Error(`协调执行终态 ${run.status} ${run.error_code ?? ''}`)
      return run?.status
    }, { timeout: 240_000 }).toBe('completed')
    stage = '核对工具文件与两轮执行'
    expect((await readFile(path.join(root, 'workflow-round.txt'), 'utf8')) === 'round-2').toBe(true)
    const snapshot = await orchestrationSnapshot(page, cid)
    const run = snapshot.runs[0]
    expect(run.loop_states.revision.iteration).toBe(1)
    expect(run.loop_states.revision.exited).toBe(true)
    const reviews = run.attempts.filter((a: { node_id: string }) => ['a','b'].includes(a.node_id))
    expect(reviews.length).toBe(4)
    expect(reviews.every((a: { result: { values: { observed: string; approved: boolean } }; iteration: number }) =>
      a.result.values.observed === `round-${a.iteration+1}` && a.result.values.approved === (a.iteration===1))).toBe(true)
    expect(run.attempts.filter((a: { execution_id: string | null }) => a.execution_id).every((a: { usage: { output_tokens: number | null } }) => (a.usage.output_tokens ?? 0) > 0)).toBe(true)
    expect(snapshot.coordinations.some((c: { mode: string; execution_id: string | null }) => c.mode==='execute' && Boolean(c.execution_id))).toBe(true)
    expect(run.attempts.some((a: { phase: string }) => a.phase==='summary')).toBe(true)
    stage = '刷新持久历史'
    await page.reload()
    await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
    await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
    await page.getByLabel('选择工作流运行').selectOption(run.id)
    await expect(page.getByText(/本次执行结束 · 定义快照/)).toBeVisible()
  } catch {
    await page.goto('about:blank')
    throw new Error(`真实协调验收未通过，阶段：${stage}；请按保留 World 的运行状态检查。`)
  }
})
