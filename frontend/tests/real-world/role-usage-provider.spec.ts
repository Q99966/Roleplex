import { expect,test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { readRunEvents } from '../e2e-log-assertions'

test.use({screenshot:'off',trace:'off',video:'off'})

test('真实 World 群聊角色用量与厂商统计一致，最近与累计分开',async({page},testInfo)=>{
  const stamp=process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!,base=process.env.ROLEPLEX_E2E_API_ORIGIN!
  let cid:number|null=null,stage='准备',failedStage:string|null=null,cleaned=false
  let observation:Record<string,unknown>={}
  let roles:Array<{id:number;name:string}>=[]
  try{
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码',{exact:true}).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button',{name:'进入工作台'}).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`,{exact:true})).toBeVisible({timeout:20000})
    const prepared=await page.evaluate(async base=>{
      const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const request=async(route:string,body?:unknown)=>{
        const response=await fetch(base+route,{headers,...(body?{method:'POST',body:JSON.stringify(body)}:{})})
        if(!response.ok)throw new Error('真实用量准备失败')
        return response.json()
      }
      if(!(await request('/api/health')).world_managed)throw new Error('需要 World')
      const seed=(await request('/api/roles')).find((role:{model_config_id:number|null})=>role.model_config_id)
      const roles=[]
      for(const name of ['真实用量甲','真实用量乙'])roles.push(await request('/api/roles',{name,model_config_id:seed.model_config_id,
        model_name:seed.model_name,params:{max_tokens:512},builtin_tools:[],system_prompt:'仅简短回复，不调用工具。'}))
      const conversation=await request('/api/conversations',{title:'真实角色用量验收',type:'group',role_ids:roles.map(role=>role.id)})
      return{cid:conversation.id,roles:roles.map(role=>({id:role.id,name:role.name}))}
    },base)
    cid=prepared.cid;roles=prepared.roles
    await page.reload()
    await page.getByRole('button',{name:'打开会话：真实角色用量验收',exact:true}).click()
    stage='两轮真实群聊'
    for(let round=1;round<=2;round++){
      const input=page.getByLabel('消息输入框')
      await input.fill('@')
      await page.getByRole('option',{name:'@全部 · 按成员顺序回复',exact:true}).click()
      await input.fill(`${await input.inputValue()}第${round}轮：请各自仅回复“收到”。`)
      await page.getByLabel('发送消息').click()
      await expect.poll(async()=>page.evaluate(async({base,cid,round})=>{
        const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).json()
        return history.active_generation_ids.length===0&&history.items.filter((row:{sender_type:string,status:string})=>row.sender_type==='role'&&row.status==='done').length===round*2
      },{base,cid,round}),{timeout:90000}).toBe(true)
    }
    stage='角色归属与汇总核对'
    const usages=await page.evaluate(async({base,cid,roles})=>Promise.all(roles.map(async role=>{
      const response=await fetch(`${base}/api/conversations/${cid}/roles/${role.id}/usage`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})
      if(!response.ok)throw new Error('用量读取失败')
      return{role_id:role.id,...await response.json()}
    })),{base,cid,roles})
    const events=(await readRunEvents()).filter(event=>event.conversation_id===cid&&event.event==='provider.call_completed')
    for(const usage of usages){
      const calls=events.filter(event=>event.role_id===usage.role_id)
      expect(calls).toHaveLength(2)
      expect(usage.cumulative.recorded_calls).toBe(2)
      expect(usage.latest.summary.recorded_calls).toBe(1)
      for(const key of ['input_tokens','output_tokens','cache_hit_tokens','cache_write_tokens']){
        const expected=calls.every(call=>typeof call[key]==='number')?calls.reduce((sum,call)=>sum+Number(call[key]),0):null
        expect(usage.cumulative.metrics[key].total).toBe(expected)
      }
    }
    stage='右栏与刷新'
    await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
    await page.getByRole('menuitemradio',{name:'会话成员',exact:true}).locator('span').last().click()
    const member=page.getByRole('group',{name:`会话角色：${roles[0].name}`,exact:true})
    await member.getByText('执行用量',{exact:true}).click()
    const panel=member.getByRole('region',{name:'角色执行用量'})
    await expect(panel).toContainText('已记录模型调用 1 次')
    await panel.getByRole('button',{name:'本会话累计',exact:true}).click()
    await expect(panel).toContainText('2 次执行 · 已记录模型调用 2 次')
    const total=usages[0].cumulative.metrics.output_tokens.total
    expect(typeof total).toBe('number')
    await expect(panel.getByRole('group',{name:'输出 Token',exact:true}).getByText(Number(total).toLocaleString(),{exact:true})).toBeVisible()
    await page.reload()
    await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
    await page.getByRole('menuitemradio',{name:'会话成员',exact:true}).locator('span').last().click()
    await member.getByText('执行用量',{exact:true}).click()
    await panel.getByRole('button',{name:'本会话累计',exact:true}).click()
    await expect(panel.getByRole('group',{name:'输出 Token',exact:true}).getByText(Number(total).toLocaleString(),{exact:true})).toBeVisible()
    observation={passed:true,roles:usages.map(usage=>({role_id:usage.role_id,calls:usage.cumulative.recorded_calls,
      input_tokens:usage.cumulative.metrics.input_tokens.total,output_tokens:usage.cumulative.metrics.output_tokens.total})),provider_calls:events.length}
  }catch{failedStage=stage}
  finally{
    if(cid!==null)cleaned=await page.evaluate(async({base,cid})=>(await fetch(`${base}/api/conversations/${cid}/stop`,{method:'POST',signal:AbortSignal.timeout(5000),headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).ok,{base,cid}).catch(()=>false)
    await page.goto('about:blank').catch(()=>undefined)
    const report=testInfo.outputPath('role-usage.json')
    await writeFile(report,JSON.stringify({...observation,failed_stage:failedStage,cleanup_passed:cleaned}))
    await testInfo.attach('真实角色用量',{path:report,contentType:'application/json'})
  }
  if(failedStage||!cleaned)throw new Error(`真实角色用量失败：${failedStage??'清理'}`)
})
