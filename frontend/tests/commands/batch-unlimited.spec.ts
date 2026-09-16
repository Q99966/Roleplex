import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('60项批次保留完整结果并支持展开与刷新', async ({ page }, testInfo) => {
  const root = path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!, 'batch-unlimited')
  await mkdir(root)
  await ensureOwnerSession(page)
  const cid = await page.evaluate(async (root) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const request = async (route: string, body: unknown, method = 'POST') => {
      const response = await fetch(route, { method, headers, body: JSON.stringify(body) })
      if (!response.ok) throw new Error('批量修改准备失败')
      return response.json()
    }
    const model = await request('/api/model-configs', { name: 'batch-unlimited', provider_type: 'openai_compatible', api_key: 'sk-placeholder' })
    const role = await request('/api/roles', { name: '大批次助手', model_config_id: model.id, model_name: 'fake-model',
      system_prompt: '修改受控文件。', builtin_tools: ['workspace_write', 'workspace_edit'] })
    const workspace = await request('/api/workspaces', { display_name: '大批次工作区', root_path: root, acknowledge_existing_content: true })
    await request(`/api/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    return (await request('/api/conversations', { title: '大批次验收', type: 'single', role_ids: [role.id], workspace_binding_id: workspace.id })).id as number
  }, root)
  await page.reload()
  await page.getByText('大批次验收', { exact: true }).click()
  await page.getByLabel('消息输入框').fill('[BATCH_UNLIMITED_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('大批次修改完成。',{exact:true})).toBeVisible({timeout:60000})
  const edit=page.getByTestId('tool-call-card').filter({has:page.getByRole('button',{name:'执行详情：workspace_edit',exact:true})}).first()
  const batch=edit.getByRole('region',{name:'本次批量修改',exact:true})
  await expect(batch.locator('summary')).toHaveCount(50)
  await expect(batch).toContainText('共 60 项 · 已显示 50 项')
  await batch.getByRole('button',{name:'显示后续文件',exact:true}).click()
  await expect(batch.locator('summary')).toHaveCount(60)
  await expect(batch).toContainText('file-59.txt · 已应用')
  for(let index=0;index<60;index++) {
    const actual=await readFile(path.join(root,`file-${String(index).padStart(2,'0')}.txt`),'utf8')
    expect(actual=== (index===0?'new\n'+'中'.repeat(100000):'new')).toBe(true)
  }
  const counts=await page.evaluate(async cid=>{
    const headers={Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
    const history=await (await fetch(`/api/conversations/${cid}/messages`,{headers})).json()
    const message=history.items.find((item:{sender_type:string})=>item.sender_type==='role')
    const parts=message.parts_json.filter((part:{type:string})=>part.type==='tool_call')
    return Promise.all(parts.map(async(part:{call_id:string})=>{
      const detail=await (await fetch(`/api/conversations/${cid}/messages/${message.id}/tools/${part.call_id}`,{headers})).json()
      return {count:detail.write_batch.items.length,all_applied:detail.write_batch.items.every((node:{applied:boolean})=>node.applied===true)}
    }))
  },cid)
  expect(counts).toEqual([{count:60,all_applied:true},{count:60,all_applied:true}])
  await page.reload()
  await expect(batch).toContainText('共 60 项 · 已显示 50 项')
  await batch.getByRole('button',{name:'显示后续文件',exact:true}).click()
  await batch.locator('summary').last().scrollIntoViewIfNeeded()
  const shot=testInfo.outputPath('large-batch.png')
  await page.screenshot({path:shot})
  await testInfo.attach('大批次末项可访问',{path:shot,contentType:'image/png'})
})
