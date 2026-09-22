import { useEffect, useState } from 'react'
import { getAuthEpoch } from '../../api/client'
import { workflows, type WorkflowGraph } from '../../api/workflows'
import { useWorkflow } from './WorkflowContext'

/** 冲突始终属于当前编辑目标，保存被封闭；读取的是最新提案而非误用运行中的旧图。 */
export function WorkflowConflict() {
  const w = useWorkflow(), [remote, setRemote] = useState<WorkflowGraph | null>(null)
  const epoch = getAuthEpoch()
  useEffect(() => {
    let live = true
    void workflows.readGraph(w.conversation.id, w.draft.runTarget ? 'run' : 'definition', w.draft.runTarget ?? w.draft.definition.id)
      .then(value => { if (live && epoch === getAuthEpoch()) setRemote(value.graph) }).catch(() => undefined)
    return () => { live = false }
  }, [w.target.id, w.remoteDefinition?.revision, w.remoteRun?.latest_graph_revision, epoch])
  const local = new Map(w.graph.nodes.map(node => [node.id, node]))
  return <section aria-label="流程编辑冲突" className="workflow-conflict">
    <p role="alert">{w.target.label}已有新版本，当前未提交编辑仍保留。请核对差异后处理。</p>
    {remote && <details><summary>查看差异</summary><ul>
      {remote.nodes.filter(node => !local.has(node.id) || JSON.stringify(local.get(node.id)) !== JSON.stringify(node)).map(node => <li key={node.id}>{local.has(node.id) ? '内容有差异' : '服务端新增'}：{node.title}</li>)}
      {w.graph.nodes.filter(node => !remote.nodes.some(other => other.id === node.id)).map(node => <li key={node.id}>仅本地保留：{node.title}</li>)}
      {JSON.stringify(remote.edges) !== JSON.stringify(w.graph.edges) && <li>执行连线有差异</li>}
      {JSON.stringify(remote.presentation ?? null) !== JSON.stringify(w.graph.presentation ?? null) && <li>展示阶段或分支名称有差异</li>}
    </ul></details>}
    <div className="flex gap-3"><button type="button" onClick={w.forkDraft}>另存本地草稿</button>
      <button type="button" disabled={w.busy} onClick={() => { if (confirm('采用服务端版本？当前编辑将先保留为恢复副本。')) void w.acceptRemote() }}>采用服务端版本</button></div>
  </section>
}
