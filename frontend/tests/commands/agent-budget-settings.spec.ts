import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('预算可配置，冲突不覆盖，运行中任务保留发送时额度', async ({ page }, testInfo) => {
  await ensureOwnerSession(page)
  await page.getByRole('complementary', { name: '工作区侧栏' }).getByRole('button', { name: '打开设置' }).click()
  await page.getByRole('tab', { name: /运行世界与存储/ }).click()
  const panel = page.getByRole('region', { name: '任务决策预算' })
  const input = page.getByLabel('每个任务的决策上限', { exact: true })
  await expect(input).toHaveValue('8')
  await panel.getByRole('button', { name: '32 次', exact: true }).click()
  await panel.getByRole('button', { name: '保存任务预算', exact: true }).click()
  await expect(panel.getByRole('status')).toHaveText('已保存，仅新任务生效。')
  // 另一页面更新配置，旧页面的 expected_revision 必须被拒绝。
  await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}`, 'Content-Type':'application/json' }
    const config = await (await fetch('/api/agent-budget/config', { headers })).json()
    const response = await fetch('/api/agent-budget/config', { method:'PUT', headers, body:JSON.stringify({decision_limit:16,expected_revision:config.revision}) })
    if (!response.ok) throw new Error('并发配置准备失败')
  })
  await input.fill('12')
  await panel.getByRole('button', { name: '保存任务预算', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('配置可能已变化')
  await panel.getByRole('button', { name: '重新加载预算', exact: true }).click()
  await expect(input).toHaveValue('16')
  await input.fill('12')
  await panel.getByRole('button', { name: '保存任务预算', exact: true }).click()
  await expect(panel.getByRole('status')).toHaveText('已保存，仅新任务生效。')
  await input.fill('512')
  await panel.getByRole('button', { name:'保存任务预算',exact:true }).click()
  await expect(panel.getByRole('status')).toHaveText('已保存，仅新任务生效。')
  await panel.getByRole('button', { name:'重新加载预算',exact:true }).click()
  await expect(input).toHaveValue('512')
  await panel.getByRole('button', { name:'不限次数',exact:true }).click()
  await expect(input).toBeDisabled()
  await panel.getByRole('button', { name:'保存任务预算',exact:true }).click()
  await expect(panel.getByRole('status')).toHaveText('已保存，仅新任务生效。')
  await page.reload()
  await page.getByRole('complementary', { name:'工作区侧栏' }).getByRole('button', { name:'打开设置' }).click()
  await page.getByRole('tab', { name:/运行世界与存储/ }).click()
  await expect(panel.getByRole('button', { name:'不限次数',exact:true })).toHaveAttribute('aria-pressed','true')
  await expect(input).toBeDisabled()
  const shot = testInfo.outputPath('agent-budget-settings.png')
  await panel.screenshot({ path:shot })
  await testInfo.attach('World 任务预算设置', { path:shot, contentType:'image/png' })
  await page.getByRole('button', { name:'关闭系统与环境设置' }).click()
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'budget-settings')
  await mkdir(root)
  const cid = await page.evaluate(async root => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}`, 'Content-Type':'application/json' }
    const post = async (route:string, body:unknown, method='POST') => {
      const response = await fetch(route, {method, headers, body:JSON.stringify(body)})
      if (!response.ok) throw new Error('长任务准备失败')
      return response.json()
    }
    const model = await post('/api/model-configs', {name:'预算模型',provider_type:'openai_compatible',api_key:'sk-placeholder'})
    const role = await post('/api/roles', {name:'预算长任务',model_config_id:model.id,model_name:'fake-model',system_prompt:'受控测试。',builtin_tools:['workspace_list']})
    const workspace = await post('/api/workspaces', {display_name:'预算工作区',root_path:root,acknowledge_existing_content:true})
    await post(`/api/workspaces/${workspace.id}`, {file_tools_enabled:true}, 'PATCH')
    return (await post('/api/conversations', {title:'超过旧额度',type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id
  }, root)
  await page.reload()
  await page.getByRole('button', {name:'打开会话：超过旧额度',exact:true}).click()
  await page.getByLabel('消息输入框').fill('[LONG_DECISIONS_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('tool-call-card').first()).toBeVisible()
  await page.evaluate(async () => {
    const headers = {Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`,'Content-Type':'application/json'}
    const config = await (await fetch('/api/agent-budget/config',{headers})).json()
    const response = await fetch('/api/agent-budget/config',{method:'PUT',headers,body:JSON.stringify({decision_limit:1,expected_revision:config.revision})})
    if (!response.ok) throw new Error('更新默认预算失败')
  })
  await expect(page.getByText('长任务完成。',{exact:true})).toBeVisible({timeout:30000})
  await expect(page.getByTestId('tool-call-card')).toHaveCount(10)
  await page.reload()
  await expect(page.getByText('长任务完成。',{exact:true})).toBeVisible()
  const status = await page.evaluate(async cid => {
    const headers = {Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
    return (await (await fetch(`/api/conversations/${cid}/messages`,{headers})).json()).items.find((item:{sender_type:string})=>item.sender_type==='role').status
  },cid)
  expect(status).toBe('done')
  // 后续测试共用该轮 World，将默认还原，不保留任务间隐式依赖。
  await page.evaluate(async () => {
    const headers = {Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`,'Content-Type':'application/json'}
    const config = await (await fetch('/api/agent-budget/config',{headers})).json()
    await fetch('/api/agent-budget/config',{method:'PUT',headers,body:JSON.stringify({decision_limit:8,expected_revision:config.revision})})
  })
})
