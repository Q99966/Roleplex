import { test, expect } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

test('连线避开节点、循环外侧返回、自环、拖动与保存恢复', async ({ page }, info) => {
  page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1680, height: 1000 })
  await ensureOwnerSession(page)
  await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const post = async (url: string, body: unknown, method='POST') => {
      const r=await fetch('/api'+url,{method,headers,body:JSON.stringify(body)})
      if (!r.ok) throw new Error(`routing fixture ${r.status}`)
      return r.json()
    }
    const cfg=await post('/model-configs',{name:'避障配置',provider_type:'openai_compatible',api_key:'sk-placeholder'})
    const role=await post('/roles',{name:'避障角色',model_config_id:cfg.id,model_name:'fake-model',system_prompt:'受控角色'})
    const conv=await post('/conversations',{title:'连线避障验收',type:'single',role_ids:[role.id]})
    await post(`/conversations/${conv.id}/workflows/definitions/${crypto.randomUUID()}`,{name:'连线避障',expected_revision:0,graph:{runtime_version:2,
      nodes:[['a','开发',0,180],['b','审查',330,180],['c','判断',660,180],['d','自环示例',330,440],['e','结束',1000,180]].map(([id,title,x,y])=>({id,title,kind:id==='c'?'condition':'approval',inputs:[],position:{x,y},...(id==='c'?{condition:{sources:['b'],key:'approved',value:true}}:{})})),
      edges:[['a','b'],['b','c'],['a','c'],['c','a'],['c','e'],['d','d']],
      loops:[{id:'revision',entry:'a',decision:'c',exit:'e',body:['a','b','c'],carry_inputs:[],repeat_when:false,max_iterations:3}],
    }},'PUT')
  })
  await page.reload()
  await page.getByRole('button',{name:'打开会话：连线避障验收',exact:true}).click()
  async function open(restored = false) {
    if (restored) await expect(page.getByRole('region', { name: '本地草稿', exact: true })).toBeVisible()
    else {
      await page.getByRole('button',{name:'切换详情模块',exact:true}).click()
      await page.getByRole('menuitemradio',{name:'工作流',exact:true}).locator('span').last().click()
    }
    const list = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: /^已保存流程/ }) }).first()
    await expect(list.locator('summary')).toContainText('1')
    if (!await list.evaluate(element => (element as HTMLDetailsElement).open)) await list.locator('summary').click()
    await page.getByRole('button',{name:/连线避障 v/}).click()
    await page.getByRole('region', { name: '工作流画布', exact: true }).getByRole('button', { name: '节点细节', exact: true }).click()
  }
  await open()
  const canvas=page.getByRole('region',{name:'工作流画布',exact:true})
  const edge=canvas.getByLabel('连线：开发 → 判断',{exact:true})
  async function routed() { await expect(canvas.locator('[data-routing="routed"]')).toHaveCount(6) }
  async function collisions() {
    return canvas.evaluate(root=>{
      const boxes=Array.from(root.querySelectorAll('.react-flow__node')).map(el=>el.getBoundingClientRect())
      const hit:string[]=[]
      root.querySelectorAll<SVGPathElement>('.react-flow__edge-path').forEach(path=>{
        const matrix=path.getScreenCTM()!, length=path.getTotalLength()
        for(let i=12;i<length-12;i+=2) {
          const p=path.getPointAtLength(i).matrixTransform(matrix)
          if(boxes.some(b=>p.x>b.left+3&&p.x<b.right-3&&p.y>b.top+3&&p.y<b.bottom-3)) {hit.push(path.id);break}
        }
      })
      return hit
    })
  }
  await routed()
  await expect.poll(collisions).toEqual([])
  await expect(canvas.getByText('返回下一轮 · 条件不成立',{exact:true})).toBeVisible()
  await expect(canvas.getByText('自环',{exact:true})).toBeVisible()
  const oldPath=await edge.locator('.react-flow__edge-path').getAttribute('d')
  const node=canvas.getByRole('button',{name:'节点 2：审查',exact:true})
  const box=(await node.boundingBox())!
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2)
  await page.mouse.down()
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2-130,{steps:16})
  await page.mouse.up()
  await routed()
  await expect(edge.locator('.react-flow__edge-path')).not.toHaveAttribute('d',oldPath!)
  await expect.poll(collisions).toEqual([])
  await page.getByRole('button',{name:'保存流程',exact:true}).click()
  await expect(page.getByText('已保存版本 2',{exact:true})).toBeVisible()
  await page.reload(); await open(true); await routed()
  await expect.poll(collisions).toEqual([])
  const shot=info.outputPath('workflow-routing.png')
  await canvas.screenshot({path:shot})
  await info.attach('避障与循环回边',{path:shot,contentType:'image/png'})
  await edge.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Delete')
  await expect(canvas.locator('.react-flow__edge')).toHaveCount(5)
})
