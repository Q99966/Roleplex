import { useEffect, useRef, useState } from 'react'
import { ChevronDown, MoreHorizontal, PanelRight, Play, Plus, Save, Square } from 'lucide-react'
import { useWorkflow, statusLabel } from './WorkflowContext'
import { unfinished, workflowRunStatus } from './workflow-target'

/** 主要动作随准确对象变化；历史视图只提供返回和查看，不复用当前运行的控制按钮。 */
export function WorkflowToolbar() {
  const w = useWorkflow(), { target } = w
  const [menu, setMenu] = useState(false), root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const active = Boolean(w.run && ['queued', 'running', 'waiting', 'stopping'].includes(w.run.status))
  const appointed = w.conversation.type === 'group' && w.conversation.orchestrator_enabled && (w.conversation.orchestrator_revision ?? 0) > 0
  const disabled = w.busy || !w.localReady
  const pending = w.data.coordinations?.filter(c => (target.kind === 'template' ? c.definition_id === target.id && !c.run_id : c.run_id === target.id || c.started_run_id === target.id)
    && ['queued', 'running', 'stopping'].includes(c.status)) ?? []
  const feedback = w.run?.feedback?.filter(item => !['resolved', 'dismissed', 'accepted', 'obsolete'].includes(item.status)
    && (!w.historyGraph || item.graph_revision === w.historyGraph.revision)).length ?? 0
  const value = `${target.kind}:${target.id}${target.kind === 'history' ? ':' + target.version : ''}`
  const local = w.localStatus === 'saved' ? '本地已保存' : w.localStatus === 'error' ? '本地保存失败' : '本地保存中'
  useEffect(() => {
    if (!menu) return
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus()
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setMenu(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [menu])
  useEffect(() => setMenu(false), [value])
  function action(fn: () => unknown) { setMenu(false); trigger.current?.focus(); void fn() }
  const add = (kind: 'role' | 'approval' | 'join' | 'condition' | 'judge') => {
    const id = w.addNode(kind, w.selected ?? undefined)
    if (id) w.revealInspector('context')
  }
  return <div role="toolbar" aria-label="工作流操作" className="workflow-toolbar-shell">
    <div className="workflow-toolbar">
      <div className="workflow-object">
        <div className="workflow-object-heading"><span className={`workflow-object-kind kind-${target.kind}`}>{target.label}</span>
          <span>{target.kind === 'template' && !target.version ? '新草稿' : target.version != null ? `版本 ${target.version}` : w.run ? `模板版本 ${w.run.definition_revision}` : '读取版本…'}</span></div>
        <select aria-label="工作流对象" value={value} disabled={disabled} onChange={e => {
          const chosen = e.target.value
          if (chosen === 'new') void w.choose()
          else if (chosen.startsWith('draft:')) w.resumeDraft()
          else if (chosen.startsWith('template:')) void w.choose(w.data.definitions.find(item => item.id === chosen.slice(9)))
          else if (chosen.startsWith('run:')) void w.selectRun(chosen.slice(4))
        }}>
          <option value="new">＋ 新建模板</option>
          {target.kind === 'template' && !w.data.definitions.some(d => d.id === target.id) && <option value={value}>{target.name}</option>}
          {target.kind !== 'template' && !w.draft.runTarget && !w.data.definitions.some(d => d.id === w.draft.definition.id) && <option value={'draft:' + w.draft.definition.id}>未提交草稿：{w.draft.definition.name}</option>}
          {['run-edit', 'history'].includes(target.kind) && <option value={value}>{target.name} · {target.label}</option>}
          {w.mode === 'run' && !w.run && target.kind !== 'history' && <option value={value}>正在读取运行</option>}
          <optgroup label="流程模板">{w.data.definitions.map(d => <option key={d.id} value={'template:' + d.id}>{target.kind === 'template' && d.id === w.draft.definition.id ? w.draft.definition.name : d.name}</option>)}</optgroup>
          <optgroup label="运行记录">{w.data.runs.map(r => <option key={r.id} value={'run:' + r.id}>{r.name} · {workflowRunStatus(r, statusLabel)} · {r.id.slice(0, 6)}</option>)}</optgroup>
        </select>
      </div>
      <div className="workflow-main-actions">
        {target.editable && <button type="button" className={w.draft.dirty || !w.draft.definition.revision || target.kind === 'run-edit' ? 'is-primary' : ''}
          disabled={disabled || w.conflict || (!w.draft.dirty && Boolean(w.draft.definition.revision))} onClick={() => void w.save()}>
          <Save size={14} />{target.kind === 'run-edit' ? '提交本次运行调整' : '保存模板'}</button>}
        {target.kind === 'template' && <>
          <select aria-label="运行方式" value={w.startMode} disabled={disabled} onChange={e => w.setStartMode(e.target.value as 'manual' | 'coordinated')}>
            <option value="manual">手动运行</option><option value="coordinated" disabled={!appointed}>协调执行</option>
          </select>
          <button type="button" className={!w.draft.dirty && w.draft.definition.revision ? 'is-primary' : ''} disabled={disabled || w.conflict || w.draft.dirty || !w.draft.definition.revision || (w.startMode === 'coordinated' && !appointed)}
            onClick={() => void w.start(w.startMode)}><Play size={14} />启动流程</button>
        </>}
        {target.kind === 'run' && w.run && (active
          ? <button type="button" className="is-stop" disabled={disabled || w.run.status === 'stopping'} onClick={() => void w.control({ action: 'stop' })}><Square size={13} />停止运行</button>
          : <button type="button" className="is-primary" disabled={disabled} onClick={() => void (unfinished(w.run!) ? w.control({ action: 'resume' }) : w.newRun())}>{unfinished(w.run) ? '继续剩余步骤' : '准备新运行'}</button>)}
        {target.kind === 'history' && <button type="button" className="is-primary" disabled={disabled || !w.run} onClick={() => void w.selectHistory('')}>返回当前运行</button>}
        <button type="button" onClick={() => w.revealInspector('context')}><PanelRight size={14} />详情</button>
        {w.mode === 'run' && <button type="button" onClick={() => w.revealInspector('feedback')}>反馈{feedback ? ` ${feedback}` : ''}</button>}
        <button type="button" onClick={() => w.revealInspector('records')}>记录</button>
        <div ref={root} className="workflow-more">
          <button ref={trigger} type="button" aria-label="更多操作" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(value => !value)}><MoreHorizontal size={17} /><ChevronDown size={12} /></button>
          {menu && <div ref={menuRef} role="menu" aria-label="工作流更多操作" onKeyDown={event => {
            if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMenu(false); trigger.current?.focus() }
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
              event.preventDefault()
              const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)'))
              const index = items.indexOf(document.activeElement as HTMLButtonElement)
              items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length]?.focus()
            }
          }}>
            {target.editable && <>
              {(['role', 'approval', 'join', 'condition', 'judge'] as const).map(kind => <button type="button" role="menuitem" key={kind} disabled={disabled} onClick={() => action(() => add(kind))}><Plus size={13} />{({ role: '添加角色任务', approval: '添加人工确认', join: '添加结果汇合', condition: '添加条件选择', judge: '添加模型判断' })[kind]}</button>)}
              <button type="button" role="menuitem" onClick={() => action(() => w.revealInspector('advanced'))}>高级设置</button>
            </>}
            {target.kind !== 'history' && <button type="button" role="menuitem" disabled={disabled || !appointed} onClick={() => action(() => w.requestCoordination())}>{target.kind === 'template' ? '让协调者规划' : '让协调者调整运行'}</button>}
            {target.kind === 'run' && w.run?.runtime_version === 2 && <button type="button" role="menuitem" disabled={disabled || ['stopping', 'stopped'].includes(w.run.status)} onClick={() => action(w.editRun)}>编辑本次运行图</button>}
            {target.kind === 'run-edit' && <button type="button" role="menuitem" disabled={disabled} onClick={() => action(() => w.selectRun(target.id))}>查看本次运行</button>}
            {target.kind !== 'template' && target.kind !== 'history' && <button type="button" role="menuitem" disabled={disabled || !target.templateId} onClick={() => action(w.editDefinition)}>编辑原模板</button>}
            <button type="button" role="menuitem" onClick={() => action(() => w.revealInspector('drafts'))}>草稿与恢复</button>
            <button type="button" role="menuitem" onClick={() => action(w.refresh)}>刷新工作流</button>
          </div>}
        </div>
        <button type="button" onClick={() => w.setOpen(false)}>返回对话</button>
      </div>
    </div>
    <div className="workflow-status-line" role="status">
      {!w.localReady ? '正在恢复工作流…' : target.editable ? `${w.draft.dirty || !w.draft.definition.revision ? '尚未提交' : '已提交'} · ${local}` : `${workflowRunStatus(w.run, statusLabel)}${target.kind === 'history' ? ' · 历史图只读' : ''}`}
      {w.mode === 'run' && w.run?.pending_graph_revision && <span> · 另有调整等待循环边界采用</span>}
      {!!pending.length && <button type="button" onClick={() => w.revealInspector('coordination')}>协调处理中 {pending.length}</button>}
      {w.localStatus === 'error' && <button type="button" onClick={() => w.revealInspector('drafts')}>查看草稿恢复</button>}
    </div>
    {w.error && <p role="alert" className="workflow-operation-error">{w.error}</p>}
    {w.graphNotice && <p className="workflow-operation-note">{w.graphNotice}</p>}
  </div>
}
