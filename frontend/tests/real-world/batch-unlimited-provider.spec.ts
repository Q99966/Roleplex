import { test, expect } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({ screenshot:'off',trace:'off',video:'off' })

test('真实 World 一次批量创建并编辑12个文件',async({page},testInfo)=>{
  const stamp=process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base=process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root=path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!,process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!,'default','batch-unlimited')
  await mkdir(root)
  let cid:number|null=null,stage='登录',failedStage:string|null=null,cleaned=false
  let observation:Record<string,unknown>={}
  try {
    await page.goto('/#/auth')
    await page.getByPlaceholder('owner').fill(`realtest${stamp}`)
    await page.getByPlaceholder('密码',{exact:true}).fill('Roleplex-Real-E2E-1')
    await page.getByRole('button',{name:'进入工作台'}).click()
    await expect(page.getByText(`真实 API 验证 ${stamp}`,{exact:true})).toBeVisible({timeout:20000})
    stage='准备工作区'
    cid=await page.evaluate(async({base,root})=>{
      const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const request=async(route:string,body?:unknown,method='POST')=>{
        const response=await fetch(base+route,{headers,...(body?{method,body:JSON.stringify(body)}:{})})
        if(!response.ok)throw new Error('批次准备失败')
        return response.json()
      }
      const seed=(await request('/api/roles')).find((role:{model_config_id:number|null})=>role.model_config_id)
      const workspace=await request('/api/workspaces',{display_name:'真实12文件批次',root_path:root,acknowledge_existing_content:true})
      await request(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
      const role=await request('/api/roles',{name:'真实大批次助手',model_config_id:seed.model_config_id,model_name:seed.model_name,
        params:{max_tokens:4096},system_prompt:'按要求执行批量文件操作，使用工具返回的真实版本，失败后停止，不重传文件正文。',builtin_tools:['workspace_write','workspace_edit']})
      return (await request('/api/conversations',{title:'真实批次取消上限验收',type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id as number
    },{base,root})
    await page.reload()
    await page.getByRole('button',{name:'打开会话：真实批次取消上限验收',exact:true}).click()
    stage='模型批量执行'
    await page.getByLabel('消息输入框').fill('请只用一次 workspace_write 的 items 数组新建12个文件：file-01.txt 到 file-12.txt，每个正文都是 alpha=1（没有换行）。成功后，使用写入结果返回的各文件真实sha256，只用一次 workspace_edit 的 items 数组把全部12个文件的 alpha=1 改成 alpha=2。不要拆批，不要猜hash；任一步失败就停止，不重试。最后简短回答。')
    await page.getByLabel('发送消息').click()
    await expect.poll(async()=>page.evaluate(async({base,cid})=>{
      const response=await fetch(`${base}/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})
      const history=await response.json()
      return history.items.some((item:{sender_type:string;status:string})=>item.sender_type==='role'&&['done','error','stopped'].includes(item.status))&&history.active_generation_ids.length===0
    },{base,cid}),{timeout:90000}).toBe(true)
    stage='核对结果'
    await page.reload()
    observation=await page.evaluate(async({base,cid})=>{
      const headers={Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers})).json()
      const message=history.items.find((item:{sender_type:string})=>item.sender_type==='role')
      const parts=message.parts_json.filter((part:{type:string})=>part.type==='tool_call')
      const calls=[]
      for(const part of parts){
        const detail=await(await fetch(`${base}/api/conversations/${cid}/messages/${message.id}/tools/${part.call_id}`,{headers})).json()
        calls.push({tool:part.tool_name,status:part.status,count:detail.write_batch?.items.length??0,
          applied:detail.write_batch?.items.filter((node:{applied:boolean})=>node.applied===true).length??0})
      }
      return {status:message.status,calls}
    },{base,cid})
    expect(observation.status).toBe('done')
    expect(observation.calls).toEqual([{tool:'workspace_write',status:'success',count:12,applied:12},{tool:'workspace_edit',status:'success',count:12,applied:12}])
    let matches=0
    for(let index=1;index<=12;index++)if((await readFile(path.join(root,`file-${String(index).padStart(2,'0')}.txt`),'utf8'))==='alpha=2')matches++
    observation.files_verified=matches
    expect(matches).toBe(12)
    const calls=(await readRunEvents()).filter(event=>event.conversation_id===cid&&event.event==='provider.call_completed')
    expect(calls.length).toBeGreaterThan(0)
    expect(calls.every(event=>event.provider_mode==='real')).toBe(true)
    observation.provider_calls=calls.length
    observation.model=calls[0]?.model??null
    observation.output_tokens=calls.every(event=>typeof event.output_tokens==='number')?calls.reduce((sum,event)=>sum+Number(event.output_tokens),0):null
    observation.passed=true
  } catch {failedStage=stage}
  finally {
    if(cid!==null)cleaned=await page.evaluate(async({base,cid})=>(await fetch(`${base}/api/conversations/${cid}/stop`,{
      method:'POST',signal:AbortSignal.timeout(5000),headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).ok,{base,cid}).catch(()=>false)
    await page.goto('about:blank').catch(()=>undefined)
    const report=testInfo.outputPath('batch-unlimited.json')
    await writeFile(report,JSON.stringify({...observation,failed_stage:failedStage,cleanup_passed:cleaned}))
    await testInfo.attach('真实大批次验收',{path:report,contentType:'application/json'})
  }
  if(failedStage||!cleaned)throw new Error(`真实批次验证失败：${failedStage??'清理'}`)
})
