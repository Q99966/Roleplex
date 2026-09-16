import { expect,test } from '@playwright/test'
import { mkdir,readFile } from 'node:fs/promises'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

for(const revoked of [false,true]) test(`停止后普通新要求的事实交接，撤权=${revoked}`,async({page},testInfo)=>{
  await ensureOwnerSession(page)
  const root=path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!,`interruption-context-${revoked}`)
  await mkdir(root)
  const {cid,wid}=await page.evaluate(async root=>{
    const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
    const post=async(route:string,body:unknown,method='POST')=>{
      const response=await fetch(route,{method,headers,body:JSON.stringify(body)})
      if(!response.ok)throw new Error('恢复测试准备失败')
      return response.json()
    }
    const model=await post('/api/model-configs',{name:'中断事实模型'+root.split('/').at(-1),provider_type:'openai_compatible',api_key:'sk-placeholder'})
    const role=await post('/api/roles',{name:'中断事实助手'+root.split('/').at(-1),model_config_id:model.id,model_name:'fake-model',system_prompt:'按当前请求操作。',builtin_tools:['workspace_write','workspace_edit','workspace_read']})
    const workspace=await post('/api/workspaces',{display_name:'中断事实工作区'+root.split('/').at(-1),root_path:root,acknowledge_existing_content:true})
    await post(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
    const conversation=await post('/api/conversations',{title:'中断事实交接验收'+root.split('/').at(-1),type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})
    return {cid:conversation.id as number,wid:workspace.id as number}
  },root)
  await page.reload()
  await page.getByRole('button',{name:`打开会话：中断事实交接验收interruption-context-${revoked}`,exact:true}).click()
  await page.getByLabel('消息输入框').fill('[INTERRUPTION_SETUP_FAKE]')
  await page.getByLabel('发送消息').click()
  await expect.poll(()=>readFile(path.join(root,'interruption.txt'),'utf8').catch(()=>''),{timeout:15000}).toBe('alpha=1\n')
  await page.getByRole('button',{name:'停止生成',exact:true}).click()
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0)
  await page.reload()
  if(revoked) await page.evaluate(async wid=>{
    const response=await fetch(`/api/workspaces/${wid}`,{method:'PATCH',headers:{'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`},body:JSON.stringify({file_tools_enabled:false})})
    if(!response.ok)throw new Error('撤权失败')
  },wid)
  await page.getByLabel('消息输入框').fill('请将已写好的alpha改为2，不要重新创建文件')
  await page.getByLabel('发送消息').click()
  if(revoked){
    await expect(page.getByText('缺少可信执行事实。',{exact:true})).toBeVisible({timeout:20000})
    expect(await readFile(path.join(root,'interruption.txt'),'utf8')).toBe('alpha=1\n')
    return
  }
  await expect(page.getByText('已根据执行事实完成新要求。',{exact:true})).toBeVisible({timeout:20000})
  expect(await readFile(path.join(root,'interruption.txt'),'utf8')).toBe('alpha=2\n')
  const result=await page.evaluate(async cid=>{
    const history=await(await fetch(`/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).json()
    const replies=history.items.filter((item:{sender_type:string})=>item.sender_type==='role')
    return {states:replies.map((item:{status:string})=>item.status),tools:replies.map((item:{parts_json:{type:string;tool_name?:string}[]})=>item.parts_json.filter(part=>part.type==='tool_call').map(part=>part.tool_name))}
  },cid)
  expect(result).toEqual({states:['stopped','done'],tools:[['workspace_write'],['workspace_edit']]})
  await expect(page.getByText('以下是服务器观察到的最近中断执行数据',{exact:false})).toHaveCount(0)
  await page.getByLabel('消息输入框').fill('改做一个新问题：回复你好即可')
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('chat-message').nth(5)).toContainText('改做一个新问题')
  expect(await readFile(path.join(root,'interruption.txt'),'utf8')).toBe('alpha=2\n')
  const shot=testInfo.outputPath('interruption-context.png')
  await page.screenshot({path:shot})
  await testInfo.attach('普通新要求完成',{path:shot,contentType:'image/png'})
})
