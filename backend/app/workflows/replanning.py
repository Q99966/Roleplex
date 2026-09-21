"""运行图修订：活跃尝试冻结，未派发节点即时更新，活动循环在轮次边界采用。"""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..models import WorkflowActivation, WorkflowAttempt, WorkflowGraphRevision
from . import service
from .graph_service import GRAPH_FIELDS, normalize, compile_checked, structure, problem


def loop_changes(before, after):
    """节点内容或域内依赖改变也属于循环修订，不能只比较 loops 字段。"""
    old={l['id']:l for l in before.get('loops',[])};new={l['id']:l for l in after.get('loops',[])}
    old_nodes={n['id']:structure(n) for n in before['nodes']};new_nodes={n['id']:structure(n) for n in after['nodes']}
    changed=set()
    for lid,loop in old.items():
        body=set(loop['body'])
        old_edges=[e for e in before['edges'] if set(e)&body];new_edges=[e for e in after['edges'] if set(e)&body]
        old_rules=[r for r in before.get('edge_rules',[]) if r['source'] in body];new_rules=[r for r in after.get('edge_rules',[]) if r['source'] in body]
        if (new.get(lid)!=loop or sorted(old_edges)!=sorted(new_edges) or old_rules!=new_rules
            or any(old_nodes.get(n)!=new_nodes.get(n) for n in body)):
            changed.add(lid)
    return changed


async def edit_scope(session,run):
    from .engine import current_activations
    rows=list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id==run.id))).all())
    current=current_activations(run,rows)
    return {'effective_graph_revision':run.graph_revision,'pending_graph_revision':run.state_json.get('pending_graph_revision'),
        'frozen_nodes':[nid for nid,a in current.items() if not nid.startswith('__') and a.status not in ('pending','waiting_feedback','skipped','dormant')],
        'future_loop_ids':[l['id'] for l in run.snapshot.get('loops',[]) if not run.state_json['loops'][l['id']].get('exited')],
        'rules':'已派发/等待确认/已处理节点的内容和入边保持冻结；可新增后继。活动循环修改在未来轮次边界采用。'}


async def prepare(session,run,before,after,*,target_is_effective=True):
    from .engine import current_activations
    if run.status in ('stopping','stopped'): service.reject('WORKFLOW_STATE_CONFLICT')
    effective=normalize({k:run.snapshot[k] for k in GRAPH_FIELDS if k in run.snapshot})
    compiled=compile_checked(after)
    from ..config import settings
    compiled['concurrency']=after.get('concurrency') or settings.workflow_parallelism
    if after['runtime_version']!=2: service.reject('WORKFLOW_VERSION_REQUIRED',422)
    execution_shape = lambda graph: {key: [structure(node) for node in graph['nodes']] if key == 'nodes' else graph.get(key)
        for key in GRAPH_FIELDS if key != 'presentation'}
    if target_is_effective and execution_shape(before) == execution_shape(after):
        # 同一执行设计只改变展示：保留启动时容量、已接受分工、当前 phase 和所有激活。
        # 最新目标若为尚未采用/已取消的业务修订，仍须走正常重规划，不能借标签提交采用它。
        compiled['concurrency'] = run.snapshot['concurrency']
        return {'compiled': compiled, 'assignments': run.state_json.get('assignments', {}), 'deferred_loops': [], 'display_only': True}
    rows=list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id==run.id))).all())
    current=current_activations(run,rows)
    changed_loops=loop_changes(effective,after)
    from .feedback import blockers
    _, feedback_loops, _ = await blockers(session, run, current)
    new_loops = {loop['id']: loop for loop in after.get('loops', [])}
    deferred=set()
    for loop in effective.get('loops',[]):
        started=any(current.get(n) and current[n].status not in ('pending','waiting_feedback','dormant','skipped') for n in loop['body'])
        if loop['id'] in changed_loops and started:
            if loop['id'] in feedback_loops:
                proposed = new_loops.get(loop['id'], {})
                # 反馈已挡住本轮交接，不能再把处置节点延迟到下一轮造成互等。
                # 保持循环边界，只调整未派发区域；下方仍逐项冻结已有执行及其入边。
                if {k:v for k,v in loop.items() if k != 'body'} != {k:v for k,v in proposed.items() if k != 'body'}:
                    problem('WORKFLOW_FEEDBACK_LOOP_BOUNDARY', fields=['loops', loop['id']])
                continue
            if run.state_json['loops'][loop['id']].get('exited'): problem('WORKFLOW_GRAPH_FROZEN',fields=['loops',loop['id']])
            if any(current.get(n) and current[n].status in ('failed','blocked','stopped','interrupted') for n in loop['body']):
                problem('WORKFLOW_RETRY_REVIEW_REQUIRED',fields=['loops',loop['id']])
            deferred.add(loop['id'])
    old_nodes={n['id']:n for n in effective['nodes']};new_nodes={n['id']:n for n in after['nodes']}
    for nid,activation in current.items():
        if nid.startswith('__') or activation.status in ('pending','waiting_feedback','dormant','skipped'): continue
        if activation.loop_id in deferred: continue
        incoming=lambda graph:sorted(e for e in graph['edges'] if e[1]==nid)
        if (nid not in new_nodes or structure(old_nodes[nid])!=structure(new_nodes[nid]) or incoming(effective)!=incoming(after)
            or run.snapshot.get('loop_membership',{}).get(nid)!=compiled['loop_membership'].get(nid)):
            problem('WORKFLOW_GRAPH_FROZEN',node_id=nid)
    # 默认工具只在已有分配上冻结；新/修改的未派发任务按最新授权检查。
    from .engine import manual_assignments
    conv=await service.owned(session,run.conversation_id,run.owner_id)
    assignments=await manual_assignments(session,conv,after['nodes'],run.owner_id,run.snapshot.get('coordinator_role_id'))
    for nid,old in run.state_json.get('assignments',{}).items():
        if nid in new_nodes and nid in old_nodes and structure(new_nodes[nid])==structure(old_nodes[nid]):
            assignments[nid]=old
    if run.snapshot.get('mode')=='coordinated':
        for node in after['nodes']:
            if node['kind']=='judge' and node.get('role_id') not in (None,run.snapshot.get('coordinator_role_id')):
                problem('ORCHESTRATOR_PLAN_INVALID',node_id=node['id'])
    return {'compiled':compiled,'assignments':assignments,'deferred_loops':sorted(deferred)}


async def apply_version(session,run,version,prepared,*,advance_loops=None):
    """在一个短事务内更换有效图和选择集合；旧 activation/attempt 永不改成新事实。"""
    from .engine import current_activations,create_activation
    old_graph=copy.deepcopy(run.snapshot)
    rows=list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id==run.id))).all())
    current=current_activations(run,rows)
    state=run.state_json
    state.setdefault('activation_selection',{}).update({nid:a.id for nid,a in current.items()})
    old_nodes={n['id']:n for n in old_graph['nodes']}
    graph=prepared['compiled'];advance_loops=advance_loops or {}
    old_loops={l['id']:l for l in old_graph.get('loops',[])}
    old_states=copy.deepcopy(state.get('loops',{}))
    state['assignments']=prepared['assignments']
    for lid in old_states:
        if not any(l['id']==lid for l in graph['loops']): state['loops'][lid]={**old_states[lid],'retired':True,'exited':True}
    for loop in graph['loops']:
        lid=loop['id']
        advance_from=[source for source in advance_loops if source==lid or set(old_loops[source]['body'])&set(loop['body'])]
        if advance_from:
            iteration=max(old_states[source]['iteration']+1 for source in advance_from)
            state['loops'][lid]={'iteration':iteration,'exited':False,'handled':None}
        elif lid not in old_states or old_states[lid].get('retired'):
            state['loops'][lid]={'iteration':0,'exited':False,'handled':None}
    run.snapshot={**run.snapshot,**graph}
    run.graph_revision=version.number
    run.state_json=state
    new_nodes={n['id']:n for n in graph['nodes']}
    for nid,node in new_nodes.items():
        lid=graph['loop_membership'].get(nid)
        prior=current.get(nid)
        iteration=state['loops'][lid]['iteration'] if lid else 0
        advanced=bool(lid and (lid in advance_loops or state['loops'][lid]['iteration']!=old_states.get(lid,{}).get('iteration',0)))
        modified=nid in old_nodes and structure(node)!=structure(old_nodes[nid])
        # 当前循环整体进入新轮；从被移除循环转为普通节点时，仅显式改变的任务创建新激活。
        retired_changed=bool(prior and prior.loop_id in advance_loops and not lid and modified)
        if prior and prior.status not in ('pending','waiting_feedback','dormant','skipped') and not advanced and not retired_changed:
            state['activation_selection'][nid]=prior.id
            continue
        if prior and prior.status in ('pending','waiting_feedback','skipped','dormant') and not advanced:
            if prior.selected_attempt_id:
                attempt=await session.get(WorkflowAttempt,prior.selected_attempt_id)
                if attempt and attempt.execution_id: problem('WORKFLOW_GRAPH_FROZEN',node_id=nid)
                if attempt and attempt.status=='pending': attempt.status='superseded';attempt.ended_at=service.now()
            prior.status='superseded'
        if retired_changed: iteration=max(a.iteration for a in rows if a.node_id==nid)+1
        activation=await create_activation(session,run,nid,iteration,lid)
        state['activation_selection'][nid]=activation.id
    for nid,a in current.items():
        if nid not in new_nodes and not nid.startswith('__'):
            state['activation_selection'].pop(nid,None)
            if a.status in ('pending','waiting_feedback','skipped','dormant'):
                a.status='superseded'
                if a.selected_attempt_id:
                    attempt=await session.get(WorkflowAttempt,a.selected_attempt_id)
                    if attempt and attempt.status=='pending': attempt.status='superseded';attempt.ended_at=service.now()
    # 旧汇总属于旧图；下一次汇总新建 attempt，原消息和结果保留。
    if '__summary' in current:
        summary=current['__summary']
        if summary.status=='active': problem('WORKFLOW_GRAPH_FROZEN',node_id='__summary')
        state['activation_selection'].pop('__summary',None)
    state['phase']='work'
    state.pop('pending_graph_revision',None);state.pop('pending_graph_loops',None);state.pop('pending_boundaries',None)
    run.state_json=copy.deepcopy(state)
    flag_modified(run,'state_json')
    if run.status in service.ACTIVE: run.status='queued'
    version.status='applied'


async def commit(session,run,version,prepared):
    prior=run.state_json.get('pending_graph_revision')
    if prior:
        old=await session.scalar(select(WorkflowGraphRevision).where(WorkflowGraphRevision.run_id==run.id,WorkflowGraphRevision.number==prior))
        if old: old.status='superseded'
    if prepared.get('display_only'):
        from .engine import current_activations
        rows = list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id == run.id))).all())
        current = current_activations(run, rows)
        run.state_json = {**run.state_json, 'activation_selection': {**run.state_json.get('activation_selection', {}), **{nid: row.id for nid, row in current.items()}}}
        run.snapshot = {**run.snapshot, **prepared['compiled']}
        run.graph_revision = version.number
        version.status = 'applied'
        return
    if prepared['deferred_loops']:
        version.status='pending'
        run.state_json={**run.state_json,'pending_graph_revision':version.number,'pending_graph_loops':prepared['deferred_loops'],'pending_boundaries':{}}
    else: await apply_version(session,run,version,prepared)


async def at_boundary(session,run,state,rows):
    """只有受影响循环都交接完本轮且选择重复时，才原子采用未来轮次的新图。"""
    pending=state.get('pending_graph_revision')
    if not pending or run.status=='stopping': return False
    from .engine import current_activations
    current=current_activations(run,rows)
    version=await session.scalar(select(WorkflowGraphRevision).where(WorkflowGraphRevision.run_id==run.id,WorkflowGraphRevision.number==pending))
    if not version: service.reject('WORKFLOW_GRAPH_REVISION_NOT_FOUND')
    waiting=state.get('pending_graph_loops',[]);boundaries=state.setdefault('pending_boundaries',{})
    bad=any(a.loop_id in waiting and a.status in ('blocked','failed','stopped','interrupted') for a in current.values())
    exited=any(not b['repeat'] for b in boundaries.values())
    if bad or exited:
        version.status='not_applied'
        version.changes_json={**version.changes_json,'reason':'WORKFLOW_DEPENDENCY_BLOCKED' if bad else 'WORKFLOW_GRAPH_LOOP_ENDED'}
        state.pop('pending_graph_revision',None);state.pop('pending_graph_loops',None);state.pop('pending_boundaries',None)
        return False
    if not set(waiting).issubset(boundaries): return False
    old_loops={l['id']:l for l in run.snapshot['loops']}
    for loop in version.graph.get('loops',[]):
        sources=[lid for lid in waiting if lid==loop['id'] or set(old_loops[lid]['body'])&set(loop['body'])]
        if sources and loop.get('max_iterations') is not None:
            next_round=max(state['loops'][lid]['iteration']+1 for lid in sources)
            if next_round>=loop['max_iterations']:
                version.status='not_applied';version.changes_json={**version.changes_json,'reason':'WORKFLOW_LOOP_LIMIT'}
                for lid in sources: state['loops'][lid]['limited']=True
                state.pop('pending_graph_revision',None);state.pop('pending_graph_loops',None);state.pop('pending_boundaries',None)
                run.error_code='WORKFLOW_LOOP_LIMIT'
                return False
    # 提交时的授权不代替实际采用时复核；权限失效保持旧图及已执行事实。
    from .graph_service import validate_authority
    try:
        from .graph_service import validate_constraints
        validate_constraints(version.graph,run.snapshot.get('constraints',{}))
        if version.source_execution_id:
            from ..models import CoordinationSession
            from .coordination import coordinator
            grant=await session.scalar(select(CoordinationSession).where(CoordinationSession.execution_id==version.source_execution_id))
            if not grant or grant.status not in ('queued','running','completed') or grant.run_id!=run.id or grant.owner_id!=run.owner_id or grant.conversation_id!=run.conversation_id:
                service.reject('WORKFLOW_COORDINATION_REVOKED',403)
            conv=await service.owned(session,run.conversation_id,run.owner_id)
            await coordinator(session,conv,expected_id=grant.role_id,expected_revision=grant.appointment_revision)
            validate_constraints(version.graph,grant.constraints_json)
        await validate_authority(session,run.conversation_id,run.owner_id,version.graph)
        compiled=compile_checked(version.graph)
        from ..config import settings
        compiled['concurrency']=version.graph.get('concurrency') or settings.workflow_parallelism
        from .engine import manual_assignments
        conv=await service.owned(session,run.conversation_id,run.owner_id)
        assignments=await manual_assignments(session,conv,version.graph['nodes'],run.owner_id,run.snapshot.get('coordinator_role_id'))
    except HTTPException as exc:
        reason=exc.detail if isinstance(exc.detail,str) else exc.detail['code']
        await cancel_pending(session,run,reason,state=state)
        return False
    old_nodes={n['id']:n for n in run.snapshot['nodes']}
    for node in version.graph['nodes']:
        nid=node['id']
        if nid in old_nodes and structure(old_nodes[nid])==structure(node) and nid in state.get('assignments',{}): assignments[nid]=state['assignments'][nid]
    prepared={'compiled':compiled,'assignments':assignments}
    await apply_version(session,run,version,prepared,advance_loops={lid:boundaries[lid] for lid in waiting})
    state.clear();state.update(copy.deepcopy(run.state_json));run.state_json=state
    return True


async def cancel_pending(session,run,reason,*,state=None,source_execution_id=None):
    """只关闭尚未采用的修订；已采用图及文件事实不可回滚。"""
    values=state if state is not None else copy.deepcopy(run.state_json)
    number=values.get('pending_graph_revision')
    if not number: return
    version=await session.scalar(select(WorkflowGraphRevision).where(WorkflowGraphRevision.run_id==run.id,WorkflowGraphRevision.number==number))
    if source_execution_id and (not version or version.source_execution_id!=source_execution_id): return
    if version:
        version.status='not_applied';version.changes_json={**version.changes_json,'reason':reason}
    values.pop('pending_graph_revision',None);values.pop('pending_graph_loops',None);values.pop('pending_boundaries',None)
    if state is None: run.state_json=values
