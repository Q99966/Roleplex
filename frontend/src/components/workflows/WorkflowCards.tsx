import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { Bot, GitBranch, GitMerge, Hand, BrainCircuit, Layers, Repeat2, ArrowRight } from 'lucide-react'
import type { ViewGroup, groupSummary } from './workflow-projection'

export type WorkflowCardData = {
  title: string; index: number; role: string; status: string; color: string; kind: string; editable: boolean;
  tone: 'neutral' | 'active' | 'waiting' | 'failed' | 'completed'; dimmed: boolean; feedbackCount: number;
  group?: ViewGroup; summary?: ReturnType<typeof groupSummary>; loopText?: string; membersText?: string; expand?: () => void;
}
export type WorkflowCardNode = Node<WorkflowCardData, 'task'>
const icons = { role: Bot, approval: Hand, join: GitMerge, condition: GitBranch, judge: BrainCircuit }
export const kindNames: Record<string, string> = { role: '角色任务', approval: '人工确认', join: '结果汇合', condition: '条件选择', judge: '模型判断' }

/** 类型、角色与真实状态分别展示，分组摘要不把完成执行说成验收通过。 */
export function WorkflowCard({ data, selected, isConnectable }: NodeProps<WorkflowCardNode>) {
  const Icon = data.group ? data.group.kind === 'loop' ? Repeat2 : Layers : icons[data.kind as keyof typeof icons] ?? Bot
  return <div className={`workflow-task ${data.group ? 'workflow-stage' : ''} workflow-kind-${data.kind} ${selected ? 'is-selected' : ''}`}
    data-dimmed={data.dimmed || undefined} style={{ borderTopColor: data.color, backgroundColor: `${data.color}09` }}>
    <Handle type="target" position={Position.Left} id="in" isConnectable={isConnectable} aria-label={`${data.title} 输入连接点`} />
    <div className="workflow-card-heading"><span className="workflow-kind-icon"><Icon size={16} /></span>
      <span title={data.group ? data.membersText : data.role}>{data.group ? ({ phase: '阶段', parallel: '并行区域', loop: '循环区域' })[data.group.kind] : data.role}</span>
      {!data.group && <span className="workflow-card-number" title={`定位编号 ${data.index + 1}，执行顺序由连线决定`}>#{data.index + 1}</span>}
    </div>
    <strong className="workflow-card-title" title={data.title}>{data.title}</strong>
    {data.group ? <>
      <p className="workflow-card-caption" title={data.membersText}>{data.summary?.total} 个节点 · {data.membersText}</p>
      {data.loopText && <p className="workflow-loop-caption">{data.loopText}</p>}
      <div className="workflow-group-states">
        {!!data.summary?.active && <span data-tone="active">运行 {data.summary.active}</span>}
        {!!data.summary?.waiting && <span data-tone="waiting">等待 {data.summary.waiting}</span>}
        {!!data.summary?.failed && <span data-tone="failed">失败 / 受阻 {data.summary.failed}</span>}
        {!!data.summary?.stopped && <span data-tone="neutral">已停止 {data.summary.stopped}</span>}
        {!!data.summary?.feedback && <span data-tone="waiting">待处理反馈 {data.summary.feedback}</span>}
        {!data.editable && !!data.summary?.completed && <span data-tone="completed">执行结束 {data.summary.completed}</span>}
      </div>
      <button type="button" className="nodrag nopan workflow-expand" onClick={data.expand} aria-label={`展开${data.group.kind === 'loop' ? '循环' : '阶段'}：${data.title}`}>展开查看 <ArrowRight size={13} /></button>
    </> : <>
      <p className="workflow-card-caption">{kindNames[data.kind]}</p>
      {!data.editable && <p className="workflow-node-state" data-tone={data.tone}>{data.status}</p>}
      {!!data.feedbackCount && <span className="workflow-feedback-badge">待处理反馈 {data.feedbackCount}</span>}
    </>}
    <Handle type="source" position={Position.Right} id="out" isConnectable={isConnectable} aria-label={`${data.title} 输出连接点`} />
  </div>
}
