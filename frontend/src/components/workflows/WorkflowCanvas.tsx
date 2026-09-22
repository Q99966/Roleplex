import { lazy, Suspense, useEffect, useRef, type ReactNode } from 'react'
import { useAppStore } from '../../store/app'
import { useWorkflow } from './WorkflowContext'
import { WorkflowToolbar } from './WorkflowToolbar'
import { WorkflowAttemptDetails } from './WorkflowAttemptDetails'
import { WorkflowConflict } from './WorkflowConflict'
import './workflow-workbench.css'
const WorkflowFlow = lazy(() => import('./WorkflowFlow').then(module => ({ default: module.WorkflowFlow })))

/** 画布覆盖消息与输入，聊天保持挂载和布局；inert 阻止隐藏区抢焦点。 */
export function WorkflowSurface({ children }: { children: ReactNode }) {
  const w = useWorkflow()
  const chat = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (chat.current) chat.current.inert = w.open
    if (!w.open) return
    const previous = document.activeElement as HTMLElement | null
    canvas.current?.focus()
    return () => {
      if (previous?.isConnected) previous.focus()
      else document.querySelector<HTMLButtonElement>('[aria-label="打开会话详情"]')?.focus()
    }
  }, [w.open])
  return <div className="relative min-h-0 flex-1">
    <div ref={chat} aria-hidden={w.open || undefined} className={`flex h-full min-h-0 flex-col ${w.open ? 'invisible pointer-events-none' : ''}`}>{children}</div>
    {w.open && <div ref={canvas} tabIndex={-1} role="region" aria-label="工作流画布" className="absolute inset-0 z-20 flex min-h-0 flex-col bg-slate-950 outline-none">
      <WorkflowCanvas />
    </div>}
  </div>
}

function WorkflowCanvas() {
  const w = useWorkflow()
  const roles = useAppStore(state => state.roles).filter(role => w.conversation.role_ids.includes(role.id) && role.active)
  const owner = useAppStore(state => state.user?.is_owner)
  if (!owner) return <p>工作流由 Owner 管理。</p>
  return <div className="flex h-full min-h-0 flex-col" onKeyDown={event => {
    if (event.key !== 'Escape') return
    event.stopPropagation()
    if (w.detailAttempt) w.selectAttempt(null)
    else if (w.selected || w.selectedEdge || w.feedbackFocusId) { w.selectNode(null); w.selectEdge('') }
    else w.setOpen(false)
  }}>
    <WorkflowToolbar />
    {w.target.editable && w.conflict && <WorkflowConflict />}
    <Suspense fallback={<p role="status" className="flex-1 p-4 text-xs text-slate-500">正在加载画布…</p>}>
      <WorkflowFlow key={`${w.mode}:${w.mode === 'run' ? w.run?.id : w.draft.runTarget ?? w.draft.definition.id}`} graph={w.graph} run={w.mode === 'run' ? w.run : null} editable={w.target.editable}
        selected={w.selected} onSelect={w.selectNode} selectedEdge={w.selectedEdge} onSelectEdge={w.selectEdge}
        roleNames={Object.fromEntries(roles.map(role => [role.id, role.name]))}
        onGraphChange={w.changeGraph} onInsertAfter={id => w.addNode('role', id)} />
    </Suspense>
    {w.detailAttempt && w.run?.attempts.find(attempt => attempt.id === w.detailAttempt) && <WorkflowAttemptDetails key={w.detailAttempt} attempt={w.run.attempts.find(attempt => attempt.id === w.detailAttempt)!} onClose={() => w.selectAttempt(null)} />}
  </div>
}
