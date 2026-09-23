"""世界目标关联已有群协调；子链独立，根预算与取消范围由宿主维护。"""
import asyncio
import json
import logging
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update

from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import (AgentExecution, Conversation, ConversationMember, CoordinationSession, Generation, Message,
    QueueJob, WorkflowBudget, WorkflowDefinition, WorkflowRun, WorldCoordinationGrant, WorldOrchestrator, WorldTask, WorldTaskChild)
from ..context.fingerprint import stable_hash
from ..context.domain import ContextBuildError
from ..realtime import store as events
from ..realtime.events import current_epoch
from . import service

TERMINAL = {'completed', 'stopped', 'failed', 'interrupted'}
_worker = None
_wake = asyncio.Event()
logger = logging.getLogger('roleplex.world.tasks')


async def create_in_session(session, execution, message, uid, appointment_revision):
    """在原用户消息事务中冻结世界目标与普通群范围，不派发子任务。"""
    groups = list((await session.scalars(select(Conversation.id).join(ConversationMember,
        ConversationMember.conversation_id == Conversation.id).where(Conversation.created_by == uid,
        Conversation.type == 'group', Conversation.purpose == 'chat', Conversation.deleted_at.is_(None),
        ConversationMember.member_type == 'user', ConversationMember.member_id == uid))).all())
    text = next((p.get('text', '') for p in message.parts_json if p.get('type') == 'text'), '')
    task = WorldTask(id=uuid4().hex, owner_id=uid, conversation_id=execution.conversation_id,
        trigger_message_id=message.id, root_execution_id=execution.execution_id, chain_id=execution.chain_id,
        role_id=execution.role_id, appointment_revision=appointment_revision, title=text[:160],
        scope_json={'group_ids': groups}, status='queued', revision=0, created_at=now_utc(), updated_at=now_utc())
    session.add(task); await session.flush()
    return task


async def owned(session, task_id, uid):
    row = await session.get(WorldTask, task_id)
    if not row or row.owner_id != uid:
        raise HTTPException(404, 'WORLD_TASK_NOT_FOUND')
    return row


async def continue_in_session(session, payload, uid, appointment):
    """补充要求进入同一任务的下一个空闲协调回合，原根额度和目标范围不重置。"""
    task = await owned(session, payload.world_task_id, uid)
    if payload.world_task_mode != 'execute' or task.status in TERMINAL | {'stopping'} or task.appointment_revision != appointment.revision:
        raise HTTPException(409, 'WORLD_TASK_STATE_CONFLICT')
    if payload.expected_task_revision != task.revision:
        raise HTTPException(409, 'WORLD_TASK_REVISION_CONFLICT')
    if await session.scalar(select(AgentExecution.id).join(WorldCoordinationGrant,
        WorldCoordinationGrant.execution_id == AgentExecution.execution_id).where(WorldCoordinationGrant.task_id == task.id,
        AgentExecution.status.in_(['queued', 'running'])).limit(1)):
        raise HTTPException(409, 'WORLD_TASK_COORDINATOR_BUSY')
    budget = await session.get(WorkflowBudget, task.chain_id)
    if budget.decision_limit is not None and budget.used_decisions >= budget.decision_limit:
        raise HTTPException(409, 'WORLD_TASK_BUDGET_EXCEEDED')
    return task


async def authorized(session, execution_id, *, writing=False):
    """把本次 grant 解析到所属任务；写操作额外拒绝终态与停止中。"""
    grant = await service.authorized(session, execution_id)
    if not grant.task_id:
        raise HTTPException(403, 'WORLD_TASK_EXECUTION_REQUIRED')
    task = await owned(session, grant.task_id, grant.owner_id)
    if task.appointment_revision != grant.appointment_revision or writing and task.status in TERMINAL | {'stopping'}:
        raise HTTPException(409, 'WORLD_TASK_STATE_CONFLICT')
    return grant, task


async def child_allowed(session, chain_id):
    """普通子群的每次模型/工具准入仍核对其世界根授权，旧任命不得借子链继续。"""
    budget = await session.get(WorkflowBudget, chain_id)
    if budget is None or budget.root_chain_id is None:
        return True
    child = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.chain_id == chain_id))
    task = await session.get(WorldTask, child.task_id) if child else None
    state = await session.get(WorldOrchestrator, 1)
    return bool(task and state and task.status not in TERMINAL | {'stopping'} and
        state.revision == task.appointment_revision and state.role_id == task.role_id and
        budget.root_chain_id == task.chain_id)


async def attach_child(session, child_id, coordination, execution):
    """群请求提交/排队之前固定父执行和预算归属；只供宿主服务调用。"""
    child = await session.get(WorldTaskChild, child_id)
    if not child or child.coordination_id or child.status != 'pending_dispatch':
        raise HTTPException(409, 'WORLD_TASK_REQUEST_CONFLICT')
    _, task = await authorized(session, child.parent_execution_id, writing=True)
    if child.task_id != task.id or child.conversation_id != coordination.conversation_id:
        raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
    budget = await session.get(WorkflowBudget, coordination.chain_id)
    if child.kind == 'replan':
        target = await session.get(WorldTaskChild, child.reference_json['target_child_id'])
        if not target or not budget or target.task_id != task.id or coordination.chain_id != target.chain_id or budget.root_chain_id != task.chain_id:
            raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
        execution.parent_execution_id = child.parent_execution_id
        child.coordination_id = coordination.id; child.status = 'queued'
        return
    if not budget or budget.conversation_id != child.conversation_id or budget.root_chain_id:
        raise HTTPException(409, 'WORLD_TASK_REQUEST_CONFLICT')
    budget.root_chain_id = task.chain_id
    execution.parent_execution_id = child.parent_execution_id
    child.coordination_id = coordination.id; child.chain_id = coordination.chain_id; child.status = 'queued'


async def children(session, task):
    """从原群运行/协调记录投影子任务；源不可读时只保留身份和执行状态。"""
    rows = (await session.scalars(select(WorldTaskChild).where(WorldTaskChild.task_id == task.id).order_by(WorldTaskChild.created_at))).all()
    result = []
    for child in rows:
        coordination = await session.get(CoordinationSession, child.coordination_id) if child.coordination_id else None
        run_id = (coordination.started_run_id or coordination.run_id) if coordination else None
        run = await session.get(WorkflowRun, run_id) if run_id else None
        status = run.status if run else coordination.status if coordination else child.status
        if not coordination and child.kind != 'memory' and status == 'queued':
            status = 'interrupted'  # 原群记录被清理后不伪装为仍可派发。
        if coordination and (child.kind == 'replan' or run and run.status == 'completed' and coordination.status != 'completed'):
            status = coordination.status
        if status in {'blocked', 'interrupted'}:
            status = 'failed'
        reports, feedback = [], []
        group = await session.get(Conversation, child.conversation_id) if child.conversation_id else None
        accessible = child.kind == 'memory' or bool(group and not group.deleted_at and group.created_by == task.owner_id and
            await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == group.id,
                ConversationMember.member_type == 'user', ConversationMember.member_id == task.owner_id)))
        if run and accessible:
            from ..models import WorkflowAttempt, WorkflowActivation, WorkflowFeedback
            attempts = (await session.scalars(select(WorkflowAttempt).join(WorkflowActivation,
                WorkflowActivation.selected_attempt_id == WorkflowAttempt.id).where(WorkflowAttempt.run_id == run.id)
                .order_by(WorkflowAttempt.created_at.desc()).limit(20))).all()
            reports = [{'attempt_id': a.id, 'node_id': a.node_id, 'status': a.status,
                'graph_revision': a.graph_revision, 'result': a.result_json} for a in attempts]
            opinions = (await session.scalars(select(WorkflowFeedback).where(WorkflowFeedback.run_id == run.id,
                WorkflowFeedback.status.not_in(['resolved', 'dismissed', 'accepted', 'obsolete'])).order_by(WorkflowFeedback.created_at.desc()).limit(20))).all()
            feedback = [{'id': f.id, 'category': f.category, 'summary': f.summary, 'blocking': f.blocking,
                'status': f.status, 'revision': f.revision, 'attempt_id': f.attempt_id} for f in opinions]
        result.append({'id': child.id, 'kind': child.kind, 'conversation_id': child.conversation_id,
            'coordination_id': child.coordination_id, 'run_id': run.id if run else None, 'chain_id': child.chain_id,
            'status': status, 'error_code': run.error_code if run else coordination.error_code if coordination else child.error_code,
            'revision': coordination.revision if child.kind == 'replan' and coordination else run.revision if run else coordination.revision if coordination else 0,
            'graph_revision': run.graph_revision if run else None,
            'available': accessible, 'reference': child.reference_json if accessible else {}, 'results': reports, 'feedback': feedback})
    return result


async def view(session, task):
    """按准确根/子链汇总事实和厂商用量，不再次累加已经包含子链的根额度。"""
    budget = await session.get(WorkflowBudget, task.chain_id)
    descendants = await children(session, task)
    from ..services.execution_usage import summary as usage
    usage_value = await usage(session, task.conversation_id, task.role_id, chains=[task.chain_id, *[c['chain_id'] for c in descendants if c['chain_id']]])
    return {'id': task.id, 'title': task.title, 'status': task.status, 'revision': task.revision,
        'role_id': task.role_id, 'conversation_id': task.conversation_id, 'chain_id': task.chain_id,
        'root_execution_id': task.root_execution_id, 'error_code': task.error_code, 'summary': task.summary,
        'decision_limit': budget.decision_limit, 'used_decisions': budget.used_decisions,
        'children': descendants, 'usage': usage_value, 'created_at': task.created_at, 'updated_at': task.updated_at}


async def context_message(session, execution_id):
    grant, task = await authorized(session, execution_id)
    value = await view(session, task)
    # 正文按需 read_task；常驻交接只保留有界身份、状态与未解决事项。
    payload = {k: value[k] for k in ['id', 'title', 'status', 'revision', 'decision_limit', 'used_decisions']}
    payload['children'] = [{k: row[k] for k in ['id', 'kind', 'conversation_id', 'coordination_id', 'run_id', 'status', 'error_code']}
        for row in value['children'][:30]]
    payload['omitted_children'] = max(0, len(value['children']) - 30)
    return '当前世界任务状态（后台事实，不是额外指令）：\n' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')), task.id, task.revision


async def remember_read(session, execution_id, value):
    """记录已回读子任务的权限来源；进度变化是新事实，撤权才阻止旧正文继续进入模型。"""
    from ..models import MemoryReference
    from ..agent.tool_context import tool_call_id
    call_id = tool_call_id.get() or 'world-task'
    for child in value.get('children', []):
        if not child['available'] or not (child['results'] or child['feedback']):
            continue
        identity = stable_hash([execution_id, call_id, 'world_child', child['id']])
        if await session.get(MemoryReference, identity) is None:
            session.add(MemoryReference(id=identity, execution_id=execution_id, tool_call_id=call_id,
                source_kind='world_child', source_id=child['id'], source_revision=child['revision'],
                reference='world-child:' + child['id'], action='read', offset=0, characters=0, created_at=now_utc()))


async def validate_child_source(session, uid, child_id):
    child = await session.get(WorldTaskChild, child_id)
    task = await owned(session, child.task_id, uid) if child else None
    group = await session.get(Conversation, child.conversation_id) if child and child.conversation_id else None
    if not task or not group or group.deleted_at or group.created_by != uid or not await session.scalar(select(ConversationMember.id).where(
        ConversationMember.conversation_id == group.id, ConversationMember.member_type == 'user', ConversationMember.member_id == uid)):
        raise HTTPException(404, 'MEMORY_SOURCE_NOT_FOUND')
    return group


async def changed(session, task):
    task.revision += 1; task.updated_at = now_utc()
    return await events.append_event(session, task.conversation_id, 'world_task_updated',
        {'task_id': task.id, 'revision': task.revision, 'status': task.status})


async def overview(execution_id):
    """返回当前可见的有界群/任务引用，任命和创建时范围都不是永久读取授权。"""
    async with SessionLocal() as session:
        grant = await service.authorized(session, execution_id)
        task = await owned(session, grant.task_id, grant.owner_id) if grant.task_id else None
        groups = (await session.scalars(select(Conversation).join(ConversationMember, ConversationMember.conversation_id == Conversation.id).where(
            Conversation.id.in_(task.scope_json['group_ids']) if task else Conversation.type == 'group',
            ConversationMember.member_type == 'user', ConversationMember.member_id == grant.owner_id,
            Conversation.deleted_at.is_(None), Conversation.created_by == grant.owner_id).order_by(Conversation.id).limit(50))).all()
        result = []
        for group in groups:
            definition = await session.scalar(select(WorkflowDefinition).where(WorkflowDefinition.conversation_id == group.id)
                .order_by(WorkflowDefinition.updated_at.desc()).limit(1))
            result.append({'id': group.id, 'title': group.title, 'coordinator_role_id': group.orchestrator_role_id if group.orchestrator_enabled else None,
                'definition_id': definition.id if definition else None, 'definition_revision': definition.revision if definition else None})
        recent = (await session.scalars(select(WorldTask).where(WorldTask.owner_id == grant.owner_id).order_by(WorldTask.created_at.desc()).limit(20))).all()
        budget = await session.get(WorkflowBudget, task.chain_id) if task else None
        return {'task_id': task.id if task else None, 'groups': result,
            'tasks': [{'id': row.id, 'title': row.title, 'status': row.status, 'revision': row.revision} for row in recent],
            'budget_remaining': None if not budget or budget.decision_limit is None else max(0, budget.decision_limit - budget.used_decisions)}


async def delegate(execution_id, payload):
    """认领幂等请求后通过原群规划服务派发；丢失结果按同一协调键核对，不重建任务。"""
    digest = stable_hash(payload.model_dump())
    async with service.control_lock, SessionLocal() as session:
        grant, task = await authorized(session, execution_id, writing=True)
        if payload.conversation_id not in task.scope_json['group_ids']:
            raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
        child = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.task_id == task.id, WorldTaskChild.request_key == payload.request_key))
        if child:
            if child.request_digest != digest:
                raise HTTPException(409, 'WORLD_TASK_REQUEST_CONFLICT')
            return next(row for row in await children(session, task) if row['id'] == child.id)
        group = await session.get(Conversation, payload.conversation_id)
        if not group or group.deleted_at or group.created_by != task.owner_id or group.type != 'group' or not group.orchestrator_enabled or not group.orchestrator_role_id:
            raise HTTPException(409, 'ORCHESTRATOR_REQUIRED')
        child = WorldTaskChild(id=uuid4().hex, task_id=task.id, parent_execution_id=execution_id,
            request_key=payload.request_key, request_digest=digest, kind='group', conversation_id=group.id,
            status='pending_dispatch', reference_json={}, created_at=now_utc())
        session.add(child); await session.flush()
        target_role_id = group.orchestrator_role_id
        pending = await changed(session, task)
        await session.commit()
    await events.publish_events(pending)
    from ..workflows.graph_schemas import Coordinate
    from ..workflows import planning
    try:
        await planning.start(payload.conversation_id, grant.owner_id, Coordinate(role_id=target_role_id,
            mode=payload.mode, goal=payload.goal, definition_id=payload.definition_id,
            expected_graph_revision=payload.expected_graph_revision, request_key='world-' + child.id), world_child_id=child.id)
    except BaseException:
        # 调度已提交而响应丢失时，先核对本次唯一协调身份；不自动再发有副作用请求。
        async with SessionLocal() as session:
            row = await session.get(WorldTaskChild, child.id)
            if not row.coordination_id and row.status == 'pending_dispatch':
                row.status = 'failed'; row.error_code = 'WORLD_TASK_DISPATCH_FAILED'
                await session.commit()
        raise
    _wake.set()
    async with SessionLocal() as session:
        return next(row for row in await children(session, task) if row['id'] == child.id)


async def read(execution_id):
    async with SessionLocal() as session:
        _, task = await authorized(session, execution_id)
        return await view(session, task)


async def replan(execution_id, payload):
    """将局部调整交给原子群运行，复用预算和事实；同键不重新派发。"""
    async with service.control_lock, SessionLocal() as session:
        grant, task = await authorized(session, execution_id, writing=True)
        target = await session.get(WorldTaskChild, payload.child_id)
        if not target or target.task_id != task.id or target.kind not in {'group', 'activity'} or not target.coordination_id:
            raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
        coordination = await session.get(CoordinationSession, target.coordination_id)
        run = await session.get(WorkflowRun, coordination.started_run_id) if coordination else None
        group = await session.get(Conversation, target.conversation_id)
        if not run or not group or not group.orchestrator_enabled or group.deleted_at:
            raise HTTPException(409, 'WORLD_TASK_STATE_CONFLICT')
        digest = stable_hash(payload.model_dump())
        old = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.task_id == task.id, WorldTaskChild.request_key == payload.request_key))
        if old:
            if old.request_digest != digest: raise HTTPException(409, 'WORLD_TASK_REQUEST_CONFLICT')
            return next(c for c in await children(session, task) if c['id'] == old.id)
        child = WorldTaskChild(id=uuid4().hex, task_id=task.id, parent_execution_id=execution_id,
            request_key=payload.request_key, request_digest=digest, kind='replan', conversation_id=group.id,
            status='pending_dispatch', reference_json={'target_child_id': target.id, 'run_id': run.id}, created_at=now_utc())
        session.add(child); await session.flush()
        role_id = group.orchestrator_role_id
        await session.commit()
    from ..workflows.planning import start
    from ..workflows.graph_schemas import Coordinate
    try:
        await start(group.id, grant.owner_id, Coordinate(role_id=role_id, mode='replan', goal=payload.goal,
            run_id=run.id, expected_graph_revision=payload.expected_graph_revision, request_key='world-' + child.id), world_child_id=child.id)
    except BaseException:
        async with SessionLocal() as session:
            row = await session.get(WorldTaskChild, child.id)
            if not row.coordination_id and row.status == 'pending_dispatch':
                row.status = 'failed'; row.error_code = 'WORLD_TASK_DISPATCH_FAILED'; await session.commit()
        raise
    async with SessionLocal() as session:
        return next(c for c in await children(session, task) if c['id'] == child.id)


async def stop_child(task_id, child_id, uid, revision):
    """停止准确子运行或重规划回合，先核对版本；原群独立工作不受影响。"""
    from ..workflows import service as groups
    # 与群派发的认领事务串行，避免“尚未派发”的停止在 attach_child 之后才提交。
    async with groups.control_lock, SessionLocal() as session:
        task = await owned(session, task_id, uid)
        row = next((c for c in await children(session, task) if c['id'] == child_id), None)
        if row is None: raise HTTPException(404, 'WORLD_TASK_NOT_FOUND')
        if row['status'] in TERMINAL: return row
        if row['revision'] != revision: raise HTTPException(409, 'WORLD_TASK_REVISION_CONFLICT')
        if row['coordination_id'] is None:
            await session.execute(update(WorldTaskChild).where(WorldTaskChild.id == child_id,
                WorldTaskChild.coordination_id.is_(None), WorldTaskChild.status == 'pending_dispatch').values(status='stopped'))
            await session.commit()
            return {**row, 'status': 'stopped'}
        if row['kind'] == 'replan':
            coordination = await session.get(CoordinationSession, row['coordination_id'])
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == coordination.execution_id))
            from ..scheduling import conversation_scheduler
            generation_id = execution.generation_id
            await session.rollback()
            await conversation_scheduler.stop_generations(row['conversation_id'], [generation_id])
            return row
    from ..scheduling import conversation_scheduler
    if row['run_id']:
        from ..workflows import service as groups
        from ..workflows.schemas import Control
        await groups.control(row['conversation_id'], uid, row['run_id'], Control(action='stop', expected_revision=revision))
    if row['chain_id']: await conversation_scheduler.stop_chain(row['conversation_id'], row['chain_id'])
    return row


async def wait(execution_id, seconds):
    """等待已有子任务反馈，不为等待重复调用模型；停止/撤权立即结束等待。"""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        value = await read(execution_id)
        if not value['children'] or all(c['status'] in TERMINAL | {'waiting'} for c in value['children']) or asyncio.get_running_loop().time() >= deadline:
            return value
        await asyncio.sleep(.2)


async def complete(execution_id, summary):
    """只有子事实全部成功且阻塞反馈已处理时，才允许把世界目标标为完成。"""
    async with service.control_lock, SessionLocal() as session:
        _, task = await authorized(session, execution_id, writing=True)
        descendants = await children(session, task)
        if not descendants or any(c['status'] != 'completed' or any(f['blocking'] for f in c['feedback']) for c in descendants):
            raise HTTPException(409, 'WORLD_TASK_CHILDREN_UNFINISHED')
        task.status = 'completed'; task.summary = summary
        pending = await changed(session, task)
        await session.commit()
    await events.publish_events(pending)
    return {'task_id': task.id, 'status': task.status}


async def stop(task_id, uid, expected_revision=None):
    """先持久封闭派发，再取消精确任务树；重复停止继续核对真实收口。"""
    async with service.control_lock, SessionLocal() as session:
        task = await owned(session, task_id, uid)
        if task.status in TERMINAL:
            return await view(session, task)
        if expected_revision is not None and task.revision != expected_revision:
            raise HTTPException(409, 'WORLD_TASK_REVISION_CONFLICT')
        already_stopping = task.status == 'stopping'
        task.status = 'stopping'
        descendants = await children(session, task)
        pending = None if already_stopping else await changed(session, task)
        await session.commit()
    if pending:
        await events.publish_events(pending)
    from ..scheduling import conversation_scheduler
    from ..workflows import service as groups
    from ..workflows.schemas import Control
    for child in descendants:
        if child['run_id'] and child['status'] not in TERMINAL:
            try:
                await groups.control(child['conversation_id'], uid, child['run_id'], Control(action='stop', expected_revision=child['revision']))
            except HTTPException:
                pass  # 运行版本可能正收口；下轮根据实际状态继续取消关联链。
        if child['chain_id']:
            await conversation_scheduler.stop_chain(child['conversation_id'], child['chain_id'])
    await conversation_scheduler.stop_chain(task.conversation_id, task.chain_id)
    _wake.set()
    async with SessionLocal() as session:
        return await view(session, await owned(session, task_id, uid))


async def tick():
    """合并子状态/反馈，去重唤醒空闲协调回合；所有续办沿用根预算。"""
    pending, to_stop, to_enqueue = [], [], []
    async with service.control_lock, SessionLocal() as session:
        tasks = list((await session.scalars(select(WorldTask).where(WorldTask.status.not_in(TERMINAL)))).all())
        appointment = await session.get(WorldOrchestrator, 1)
        for task in tasks:
            before = task.status
            executions = (await session.execute(select(AgentExecution, Generation).join(Generation,
                Generation.id == AgentExecution.generation_id).join(WorldCoordinationGrant,
                WorldCoordinationGrant.execution_id == AgentExecution.execution_id).where(WorldCoordinationGrant.task_id == task.id))).all()
            active = any(e.status in {'queued', 'running'} for e, g in executions)
            pending_children = (await session.scalars(select(WorldTaskChild).where(WorldTaskChild.task_id == task.id,
                WorldTaskChild.coordination_id.is_(None), WorldTaskChild.status == 'pending_dispatch'))).all()
            active_ids = {e.execution_id for e, g in executions if e.status in {'queued', 'running'}}
            for child in pending_children:
                if child.parent_execution_id not in active_ids:
                    child.status = 'interrupted'; child.error_code = 'WORLD_TASK_DISPATCH_INTERRUPTED'
            await session.flush()
            descendants = await children(session, task)
            revoked = not appointment or appointment.revision != task.appointment_revision or appointment.role_id != task.role_id
            stopped = any(g.status == 'stopped' for e, g in executions)
            failed = any(g.status == 'failed' for e, g in executions)
            if revoked or stopped or task.status == 'stopping':
                if active or any(c['status'] not in TERMINAL for c in descendants):
                    to_stop.append((task.id, task.owner_id))
                    task.status = 'stopping'
                else:
                    task.status = 'failed' if task.error_code else 'stopped'
            elif failed:
                task.status = 'stopping'; task.error_code = next((g.error_code for e, g in executions if g.error_code), 'WORLD_TASK_EXECUTION_FAILED')
                to_stop.append((task.id, task.owner_id))
            elif active:
                task.status = 'running'
            else:
                task.status = 'waiting'
                signature = stable_hash([[c['id'], c['status'], c['run_id'], [[f['id'], f['revision']] for f in c['feedback']]] for c in descendants])
                meaningful = bool(descendants) and all(c['status'] in TERMINAL | {'waiting'} for c in descendants)
                budget = await session.get(WorkflowBudget, task.chain_id)
                if not descendants and budget.decision_limit is not None and budget.used_decisions >= budget.decision_limit:
                    task.status = 'failed'; task.error_code = 'WORLD_TASK_BUDGET_EXCEEDED'
                if meaningful and signature != task.feedback_hash:
                    task.feedback_hash = signature
                    if budget.decision_limit is not None and budget.used_decisions >= budget.decision_limit:
                        task.status = 'stopping'; task.error_code = 'WORLD_TASK_BUDGET_EXCEEDED'
                        to_stop.append((task.id, task.owner_id))
                    else:
                        to_enqueue.append((task.conversation_id, await _enqueue_continuation(session, task)))
                        task.status = 'queued'
            if task.status != before:
                pending.append(await changed(session, task))
        await session.commit()
    if pending: await events.publish_events(*pending)
    from ..scheduling import conversation_scheduler
    for cid, job_id in to_enqueue:
        await conversation_scheduler.enqueue_parallel(cid, job_id)
    for tid, uid in to_stop:
        await stop(tid, uid)


async def initialize():
    """恢复未完任务为中断，建立当前宿主的状态协调循环，不重放动作。"""
    global _worker, _wake
    # 每个应用生命周期拥有自己的异步原语；测试/托管重启不得复用旧事件循环的锁。
    service.control_lock = asyncio.Lock()
    _wake = asyncio.Event()
    # 进程恢复沿已有执行中断事实收口，不能在没有用户请求时重放派发。
    async with SessionLocal() as session:
        await session.execute(update(WorldTask).where(WorldTask.status.not_in(TERMINAL)).values(status='interrupted', error_code='EXECUTION_INTERRUPTED'))
        await session.commit()
    async def run():
        last_error = None
        while True:
            try:
                await tick()
                last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                from ..agent.argument_errors import safe_exception_type
                kind = safe_exception_type(exc)
                if kind != last_error:
                    logger.warning('world_task.reconcile_failed', extra={'error_type': kind, 'error_code': 'WORLD_TASK_RECONCILE_FAILED'})
                    last_error = kind
            _wake.clear()
            try: await asyncio.wait_for(_wake.wait(), .3)
            except TimeoutError: pass
    _worker = asyncio.create_task(run())


async def _enqueue_continuation(session, task):
    """反馈在无在途协调回合时续接一次，复用根消息/预算，来源和任命均不重置。"""
    generation = Generation(conversation_id=task.conversation_id, stream_epoch=current_epoch(), status='queued', run_id=task.chain_id)
    session.add(generation); await session.flush()
    execution = AgentExecution(execution_id=uuid4().hex, parent_execution_id=task.root_execution_id,
        conversation_id=task.conversation_id, generation_id=generation.id, chain_id=task.chain_id,
        role_id=task.role_id, execution_kind='world_coord', status='queued', created_at=now_utc())
    session.add(execution); await session.flush()
    session.add(WorldCoordinationGrant(execution_id=execution.execution_id, conversation_id=task.conversation_id,
        owner_id=task.owner_id, role_id=task.role_id, appointment_revision=task.appointment_revision, task_id=task.id, created_at=now_utc()))
    current_message_id = await session.scalar(select(Message.id).where(Message.conversation_id == task.conversation_id,
        Message.chain_id == task.chain_id, Message.sender_type == 'user').order_by(Message.id.desc()).limit(1))
    job = QueueJob(conversation_id=task.conversation_id, generation_id=generation.id, status='queued',
        payload_json={'current_message_id': current_message_id or task.trigger_message_id, 'triggered_by_user_id': task.owner_id,
            'allow_dangerous': True, 'parallel_workflow': True}, attempts=0, cancel_requested=False, created_at=now_utc())
    session.add(job); await session.flush()
    # 调用方提交事务后才通知执行器；重启按中断收口，不盲目重放。
    return job.id


async def shutdown():
    """等待状态协调循环退出；实际执行沿宿主原调度器关闭。"""
    global _worker
    if _worker:
        _worker.cancel(); await asyncio.gather(_worker, return_exceptions=True); _worker = None
