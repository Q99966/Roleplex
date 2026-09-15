import { expect, test } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { ensureOwnerSession } from '../owner'

for (const kind of ['schema','json','identity']) {
  test(`参数 ${kind} 错误不终止会话，修正后真实写入并保留诊断`, async ({ page },testInfo) => {
    const root=path.join(process.env.ROLEPLEX_COMMAND_E2E_WORKSPACE!,`argument-${kind}`)
    await mkdir(root)
    await ensureOwnerSession(page)
    const title=`参数修正-${kind}`
    const cid=await page.evaluate(async ({root,title})=>{
      const headers={'Content-Type':'application/json',Authorization:`Bearer ${localStorage.getItem('roleplex_token')}`}
      const post=async(route:string,body:unknown,method='POST')=>{
        const response=await fetch(route,{method,headers,body:JSON.stringify(body)})
        if(!response.ok)throw new Error('参数恢复准备失败')
        return response.json()
      }
      const model=await post('/api/model-configs',{name:title,provider_type:'openai_compatible',api_key:'sk-placeholder'})
      const role=await post('/api/roles',{name:title,model_config_id:model.id,model_name:'fake-model',system_prompt:'受控修正',builtin_tools:['workspace_write']})
      const workspace=await post('/api/workspaces',{display_name:title,root_path:root,acknowledge_existing_content:true})
      await post(`/api/workspaces/${workspace.id}`,{file_tools_enabled:true},'PATCH')
      return(await post('/api/conversations',{title,type:'single',role_ids:[role.id],workspace_binding_id:workspace.id})).id
    },{root,title})
    await page.reload()
    await page.getByRole('button',{name:`打开会话：${title}`,exact:true}).click()
    await page.getByLabel('消息输入框').fill(kind==='identity'?'[TOOL_ID_ERROR_FAKE]':kind==='json'?'[JSON_RECOVERY_FAKE]':'[ARGUMENT_RECOVERY_FAKE]')
    await page.getByLabel('发送消息').click()
    if(kind==='identity'){
      await expect(page.getByText(/模型返回的工具调用标识缺失或重复/)).toBeVisible()
      await expect(page.getByTestId('tool-call-card')).toHaveCount(0)
      expect(await readFile(path.join(root,'recovered.txt')).then(()=>true).catch(()=>false)).toBe(false)
      return
    }
    await expect(page.getByText('参数修正后已完成。',{exact:true})).toBeVisible()
    const cards=page.getByTestId('tool-call-card')
    await expect(cards).toHaveCount(2)
    await expect(cards.first()).toContainText(kind==='json'?'TOOL_ARGUMENT_JSON_INVALID':'TOOL_ARGUMENT_INVALID')
    await cards.first().getByRole('button',{name:'执行详情：workspace_write'}).click()
    await expect(cards.first()).toContainText(kind==='json'?'参数不是合法 JSON':'字段类型不正确')
    await expect(cards.last()).toContainText('已完成')
    expect(await readFile(path.join(root,'recovered.txt'),'utf8')).toBe('confirmed')
    const shot=testInfo.outputPath(`argument-${kind}.png`)
    await cards.first().screenshot({path:shot})
    await testInfo.attach('可恢复参数错误',{path:shot,contentType:'image/png'})
    await page.reload()
    await cards.first().getByRole('button',{name:'执行详情：workspace_write'}).click()
    await expect(cards.first()).toContainText(kind==='json'?'参数不是合法 JSON':'字段类型不正确')
    if(kind==='json'){
      const database=path.resolve(process.cwd(),'..',process.env.ROLEPLEX_E2E_WORLDS!.split(',')[0],'roleplex.db')
      execFileSync('python',['tests/seed_tool_viewer.py',database,String(cid)],{cwd:path.resolve(process.cwd(),'../backend')})
      await page.evaluate(async stamp=>{
        const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:`test${stamp}_toolviewer`,password:'Roleplex-Test-1234'})})
        if(!response.ok)throw new Error('Guest 登录失败')
        localStorage.setItem('roleplex_token',(await response.json()).access_token)
      },process.env.ROLEPLEX_COMMAND_E2E_STAMP)
      await page.reload()
      await cards.first().getByRole('button',{name:'执行详情：workspace_write'}).click()
      await expect(page.getByText('详细输入和输出仅 Owner 可见。')).toBeVisible()
      await expect(page.getByText('参数不是合法 JSON',{exact:false})).toHaveCount(0)
    }
  })
}
