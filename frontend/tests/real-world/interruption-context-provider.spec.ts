import { test,expect } from '@playwright/test'
import { mkdir,readFile,writeFile } from 'node:fs/promises'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({screenshot:'off',trace:'off',video:'off'})

test('真实 World 主动停止后以新要求完成修改，不依赖继续关键词',async({page},testInfo)=>{
  const stamp=process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base=process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root=path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!,process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!,'default','interruption-context')
  await mkdir(root)
  let cid:number|null=null,stage='登录',failedStage:string|null=null,cleaned=false
  let observation:Record<string,unknown>={}
  try{
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码',{exact:true}).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button',{name:'进入工作台'}).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`,{exact:true})).toBeVisible({timeout:20000})
    stage='准备'
    cid=await page.evaluate(async({base,root})=>{
      const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const request=async(route:string,body?:unknown,method='POST')=>{
        const response=await fetch(base+route,{headers,...(body?{method,body:JSON.stringify(body)}:{})})
        if(!response.ok)throw new Error('事实交接准备失败')
        return response.json()
      }
      const seed=(await request('/api/roles')).find((role:{model_config_id:number|null})=>role.model_config_id)
      const workspace=await request('/api/workspaces',{display_name:'真实中断工作区',root_path:root,acknowledge_existing_content:true})
      await request(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
      const role=await request('/api/roles',{name:'真实中断事实助手',model_config_id:seed.model_config_id,model_name:seed.model_name,
        params:{max_tokens:1024},system_prompt:'以当前用户要求为准，依据工具返回或服务器核对的真实版本修改文件。不要猜hash或重复创建已有文件，失败后停止。',builtin_tools:['workspace_write','workspace_edit']})
      return (await request('/api/conversations',{title:'真实中断事实交接验收',type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id as number
    },{base,root})
    await page.reload()
    await page.getByRole('button',{name:'打开会话：真实中断事实交接验收',exact:true}).click()
    stage='创建后主动停止'
    await page.getByLabel('消息输入框').fill('请先用workspace_write创建interruption.txt，内容严格为alpha=1加一个换行。工具成功后，详细说明这个配置值以后可以如何用于网页小游戏。')
    await page.getByLabel('发送消息').click()
    await expect.poll(()=>readFile(path.join(root,'interruption.txt'),'utf8').catch(()=>''),{timeout:90000,intervals:[50,100]}).toBe('alpha=1\n')
    await page.getByRole('button',{name:'停止生成',exact:true}).click()
    await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0)
    await page.reload()
    stage='新要求完成修改'
    await page.getByLabel('消息输入框').fill('把interruption.txt中的alpha=1改为alpha=2，保留换行。请根据当前提供的已核对执行事实取得真实版本，使用workspace_edit，不要重新创建文件，不要猜hash；最后简短报告。')
    await page.getByLabel('发送消息').click()
    await expect.poll(async()=>page.evaluate(async({base,cid})=>{
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).json()
      const replies=history.items.filter((item:{sender_type:string})=>item.sender_type==='role')
      return replies.length===2&&replies.every((item:{status:string})=>['done','stopped','error'].includes(item.status))&&history.active_generation_ids.length===0
    },{base,cid}),{timeout:90000}).toBe(true)
    stage='核对'
    observation=await page.evaluate(async({base,cid})=>{
      const headers={Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers})).json()
      const replies=history.items.filter((item:{sender_type:string})=>item.sender_type==='role')
      const last=replies[1]
      const calls=last.parts_json.filter((part:{type:string})=>part.type==='tool_call')
      const confirmed=[]
      for(const part of calls){
        const detail=await(await fetch(`${base}/api/conversations/${cid}/messages/${last.id}/tools/${part.call_id}`,{headers})).json()
        confirmed.push({tool:part.tool_name,status:part.status,applied:detail.write?.files?.[0]?.applied===true})
      }
      return {states:replies.map((item:{status:string})=>item.status),stops:replies.map((item:{stop_reason:string})=>item.stop_reason),calls:confirmed,
        separate_chains:replies[0].chain_id!==last.chain_id}
    },{base,cid})
    expect(observation.states).toEqual(['stopped','done'])
    expect(observation.calls).toEqual([{tool:'workspace_edit',status:'success',applied:true}])
    expect(observation.separate_chains).toBe(true)
    observation.file_verified=(await readFile(path.join(root,'interruption.txt'),'utf8'))==='alpha=2\n'
    expect(observation.file_verified).toBe(true)
    const events=(await readRunEvents()).filter(event=>event.conversation_id===cid)
    const completed=events.filter(event=>event.event==='provider.call_completed')
    expect(completed.every(event=>event.provider_mode==='real')).toBe(true)
    observation.started_provider_calls=events.filter(event=>event.event==='provider.call_started').length
    observation.completed_provider_calls=completed.length
    observation.reported_output_tokens=completed.every(event=>typeof event.output_tokens==='number')?completed.reduce((sum,event)=>sum+Number(event.output_tokens),0):null
    observation.model=completed[0]?.model??null
    observation.passed=true
  }catch{failedStage=stage}
  finally{
    if(cid!==null)cleaned=await page.evaluate(async({base,cid})=>(await fetch(`${base}/api/conversations/${cid}/stop`,{method:'POST',signal:AbortSignal.timeout(5000),headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).ok,{base,cid}).catch(()=>false)
    await page.goto('about:blank').catch(()=>undefined)
    const report=testInfo.outputPath('interruption-context.json')
    await writeFile(report,JSON.stringify({...observation,failed_stage:failedStage,cleanup_passed:cleaned}))
    await testInfo.attach('真实事实交接验收',{path:report,contentType:'application/json'})
  }
  if(failedStage||!cleaned)throw new Error(`真实事实交接失败：${failedStage??'清理'}`)
})
