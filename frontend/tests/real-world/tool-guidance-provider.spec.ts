import { expect,test } from '@playwright/test'
import { mkdir,readFile,writeFile } from 'node:fs/promises'
import path from 'node:path'
import { readRunEvents } from '../e2e-log-assertions'

test.use({screenshot:'off',trace:'off',video:'off'})

test('真实 World 验证搜索、范围读取、局部编辑和新建文件说明',async({page},testInfo)=>{
  const stamp=process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP!
  const base=process.env.ROLEPLEX_E2E_API_ORIGIN!
  const root=path.join(process.env.ROLEPLEX_E2E_WORKSPACE_ROOT!,process.env.ROLEPLEX_E2E_WORKSPACE_RELATIVE_ROOT!,'default','tool-guidance')
  await mkdir(path.join(root,'src'),{recursive:true})
  const original='export const initialScore = 0;\n'+'// 保留这段无关说明，不要重写。\n'.repeat(1800)+'export function resetScore() { return 0; }\n'
  await writeFile(path.join(root,'src','scoring.js'),original)
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
        if(!response.ok)throw new Error('工具说明验证准备失败')
        return response.json()
      }
      if(!(await request('/api/health')).world_managed)throw new Error('需要独立 World')
      const seed=(await request('/api/roles')).find((role:{model_config_id:number|null})=>role.model_config_id)
      const workspace=await request('/api/workspaces',{display_name:'真实工具说明验证',root_path:root,acknowledge_existing_content:true})
      await request(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
      const role=await request('/api/roles',{name:'真实工具使用助手',model_config_id:seed.model_config_id,model_name:seed.model_name,
        params:{max_tokens:1024},builtin_tools:['workspace_search','workspace_read','workspace_write','workspace_edit'],
        system_prompt:'按工具定义使用当前工作区。先获取真实版本，再修改；保留无关内容，简短报告，不重复文件正文。'})
      return(await request('/api/conversations',{title:'真实工具说明验收',type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id
    },{base,root})
    await page.reload()
    await page.getByRole('button',{name:'打开会话：真实工具说明验收',exact:true}).click()
    stage='真实工具调用'
    await page.getByLabel('消息输入框').fill('请先搜索 src 中的 initialScore 和 resetScore，按行读取各自附近内容，将初始得分和重置后的得分都改为 5。文件中有很长的无关说明，使用局部编辑保留它，不重写整份文件。同时新建 docs/change-note.txt，简短记录这两处修改。最后核对结果。')
    await page.getByLabel('发送消息').click()
    await expect.poll(async()=>page.evaluate(async({base,cid})=>{
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).json()
      return history.active_generation_ids.length===0&&history.items.some((row:{sender_type:string,status:string})=>row.sender_type==='role'&&row.status!=='generating')
    },{base,cid}),{timeout:110000}).toBe(true)
    stage='实际结果核对'
    observation=await page.evaluate(async({base,cid})=>{
      const headers={Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const history=await(await fetch(`${base}/api/conversations/${cid}/messages`,{headers})).json()
      const reply=history.items.find((row:{sender_type:string})=>row.sender_type==='role')
      const calls=reply.parts_json.filter((part:{type:string})=>part.type==='tool_call')
      const used=Array.from(new Set(calls.filter((part:{status:string})=>part.status==='success').map((part:{tool_name:string})=>part.tool_name)))
      let lineRead=false
      for(const call of calls.filter((part:{tool_name:string,status:string})=>part.tool_name==='workspace_read'&&part.status==='success')){
        const detail=await(await fetch(`${base}/api/conversations/${cid}/messages/${reply.id}/tools/${call.call_id}`,{headers})).json()
        lineRead ||= !!detail.read_range || detail.read_batch?.items?.some((item:{result?:{start_line?:number}})=>typeof item.result?.start_line==='number')===true
      }
      return{normal_finish:reply.status==='done',used,line_read:lineRead,rejected:calls.filter((part:{status:string})=>part.status==='not_executed'||part.status==='rejected').length}
    },{base,cid})
    observation.file_matches=(await readFile(path.join(root,'src','scoring.js'),'utf8'))===original.replace('initialScore = 0','initialScore = 5').replace('return 0;','return 5;')
    observation.note_created=(await readFile(path.join(root,'docs','change-note.txt'),'utf8')).trim().length>0
    const used=observation.used as string[]
    if(!observation.normal_finish||!observation.line_read||!observation.file_matches||!observation.note_created||!['workspace_search','workspace_read','workspace_edit','workspace_write'].every(name=>used.includes(name)))throw new Error('实际工具路径未覆盖')
    const calls=(await readRunEvents()).filter(event=>event.conversation_id===cid&&event.event==='provider.call_completed')
    observation.provider_calls=calls.length
    observation.model=calls[0]?.model??null
    observation.output_tokens=calls.every(event=>typeof event.output_tokens==='number')?calls.reduce((sum,event)=>sum+Number(event.output_tokens),0):null
  }catch{failedStage=stage}
  finally{
    if(cid!==null)cleaned=await page.evaluate(async({base,cid})=>(await fetch(`${base}/api/conversations/${cid}/stop`,{method:'POST',signal:AbortSignal.timeout(5000),headers:{Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}})).ok,{base,cid}).catch(()=>false)
    await page.goto('about:blank').catch(()=>undefined)
    const report=testInfo.outputPath('tool-guidance.json')
    await writeFile(report,JSON.stringify({...observation,failed_stage:failedStage,cleanup_passed:cleaned}))
    await testInfo.attach('真实工具说明验证',{path:report,contentType:'application/json'})
  }
  if(failedStage||!cleaned)throw new Error(`真实工具说明验证失败：${failedStage??'清理'}`)
})
