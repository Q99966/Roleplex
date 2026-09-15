import { expect,test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

test('角色用量区分最近与累计，fake 显示未知，刷新保留并隔离 Guest',async({page},testInfo)=>{
  await ensureOwnerSession(page)
  const {cid,rid}=await page.evaluate(async()=>{
    const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
    const post=async(route:string,body:unknown)=>{
      const response=await fetch(route,{method:'POST',headers,body:JSON.stringify(body)})
      if(!response.ok)throw new Error('用量准备失败')
      return response.json()
    }
    const model=await post('/api/model-configs',{name:'用量模型',provider_type:'openai_compatible',api_key:'sk-placeholder'})
    const role=await post('/api/roles',{name:'用量助手',model_config_id:model.id,model_name:'fake-model',system_prompt:'简短回答。'})
    const conversation=await post('/api/conversations',{title:'角色用量验收',type:'single',role_ids:[role.id]})
    return{cid:conversation.id,rid:role.id}
  })
  await page.reload()
  await page.getByRole('button',{name:'打开会话：角色用量验收',exact:true}).click()
  let requests=0
  page.on('request',request=>{if(request.url().includes(`/roles/${rid}/usage`))requests++})
  await page.getByLabel('消息输入框').fill('第一条用量消息')
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('chat-message').nth(1)).toContainText('第一条用量消息')
  const panel=page.getByRole('complementary',{name:'会话详情',exact:true})
  await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
  await page.getByRole('menuitemradio',{name:'会话成员',exact:true}).locator('span').last().click()
  await expect(panel.getByText('执行用量',{exact:true})).toBeVisible()
  expect(requests).toBe(0)
  await panel.getByText('执行用量',{exact:true}).click()
  const usage=panel.getByRole('region',{name:'角色执行用量'})
  await expect(usage).toContainText('已记录模型调用 1 次')
  await expect(usage.getByRole('group',{name:'缓存未命中输入 Token',exact:true})).toBeVisible()
  await expect(usage.getByRole('group',{name:'输入缓存命中率',exact:true})).toBeVisible()
  await expect(usage.getByText('缓存写入 Token',{exact:true})).toHaveCount(0)
  await expect(usage.getByText('未知',{exact:true})).toHaveCount(4)
  await page.getByLabel('消息输入框').fill('第二条用量消息')
  await page.getByLabel('发送消息').click()
  await expect(page.getByTestId('chat-message').nth(3)).toContainText('第二条用量消息')
  await usage.getByRole('button',{name:'本会话累计',exact:true}).click()
  await expect(usage).toContainText('2 次执行 · 已记录模型调用 2 次')
  await usage.getByRole('button',{name:'最近一次',exact:true}).click()
  await expect(usage).toContainText('已记录模型调用 1 次')
  await expect(usage).toContainText('正常结束')
  const shot=testInfo.outputPath('role-usage.png')
  await panel.screenshot({path:shot})
  await testInfo.attach('角色用量',{path:shot,contentType:'image/png'})
  await page.reload()
  await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
  await page.getByRole('menuitemradio',{name:'会话成员',exact:true}).locator('span').last().click()
  await panel.getByText('执行用量',{exact:true}).click()
  await usage.getByRole('button',{name:'本会话累计',exact:true}).click()
  await expect(usage).toContainText('2 次执行 · 已记录模型调用 2 次')
  const database=path.resolve(process.cwd(),'..',process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0],'roleplex.db')
  execFileSync('python',['tests/seed_tool_viewer.py',database,String(cid)],{cwd:path.resolve(process.cwd(),'../backend')})
  await page.evaluate(async stamp=>{
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:`test${stamp}_toolviewer`,password:'Roleplex-Test-1234'})})
    if(!response.ok)throw new Error('Guest 登录失败')
    localStorage.setItem('roleplex_token',(await response.json()).access_token)
  },process.env.ROLEPLEX_COMMAND_E2E_STAMP)
  await page.reload()
  await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
  await page.getByRole('menuitemradio',{name:'会话成员',exact:true}).locator('span').last().click()
  await expect(panel.getByText('执行用量',{exact:true})).toHaveCount(0)
  const status=await page.evaluate(async({cid,rid})=>(await fetch(`/api/conversations/${cid}/roles/${rid}/usage`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).status,{cid,rid})
  expect(status).toBe(403)
})
