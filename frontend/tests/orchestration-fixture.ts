import type { Page } from '@playwright/test'

/** 创建受控群流程；真实凭据仅复用后端加密配置，浏览器不接触 Key。 */
export async function orchestrationFixture(page: Page, root: string, real = false, title = '并行返工协调验收') {
  return page.evaluate(async ({ root, real, base, title }) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    async function request(url: string, body?: unknown, method = 'POST') {
      const r = await fetch(base + '/api' + url, { method: body === undefined ? 'GET' : method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
      if (!r.ok) throw new Error(`orchestration fixture ${r.status}`)
      return r.json()
    }
    let cfg: number, model: string
    if (real) {
      const seed = (await request('/roles')).find((r: { model_config_id: number; deleted_at: string | null }) => r.model_config_id && !r.deleted_at)
      cfg = seed.model_config_id; model = seed.model_name
    } else {
      cfg = (await request('/model-configs', { name: `${title}配置`, provider_type: 'openai_compatible', api_key: 'sk-placeholder' })).id
      model = 'fake-model'
    }
    const budget = await request('/agent-budget/config')
    await request('/agent-budget/config', { decision_limit: 64, expected_revision: budget.revision }, 'PUT')
    const roles = []
    for (const [name, tools] of [['开发', ['workspace_read','workspace_write']], ['审查A', ['workspace_read']], ['审查B', ['workspace_read']], ['协调者', []]] as const) {
      roles.push(await request('/roles', { name: `${title}${name}`, model_config_id: cfg, model_name: model,
        context_window_tokens: 200000, params: { max_tokens: 4096, temperature: 0 }, builtin_tools: tools,
        system_prompt: '按当前节点指令完成实际工具操作。使用 workflow_result 提交所需结构化结果后简短结束。协调阶段先 workflow_read_graph，保留用户指定结构、角色与工具；需要改图时使用 workflow_write_graph/workflow_edit_graph，只有实际获得 workflow_start 时才启动。汇总阶段调用 workflow_summary。不要重复成功的工具调用。' }))
    }
    const workspace = await request('/workspaces', { display_name: `${title}工作区`, root_path: root, acknowledge_existing_content: true })
    await request(`/workspaces/${workspace.id}`, { file_tools_enabled: true }, 'PATCH')
    const conv = await request('/conversations', { title, type: 'group', role_ids: roles.map(r => r.id), workspace_binding_id: workspace.id })
    const nodes = [
      { id: 'build', kind: 'role', title: '开发', role_id: roles[0].id, tools: ['workspace_read','workspace_write'], result_keys: ['round'],
        task: real ? '读取 workflow-round.txt（不存在属于正常情况）。根据本次节点激活数据的 iteration，内容写为 round-1（iteration=0）或 round-2（iteration>=1），不加换行；已有文件使用刚读取的 sha256。成功后 workflow_result values.round 为 iteration+1。' : '[WF_BUILD]' },
      ...['a','b'].map((id, i) => ({ id, kind: 'role', title: i ? '审查B' : '审查A', role_id: roles[i+1].id, tools: ['workspace_read'], result_keys: ['approved'], inputs: ['build'],
        task: real ? '实际 workspace_read 读取 workflow-round.txt；只有内容恰为 round-2 才通过，round-1 必须不通过。workflow_result values.approved 为布尔值，values.observed 为实际内容。不要修改文件。' : '[WF_REVIEW]' })),
      { id: 'join', kind: 'join', title: '汇合', inputs: ['a','b'] },
      { id: 'judge', kind: 'judge', title: '协调判断', role_id: roles[3].id, tools: [], inputs: ['join'], condition: { sources: ['$self'], key: 'approved', value: true },
        task: real ? '核对本轮两个审查节点的结构化 approved 与 observed。只有两个 approved 都为布尔 true 才在 workflow_result values.approved 报告 true，否则报告 false。使用本次节点激活数据中的本轮结果。' : '[WF_JUDGE]' },
      { id: 'end', kind: 'join', title: '结束', inputs: ['judge'] },
    ].map((node, i) => ({ ...node, position: { x: i * 280, y: 80 } }))
    const did = crypto.randomUUID()
    await request(`/conversations/${conv.id}/workflows/definitions/${did}`, { name: '并行返工', expected_revision: 0,
      graph: { runtime_version: 2, concurrency: 3, nodes,
        edges: [['build','a'],['build','b'],['a','join'],['b','join'],['join','judge'],['judge','build'],['judge','end']],
        loops: [{ id: 'revision', entry: 'build', decision: 'judge', exit: 'end', body: ['build','a','b','join','judge'], carry_inputs: ['judge'], max_iterations: 3 }] } }, 'PUT')
    return { cid: conv.id as number, did, coordinator: roles[3].id as number }
  }, { root, real, title, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}

export async function orchestrationSnapshot(page: Page, cid: number) {
  return page.evaluate(async ({ cid, base }) => {
    const r = await fetch(`${base}/api/conversations/${cid}/workflows`, { headers: { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` } })
    return r.json()
  }, { cid, base: process.env.ROLEPLEX_E2E_API_ORIGIN! })
}
