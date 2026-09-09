import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'
import {
  expectManagedWorldLayout,
  expectStableTwoTurnContext,
  readRunEvents,
  waitForRunEvents,
} from '../e2e-log-assertions'

const backend = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8003'
const workspaceRoot = process.env.ROLEPLEX_E2E_WORKSPACE_ROOT ?? '/home/chen/workspace/testworkspace'
const workspaceRelativeRoot = process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT ?? 'missing'

/** 通过设置中心登记并开启当前 World 的文件与命令能力。
 * @param page 当前浏览器页面。
 * @param world 本轮测试 World。
 */
async function registerWorkspace(page: Page, world: 'alpha' | 'beta'): Promise<string> {
  const displayName = `${world.toUpperCase()} E2E 工作区`
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  await page.getByRole('tab', { name: '工作区', exact: true }).click()
  await page.getByLabel('显示名称').fill(displayName)
  await page.getByLabel('工作区绝对路径').fill(path.join(workspaceRoot, workspaceRelativeRoot, world))
  await page.getByLabel(/我确认该目录的文件内容可能/).check()
  await page.getByRole('button', { name: '添加工作区', exact: true }).click()
  await expect(page.getByRole('heading', { name: displayName })).toBeVisible()
  await page.getByRole('button', { name: /原生文件读写 · 关闭/ }).click()
  await expect(page.getByRole('button', { name: /原生文件读写 · 已开启/ })).toBeVisible()
  await page.getByRole('button', { name: /结构化命令 · 关闭/ }).click()
  await expect(page.getByRole('button', { name: /结构化命令 · 已开启/ })).toBeVisible()
  await page.getByRole('button', { name: '关闭系统与环境设置' }).click()
  return displayName
}

/** 创建启用文件与命令的角色，并验证绑定 single 会话。
 * @param page 当前浏览器页面。
 * @param world 本轮测试 World。
 * @param workspaceName 通过设置页登记的工作区名称。
 */
async function runWorkspaceFlow(page: Page, world: 'alpha' | 'beta', workspaceName: string): Promise<void> {
  const roleName = await page.evaluate(async ({ base, suffix }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (route: string, body: unknown) => {
      const response = await fetch(`${base}${route}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${route} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `w1a-${suffix}`, provider_type: 'openai_compatible', api_key: 'sk-e2e-placeholder',
    })
    const role = await post('/api/roles', {
      name: `W1a 文件助手 ${suffix}`,
      system_prompt: '按用户要求使用当前 execution 绑定的工作区工具。',
      model_config_id: config.id,
      model_name: 'fake-model',
      builtin_tools: ['workspace_list', 'workspace_read', 'workspace_write', 'workspace_run_command'],
    })
    return role.name as string
  }, { base: backend, suffix: `${world}-${Date.now()}` })

  await page.reload()
  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByLabel('会话名称 (必填)').fill(`${world.toUpperCase()} W1a 工具闭环`)
  await page.getByRole('button', { name: new RegExp(roleName) }).click()
  const workspaceSelect = page.getByLabel('会话工作区')
  const workspaceValue = await workspaceSelect.locator('option').filter({ hasText: workspaceName }).getAttribute('value')
  expect(workspaceValue).not.toBeNull()
  await workspaceSelect.selectOption(workspaceValue!)
  await page.getByRole('button', { name: '确认开启会话' }).click()
  await page.getByLabel('消息输入框').fill('[W1A_FAKE_E2E] 完成 hello.txt 写读更新验收')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('W1a 工作区工具闭环完成。')).toBeVisible({ timeout: 30_000 })
  for (const toolName of ['workspace_list', 'workspace_write', 'workspace_read']) {
    await expect(page.getByText(toolName).first()).toBeVisible()
  }
  const content = await readFile(path.join(workspaceRoot, workspaceRelativeRoot, world, 'hello.txt'), 'utf-8')
  expect(content).toBe('W1a 第二版')
  await page.getByLabel('消息输入框').fill('[W1B_FAKE_E2E]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('W1b 结构化命令流程结束。')).toBeVisible({ timeout: 30_000 })
  for (const command of ['pwd', 'list', 'read', 'count']) {
    await expect(page.getByText(`命令 ${command}`, { exact: true })).toBeVisible()
  }
  await expect(page.getByText('退出码 0', { exact: true })).toHaveCount(4)
  await page.getByLabel('消息输入框').fill('[W1B_DENIED]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('WORKSPACE_PATH_INVALID', { exact: true })).toBeVisible()
}

/**
 * 在当前受托管世界中创建 fake 角色和单聊会话。
 * @param page 当前浏览器页面。
 * @param title 测试会话标题。
 */
async function seedFakeConversation(page: Page, title: string): Promise<number> {
  return page.evaluate(async ({ base, conversationTitle, suffix }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (route: string, body: unknown) => {
      const response = await fetch(`${base}${route}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${route} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `world-fake-${suffix}`, provider_type: 'openai_compatible', api_key: 'sk-e2e-placeholder',
    })
    const role = await post('/api/roles', {
      name: `世界 Fake 助手 ${suffix}`,
      system_prompt: '你是世界模式下的确定性测试助手',
      model_config_id: config.id,
      model_name: 'fake-model',
    })
    const conversation = await post('/api/conversations', {
      type: 'single', title: conversationTitle, role_ids: [role.id],
    })
    return conversation.id as number
  }, { base: backend, conversationTitle: title, suffix: `${Date.now()}` })
}

/**
 * 在 alpha 世界中创建两个 fake 角色和群聊。
 * @param page 当前浏览器页面。
 * @returns 群聊 ID 和稳定角色名称顺序。
 */
async function seedFakeGroup(page: Page): Promise<{ conversationId: number; roleNames: string[] }> {
  return page.evaluate(async ({ base, suffix }) => {
    const token = localStorage.getItem('roleplex_token')
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
    const post = async (route: string, body: unknown) => {
      const response = await fetch(`${base}${route}`, { method: 'POST', headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error(`${route} failed: ${response.status}`)
      return response.json()
    }
    const config = await post('/api/model-configs', {
      name: `world-group-${suffix}`, provider_type: 'openai_compatible', api_key: 'sk-e2e-placeholder',
    })
    const roles = []
    for (const marker of ['A', 'B']) {
      roles.push(await post('/api/roles', {
        name: `世界群聊${marker}-${suffix}`,
        system_prompt: `你是世界群聊角色 ${marker}`,
        model_config_id: config.id,
        model_name: 'fake-model',
      }))
    }
    const conversation = await post('/api/conversations', {
      type: 'group', title: 'Fake 世界 M4a 验证', role_ids: roles.map((role) => role.id),
    })
    return {
      conversationId: conversation.id as number,
      roleNames: roles.map((role) => role.name as string),
    }
  }, { base: backend, suffix: `${Date.now()}` })
}

test('runs C2 and M4a in a fake physical world, then switches worlds', async ({ page }) => {
  await ensureOwnerSession(page)

  const healthBefore = await page.evaluate(async (base) => (await fetch(`${base}/api/health`)).json(), backend)
  expect(healthBefore.world_managed).toBe(true)
  expect(healthBefore.world_name).toBe('alpha')
  await expectManagedWorldLayout((process.env.ROLEPLEX_E2E_WORLDS ?? '').split(',')[0])

  const alphaWorkspace = await registerWorkspace(page, 'alpha')
  await runWorkspaceFlow(page, 'alpha', alphaWorkspace)

  const conversationId = await seedFakeConversation(page, 'Fake 世界 C2 验证')
  await page.reload()
  await page.getByText('Fake 世界 C2 验证').first().click()
  await expect(page.getByRole('heading', { name: 'Fake 世界 C2 验证' })).toBeVisible()
  await expect(page.getByLabel('消息输入框')).toBeEnabled()

  const prompts = ['Fake 世界第一轮', 'Fake 世界第二轮']
  for (const prompt of prompts) {
    const input = page.getByLabel('消息输入框')
    await input.fill(prompt)
    await expect(input).toHaveValue(prompt)
    // 提交后按钮会立即切换为“停止生成”；force 只跳过节点稳定等待，不绕过应用的 disabled 状态。
    await expect(page.getByLabel('发送消息')).toBeEnabled()
    await page.getByLabel('发送消息').click({ force: true })
    await expect(page.getByText(`已收到你的消息：${prompt}`)).toBeVisible({ timeout: 20_000 })
  }

  const contexts = await waitForRunEvents(
    (event) => event.event === 'context.loaded' && event.conversation_id === conversationId,
    2,
  )
  expectStableTwoTurnContext(contexts.slice(-2))
  const providerCalls = await waitForRunEvents(
    (event) => event.event === 'provider.call_completed' && event.conversation_id === conversationId,
    2,
  )
  for (const event of providerCalls.slice(-2)) {
    expect(event.provider_mode).toBe('fake')
    expect(event.base_url).toBe('fake://local')
    expect(event.base_url_source).toBe('fake')
    for (const field of [
      'input_tokens', 'output_tokens', 'total_tokens',
      'cache_hit_tokens', 'cache_write_tokens', 'cache_hit_ratio', 'usage_source',
    ]) expect(event[field]).toBeUndefined()
  }
  const serialized = JSON.stringify(await readRunEvents())
  for (const prompt of prompts) expect(serialized).not.toContain(prompt)

  const group = await seedFakeGroup(page)
  await page.reload()
  await page.getByText('Fake 世界 M4a 验证').first().click()
  await expect(page.getByRole('heading', { name: 'Fake 世界 M4a 验证' })).toBeVisible()
  const groupInput = page.getByLabel('消息输入框')
  await groupInput.fill('@')
  await page.getByRole('option', { name: `@${group.roleNames[0]}` }).click()
  await groupInput.fill(`${await groupInput.inputValue()}@`)
  await page.getByRole('option', { name: `@${group.roleNames[1]}` }).click()
  await groupInput.fill(`${await groupInput.inputValue()}世界群聊依次回复`)
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('chat-message')).toHaveCount(3, { timeout: 20_000 })
  const groupContexts = (await waitForRunEvents(
    (event) => event.event === 'context.loaded' && event.conversation_id === group.conversationId,
    2,
  )).slice(-2)
  expect(groupContexts.map((event) => event.context_message_count)).toEqual([0, 1])
  for (const field of ['runtime_prefix_hash', 'conversation_prefix_hash', 'tool_policy_hash']) {
    expect(groupContexts[1][field]).toBe(groupContexts[0][field])
  }
  expect(groupContexts[1].role_prefix_hash).not.toBe(groupContexts[0].role_prefix_hash)

  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  const selector = page.getByLabel('切换世界')
  await expect(selector).toBeEnabled()
  await expect(selector).toHaveValue('alpha')
  await expect(selector.locator('option')).toHaveCount(2)

  page.once('dialog', (dialog) => void dialog.accept())
  await selector.selectOption('beta')
  await expect(page.getByRole('status')).toContainText('正在进入世界“beta”')

  // 包装器重启到 beta 后，前端清除 alpha Token 并回到认证页。
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  const health = await page.evaluate(async (base) => (await fetch(`${base}/api/health`)).json(), process.env.ROLEPLEX_E2E_API_ORIGIN)
  expect(health.world_name).toBe('beta')

  // beta 是物理独立的新库；同名 Owner 需要重新创建，随后顶栏显示 beta。
  await ensureOwnerSession(page)
  await expect(page.getByText('beta', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  await page.getByRole('tab', { name: '工作区', exact: true }).click()
  await expect(page.getByText('当前 World 尚未登记工作区。')).toBeVisible()
  await page.getByRole('button', { name: '关闭系统与环境设置' }).click()
  const betaWorkspace = await registerWorkspace(page, 'beta')
  await runWorkspaceFlow(page, 'beta', betaWorkspace)
  expect(await readFile(path.join(workspaceRoot, workspaceRelativeRoot, 'alpha', 'hello.txt'), 'utf-8')).toBe('W1a 第二版')
  await page.screenshot({ path: 'test-results/world-switching-beta.png', fullPage: true })
})
