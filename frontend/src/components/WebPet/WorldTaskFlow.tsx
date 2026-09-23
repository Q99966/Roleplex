import { useMemo } from 'react'
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, type NodeProps, type Node, type Viewport } from '@xyflow/react'
import type { WorldTask } from '../../api/worldOrchestrator'
import '@xyflow/react/dist/style.css'

export const worldStatus: Record<string, string> = { queued: '排队中', running: '执行中', waiting: '待处理', completed: '已完成',
  stopping: '正在停止', stopped: '已停止', failed: '未完成', interrupted: '已中断', pending_dispatch: '等待派发' }
type Data = { title: string; status: string; subtitle: string; selected: boolean }
function Card({ data }: NodeProps<Node<Data>>) {
  return <div className={`w-64 rounded-2xl border bg-panel p-4 shadow-sm ${data.selected ? 'border-indigo-500 ring-2 ring-indigo-100' : 'border-slate-700'}`}>
    <Handle type="target" position={Position.Left} /><p className="mb-2 text-[11px] text-slate-500">{data.subtitle}</p>
    <p className="line-clamp-3 text-sm font-semibold text-slate-200">{data.title}</p><p className={`mt-3 text-xs ${data.status === 'failed' ? 'text-red-600' : data.status === 'completed' ? 'text-emerald-700' : 'text-indigo-600'}`}>{worldStatus[data.status] ?? data.status}</p>
    <Handle type="source" position={Position.Right} />
  </div>
}
const nodeTypes = { world: Card }

/** 世界图只投影准确任务关系；群图编辑通过原工作流入口完成。 */
export function WorldTaskFlow({ task, selected, onSelect, conversationNames, viewport, onViewport }: { task: WorldTask; selected: string | null; onSelect: (id: string | null) => void; conversationNames: Record<number, string>; viewport?: Viewport; onViewport: (viewport: Viewport) => void }) {
  const nodes = useMemo(() => [{ id: task.id, type: 'world', position: { x: 40, y: Math.max(0, task.children.length - 1) * 90 },
    data: { title: task.title, status: task.status, subtitle: '世界任务', selected: selected === null } },
    ...task.children.map((child, index) => ({ id: child.id, type: 'world', position: { x: child.kind === 'replan' ? 800 : 420, y: index * 180 },
      data: { title: child.conversation_id ? conversationNames[child.conversation_id] ?? `会话 #${child.conversation_id}` : child.kind === 'memory' ? '保存世界约定' : '类型活动',
        status: child.status, subtitle: child.kind === 'group' ? '委派给群协调者' : child.kind === 'activity' ? '类型活动' : child.kind === 'replan' ? '局部调整' : '世界记忆', selected: selected === child.id } }))], [task, selected, conversationNames])
  return <div className="min-h-0 flex-1" aria-label="世界任务流程图"><ReactFlowProvider>
    <ReactFlow nodes={nodes} edges={task.children.map(child => ({ id: child.id,
      source: child.kind === 'replan' && typeof child.reference.target_child_id === 'string' && nodes.some(node => node.id === child.reference.target_child_id) ? child.reference.target_child_id : task.id,
      target: child.id, type: 'smoothstep' }))}
      nodeTypes={nodeTypes} nodesConnectable={false} nodesDraggable={false} fitView={!viewport} defaultViewport={viewport} onMoveEnd={(_, next) => onViewport(next)} minZoom={.25} maxZoom={1.5}
      onNodeClick={(_, node) => onSelect(node.id === task.id ? null : node.id)} onPaneClick={() => onSelect(null)}>
      <Background gap={24} /><Controls showInteractive={false} />
    </ReactFlow>
  </ReactFlowProvider></div>
}
