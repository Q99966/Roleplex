import { expect,test } from '@playwright/test'
import { mkdir,writeFile } from 'node:fs/promises'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({screenshot:'off',trace:'off',video:'off'})

test('真实 World 模型收到参数拒绝后修正并继续读取',async({page},testInfo)=>{
  const stamp=process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base=process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root=path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!,process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!,'default','argument-recovery')
  await mkdir(root)
  await writeFile(path.join(root,'proof.txt'),'controlled-argument-proof\n')
  let cid:number|null=null,stage='准备',failedStage:string|null=null,cleaned=false
  let observation:Record<string,unknown>={}
  try{
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码',{exact:true}).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button',{name:'进入工作台'}).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`,{exact:true})).toBeVisible({timeout:20000})
    cid=await page.evaluate(async({base,root})=>{
      const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const request=async(route:string,body?:unknown,method='POST')=>{
        const response=await fetch(base+route,{headers,...(body?{method,body:JSON.stringify(body)}:{})})
        if(!response.ok)throw new Error('真实参数恢复准备失败')
        return response.json()
      }
      if(!(await request('/api/health')).world_managed)throw new Error('需要 World')
      const seed=(await request('/api/roles')).find((role:{model_config_id:number|null})=>role.model_config_id)
      const workspace=await request('/api/workspaces',{display_name:'真实参数恢复',root_path:root,acknowledge_existing_content:true})
      await request(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
      const role=await request('/api/roles',{name:'真实参数纠错助手',model_config_id:seed.model_config_id,model_name:seed.model_name,
        params:{max_tokens:1024},builtin_tools:['workspace_read'],system_prompt:'这是工具参数错误恢复验收，请按用户指定顺序测试。第一次故意传错类型以验证服务端拒绝，随后根据实际反馈修正。不要省略第一次测试，不声称未执行的读取成功。'})
      return(await request('/api/conversations',{title:'真实工具参数纠错验收',type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id
    },{base,root})
    await page.reload()
    await page.getByRole('button',{name:'打开会话：真实工具参数纠错验收',exact:true}).click()
    stage='真实错误与修正'
    await page.getByLabel('消息输入框').fill('验证参数错误恢复：先调用 workspace_read，path 为 proof.txt，max_bytes 故意传字符串 "not-a-number"，预期服务端拒绝。收到错误后第二次调用将 max_bytes 改为整数 64，path 不变。最后简短报告两次调用的实际结果。只做这两次工具调用。')
    await page.getByLabel('发送消息').click()
    await expect.poll(async()=>page.evaluate(async({base,cid})=>{
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).json()
      return history.active_generation_ids.length===0 && history.items.some((row:{sender_type:string,status:string})=>row.sender_type==='role'&&row.status!=='generating')
    },{base,cid}),{timeout:90000}).toBe(true)
    stage='结果核对'
    observation=await page.evaluate(async({base,cid})=>{
      const headers={Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers})).json()
      const reply=history.items.find((row:{sender_type:string})=>row.sender_type==='role')
      const calls=reply.parts_json.filter((part:{type:string})=>part.type==='tool_call')
      const failed=calls.find((part:{error_code:string})=>part.error_code==='TOOL_ARGUMENT_INVALID')
      const good=calls.find((part:{status:string})=>part.status==='success')
      const detail=failed?await(await fetch(`${base}/api/conversations/${cid}/messages/${reply.id}/tools/${failed.call_id}`,{headers})).json():null
      return{normal_finish:reply.status==='done',call_count:calls.length,refused:failed?.status==='not_executed',
        corrected:!!good,diagnostic:detail?.argument_error?.issues?.some((issue:{path:unknown[]})=>issue.path.includes('max_bytes'))===true}
    },{base,cid})
    if(!observation.normal_finish||!observation.refused||!observation.corrected||!observation.diagnostic||observation.call_count!==2)throw new Error('参数恢复未完成')
    await page.reload()
    await expect(page.getByTestId('tool-call-card')).toHaveCount(2)
    const calls=(await readRunEvents()).filter(event=>event.conversation_id===cid&&event.event==='provider.call_completed')
    observation.model=calls[0]?.model??null
    observation.provider_calls=calls.length
    observation.output_tokens=calls.every(event=>typeof event.output_tokens==='number')?calls.reduce((sum,event)=>sum+Number(event.output_tokens),0):null
  }catch{failedStage=stage}
  finally{
    if(cid!==null)cleaned=await page.evaluate(async({base,cid})=>(await fetch(`${base}/api/conversations/${cid}/stop`,{method:'POST',signal:AbortSignal.timeout(5000),headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).ok,{base,cid}).catch(()=>false)
    await page.goto('about:blank').catch(()=>undefined)
    const report=testInfo.outputPath('argument-recovery.json')
    await writeFile(report,JSON.stringify({...observation,failed_stage:failedStage,cleanup_passed:cleaned}))
    await testInfo.attach('真实参数恢复',{path:report,contentType:'application/json'})
  }
  if(failedStage||!cleaned)throw new Error(`真实参数恢复失败：${failedStage??'清理'}`)
})
