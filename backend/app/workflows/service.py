"""串行工作流控制：短事务保存状态，复用会话 worker 执行，不在等待确认时持锁。

当前产品单 worker；control_lock 只串行化流程控制，不是全库写锁。持久版本和唯一约束
保证请求重发不重复派发；进程重启标记中断，绝不从旧 queued 记录自动重放副作用。
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, func
from ..db import SessionLocal, with_locked_retry
from ..models import (WorkflowDefinition, WorkflowRun, WorkflowAttempt, Conversation, ConversationMember,
                      User, Role, WorkspaceBinding, Generation, AgentExecution, QueueJob, Message, WorkflowBudget)
from ..realtime import store as events
from ..realtime.events import current_epoch
from ..scheduling import conversation_scheduler
from ..config.logging import current_request_id
from ..runtime.models import RuntimeGate
from ..workspaces.service import binding_root
from .schemas import Save, Start, Control, serial_execution_graph

ACTIVE = ('queued', 'running', 'waiting', 'stopping')
control_lock = asyncio.Lock()
_task = None
_accepting = False
logger = logging.getLogger('roleplex.workflows')


def now():
    return datetime.now(timezone.utc)


def reject(code, status=409):
    raise HTTPException(status, code)


async def owned(session, cid, owner_id):
    """Owner、会话归属和实际成员都有效；不存在与越权统一隐藏。"""
    user = await session.get(User, owner_id)
    conv = await session.get(Conversation, cid)
    member = await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == cid,
        ConversationMember.member_type == 'user', ConversationMember.member_id == owner_id))
    if user is None or not user.is_owner or conv is None or conv.deleted_at or conv.created_by != owner_id or member is None:
        reject('CONVERSATION_NOT_FOUND', 404)
    return conv


async def validate_roles(session, conv, nodes, owner_id):
    """定义保存与每步派发都核验当前角色成员，不将快照视为永久授权。"""
    ids = {node['role_id'] for node in nodes if node['kind'] == 'role'}
    available = set((await session.scalars(select(Role.id).join(ConversationMember,
        (ConversationMember.member_type == 'role') & (ConversationMember.member_id == Role.id)).where(
        ConversationMember.conversation_id == conv.id, Role.created_by == owner_id,
        Role.active.is_(True), Role.deleted_at.is_(None), Role.id.in_(ids)))).all())
    if ids != available:
        reject('WORKFLOW_ROLE_UNAVAILABLE', 422)


async def resource_check(session, run):
    """运行与等待期间复核资源；群聊只复用文件工具，不扩大任何角色能力。"""
    conv = await owned(session, run.conversation_id, run.owner_id)
    gate = await session.get(RuntimeGate, 1)
    if gate and gate.closing:
        reject('WORKFLOW_WORLD_CLOSING')
    if conv.workspace_binding_id != run.workspace_binding_id:
        reject('WORKFLOW_RESOURCE_CHANGED')
    if run.workspace_binding_id is not None:
        binding = await session.get(WorkspaceBinding, run.workspace_binding_id)
        if (binding is None or not binding.active or binding.created_by != run.owner_id
            or binding.root_path != run.workspace_root):
            reject('WORKFLOW_RESOURCE_CHANGED')
        try:
            if str(binding_root(binding)) != run.workspace_root:
                reject('WORKFLOW_RESOURCE_CHANGED')
        except (OSError, ValueError):
            reject('WORKFLOW_RESOURCE_CHANGED')
    # 只校验尚将参与的角色，已完成旧角色的删除不使历史事实消失。
    await validate_roles(session, conv, run.snapshot['nodes'][run.cursor:], run.owner_id)
    from ..workspaces.tools import workspace_tool_policy
    for node in run.snapshot['nodes'][run.cursor:]:
        required = set(run.snapshot.get('capabilities', {}).get(node['id'], []))
        if not required:
            continue
        role = await session.get(Role, node['role_id'])
        policy = await workspace_tool_policy(session, conversation=conv, role=role, triggered_by_user_id=run.owner_id)
        if not required.issubset({tool['name'] for tool in policy['exposed_tools']}):
            reject('WORKFLOW_CAPABILITY_CHANGED')
    return conv


async def changed(session, run):
    """只发布可公开的状态/身份提示；私有定义和任务通过 Owner REST 获取。"""
    run.revision += 1
    run.updated_at = now()
    return await events.append_event(session, run.conversation_id, 'workflow_updated',
        {'run_id': run.id, 'status': run.status, 'revision': run.revision}, revision=run.revision)


def definition_json(row):
    return {'id': row.id, 'name': row.name, 'revision': row.revision, 'graph': row.graph}


async def run_json(session, run):
    """返回控制快照和原执行引用；文件详情继续由已有 Owner 接口读取。"""
    attempts = list((await session.scalars(select(WorkflowAttempt).where(WorkflowAttempt.run_id == run.id)
        .order_by(WorkflowAttempt.created_at, WorkflowAttempt.id))).all())
    result = []
    for a in attempts:
        generation = await session.get(Generation, a.generation_id) if a.generation_id else None
        from ..models import ModelCallUsage
        calls = list((await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id == a.execution_id))).all()) if a.execution_id else []
        usage = {field: sum(getattr(c, field) for c in calls) if calls and all(getattr(c, field) is not None for c in calls) else None
                 for field in ['input_tokens', 'output_tokens']}
        result.append({'id': a.id, 'node_id': a.node_id, 'number': a.number, 'status': a.status,
            'usage': usage, 'current': run.selected.get(a.node_id) == a.id, 'upstream_ids': a.upstream_ids,
            'retry_source_id': a.retry_source_id, 'instruction': a.instruction,
            'message_id': generation.assistant_message_id if generation else None,
            'generation_id': a.generation_id, 'execution_id': a.execution_id,
            'error_code': a.error_code, 'created_at': a.created_at, 'ended_at': a.ended_at})
    budget = await session.get(WorkflowBudget, run.chain_id)
    return {'id': run.id, 'definition_id': run.definition_id, 'definition_revision': run.definition_revision,
        'name': run.snapshot['name'], 'graph': {'nodes': run.snapshot['nodes'], 'edges': run.snapshot['edges']},
        'status': run.status, 'revision': run.revision, 'cursor': run.cursor, 'error_code': run.error_code,
        'workspace_binding_id': run.workspace_binding_id, 'input_text': run.input_text, 'attempts': result,
        'decision_limit': budget.decision_limit if budget else None,
        'used_decisions': budget.used_decisions if budget else None, 'created_at': run.created_at}


async def listing(cid, uid):
    """恢复当前 Owner 会话的控制快照，私有定义不进入公开事件。"""
    async with SessionLocal() as session:
        await owned(session, cid, uid)
        definitions = (await session.scalars(select(WorkflowDefinition).where(WorkflowDefinition.conversation_id == cid)
            .order_by(WorkflowDefinition.created_at))).all()
        runs = (await session.scalars(select(WorkflowRun).where(WorkflowRun.conversation_id == cid)
            .order_by(WorkflowRun.created_at.desc()))).all()
        return {'definitions': [definition_json(row) for row in definitions],
                'runs': [await run_json(session, row) for row in runs]}


async def save(cid, uid, did, payload: Save):
    """显式版本保存；新定义使用客户端生成的稳定 ID，重发不会产生重复定义。"""
    async with control_lock:
        async with SessionLocal() as session:
            conv = await owned(session, cid, uid)
            graph = payload.graph.model_dump(mode='json')
            await validate_roles(session, conv, graph['nodes'], uid)
            row = await session.get(WorkflowDefinition, did)
            if row and row.conversation_id != cid:
                reject('WORKFLOW_NOT_FOUND', 404)
            if (row.revision if row else 0) != payload.expected_revision:
                reject('WORKFLOW_REVISION_CONFLICT')
            if not payload.name.strip():
                reject('WORKFLOW_NODE_INVALID', 422)
            if row is None:
                row = WorkflowDefinition(id=did, conversation_id=cid, created_at=now(), revision=0)
                session.add(row)
            row.name, row.graph, row.updated_at = payload.name.strip(), graph, now()
            row.revision += 1
            await session.commit()
            return definition_json(row)


async def start(cid, uid, payload: Start):
    """启动键按会话唯一，运行与预算、触发消息同事务保存。"""
    async with control_lock:
        async with SessionLocal() as session:
            conv = await owned(session, cid, uid)
            digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
            existing = await session.scalar(select(WorkflowRun).where(WorkflowRun.conversation_id == cid,
                WorkflowRun.request_key == payload.request_key))
            if existing:
                if existing.request_digest != digest:
                    reject('WORKFLOW_REQUEST_CONFLICT')
                return await run_json(session, existing)
            definition = await session.get(WorkflowDefinition, payload.definition_id)
            if definition is None or definition.conversation_id != cid:
                reject('WORKFLOW_NOT_FOUND', 404)
            if definition.revision != payload.expected_revision:
                reject('WORKFLOW_REVISION_CONFLICT')
            if await session.scalar(select(WorkflowRun.id).where(WorkflowRun.conversation_id == cid, WorkflowRun.status.in_(ACTIVE))):
                reject('WORKFLOW_RUN_ACTIVE')
            executable = serial_execution_graph(definition.graph)
            run = WorkflowRun(id=uuid4().hex, conversation_id=cid, definition_id=definition.id, owner_id=uid,
                definition_revision=definition.revision, snapshot={'name': definition.name, 'request_id': current_request_id(), **executable},
                workspace_binding_id=conv.workspace_binding_id, workspace_root=None, chain_id=uuid4().hex,
                request_key=payload.request_key, request_digest=digest, input_text=payload.input_text,
                status='queued', revision=0, cursor=0, selected={}, rerun_downstream=True, created_at=now(), updated_at=now())
            if conv.workspace_binding_id:
                binding = await session.get(WorkspaceBinding, conv.workspace_binding_id)
                run.workspace_root = binding.root_path if binding else None
            await resource_check(session, run)
            from ..workspaces.tools import workspace_tool_policy
            capabilities = {}
            for node in run.snapshot['nodes']:
                if node['kind'] == 'role':
                    policy = await workspace_tool_policy(session, conversation=conv,
                        role=await session.get(Role, node['role_id']), triggered_by_user_id=uid)
                    capabilities[node['id']] = [tool['name'] for tool in policy['exposed_tools']]
            run.snapshot = {**run.snapshot, 'capabilities': capabilities}
            message = Message(conversation_id=cid, sender_type='user', sender_id=uid,
                parts_json=[{'type': 'text', 'text': f'启动工作流：{definition.name}\n{payload.input_text}'}],
                mentions_json=[], status='done', revision=0, chain_id=run.chain_id,
                meta_json={'workflow_run_id': run.id}, created_at=now())
            session.add(message); await session.flush()
            run.trigger_message_id = message.id
            from ..services.agent_budget import freeze
            await freeze(session, message)
            session.add(run); await session.flush()
            first = await new_attempt(session, run, run.snapshot['nodes'][0])
            run.selected = {first.node_id: first.id}
            from ..services.chat import message_payload
            ev = await events.append_event(session, cid, 'message_created', {'message': message_payload(message)}, revision=0)
            status_event = await changed(session, run)
            await session.commit()
            result = await run_json(session, run)
        await events.publish_events(ev, status_event)
        return result


async def get_run(session, cid, uid, rid):
    """解析同一会话和 Owner 的运行，隐藏跨资源存在性。"""
    await owned(session, cid, uid)
    row = await session.get(WorkflowRun, rid)
    if row is None or row.conversation_id != cid or row.owner_id != uid:
        reject('WORKFLOW_NOT_FOUND', 404)
    return row


async def facts(cid, uid, rid, aid):
    """按明确尝试核对原生文件事实；不把最近本角色回复当作选中节点。"""
    async with SessionLocal() as session:
        run = await get_run(session, cid, uid, rid)
        attempt = await session.get(WorkflowAttempt, aid)
        if attempt is None or attempt.run_id != rid:
            reject('WORKFLOW_ATTEMPT_NOT_FOUND', 404)
        await resource_check(session, run)
        return {'attempt_id': aid, 'text': await attempt_facts(session, run, attempt)}


async def attempt_facts(session, run, attempt):
    """复用加密文件证据的授权投影；没有可关联执行时明确未知。"""
    generation = await session.get(Generation, attempt.generation_id) if attempt.generation_id else None
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == attempt.execution_id)) if attempt.execution_id else None
    role = await session.get(Role, execution.role_id) if execution else None
    current = await session.get(Message, attempt.input_message_id) if attempt.input_message_id else None
    if generation is None or generation.assistant_message_id is None or role is None or current is None:
        return '没有可关联的文件提交证据；不能据此推断未执行。'
    conv = await owned(session, run.conversation_id, run.owner_id)
    await validate_roles(session, conv, [{'kind': 'role', 'role_id': role.id}], run.owner_id)
    from ..context.interruption import interruption_context
    return await interruption_context(session, conversation=conv, role=role, current=current,
        triggered_by_user_id=run.owner_id, source_message_id=generation.assistant_message_id, workflow_source=True) or '没有可用证据，结果保持未知。'


async def control(cid, uid, rid, payload: Control):
    """版本约束精确运行/尝试；停止先封闭派发，再异步收口当前执行。"""
    stop_chain = None
    async with control_lock:
        async with SessionLocal() as session:
            run = await get_run(session, cid, uid, rid)
            if run.revision != payload.expected_revision:
                reject('WORKFLOW_REVISION_CONFLICT')
            if payload.action == 'stop':
                if run.status not in ACTIVE:
                    return await run_json(session, run)
                run.status = 'stopping'
                stop_chain = run.chain_id
            elif payload.action == 'resume':
                if run.status in ACTIVE or run.cursor >= len(run.snapshot['nodes']):
                    reject('WORKFLOW_STATE_CONFLICT')
                if run.snapshot['nodes'][run.cursor]['id'] in run.selected:
                    reject('WORKFLOW_RETRY_REVIEW_REQUIRED')
                if await session.scalar(select(WorkflowRun.id).where(WorkflowRun.conversation_id == cid,
                    WorkflowRun.id != rid, WorkflowRun.status.in_(ACTIVE))):
                    reject('WORKFLOW_RUN_ACTIVE')
                for node in run.snapshot['nodes'][:run.cursor]:
                    prior = await session.get(WorkflowAttempt, run.selected.get(node['id'])) if node['id'] in run.selected else None
                    if prior is None or prior.status != 'completed':
                        reject('WORKFLOW_INPUT_UNAVAILABLE')
                await resource_check(session, run)
                run.status, run.error_code, run.rerun_downstream = 'queued', None, True
            else:
                await resource_check(session, run)
                a = await session.get(WorkflowAttempt, payload.attempt_id) if payload.attempt_id else None
                if a is None or a.run_id != rid or run.selected.get(a.node_id) != a.id:
                    reject('WORKFLOW_ATTEMPT_NOT_FOUND', 404)
                if payload.action == 'confirm':
                    if run.status != 'waiting' or a.status != 'waiting':
                        reject('WORKFLOW_STATE_CONFLICT')
                    a.status, a.ended_at = 'completed', now()
                    run.cursor += 1
                    run.status = 'queued' if run.rerun_downstream else 'stopped'
                else:
                    if run.status in ACTIVE or not payload.acknowledge_facts:
                        reject('WORKFLOW_RETRY_REVIEW_REQUIRED')
                    if await session.scalar(select(WorkflowRun.id).where(WorkflowRun.conversation_id == cid,
                        WorkflowRun.id != rid, WorkflowRun.status.in_(ACTIVE))):
                        reject('WORKFLOW_RUN_ACTIVE')
                    # 重做上游使下游旧结果失效；保留旧行，仅更换当前选择。
                    index = next(i for i, node in enumerate(run.snapshot['nodes']) if node['id'] == a.node_id)
                    if any(node not in run.selected for node in [n['id'] for n in run.snapshot['nodes'][:index]]):
                        reject('WORKFLOW_INPUT_UNAVAILABLE')
                    run.cursor = index
                    await resource_check(session, run)
                    run.selected = {n['id']: run.selected[n['id']] for n in run.snapshot['nodes'][:index]}
                    run.rerun_downstream = payload.rerun_downstream
                    new = await new_attempt(session, run, run.snapshot['nodes'][index], payload.instruction, a.id)
                    run.selected = {**run.selected, a.node_id: new.id}
                    run.status, run.error_code = 'queued', None
            ev = await changed(session, run)
            await session.commit()
            result = await run_json(session, run)
        await events.publish_events(ev)
        if stop_chain:
            await conversation_scheduler.stop_chain(cid, stop_chain)
    return result


async def new_attempt(session, run, node, instruction='', source=None):
    """按节点递增尝试号，固定所选上游；调用方持有控制锁并负责同事务提交。"""
    number = (await session.scalar(select(func.max(WorkflowAttempt.number)).where(
        WorkflowAttempt.run_id == run.id, WorkflowAttempt.node_id == node['id']))) or 0
    inputs = node['inputs']
    upstream = [run.selected[key] for key in inputs if key in run.selected]
    if len(upstream) != len(inputs):
        reject('WORKFLOW_INPUT_UNAVAILABLE')
    for aid in upstream:
        a = await session.get(WorkflowAttempt, aid)
        if a is None or a.status != 'completed':
            reject('WORKFLOW_INPUT_UNAVAILABLE')
    a = WorkflowAttempt(id=uuid4().hex, run_id=run.id, node_id=node['id'], number=number + 1,
        status='pending', upstream_ids=upstream, retry_source_id=source, instruction=instruction, created_at=now())
    session.add(a); await session.flush()
    return a


async def advance(rid):
    """一次短状态转移；派发在已提交尝试后进行，重复唤醒由原队列状态拒绝。"""
    enqueue = None
    stop = None
    evs = []
    async with control_lock:
        async with SessionLocal() as session:
            run = await session.get(WorkflowRun, rid)
            if run is None or run.status not in ACTIVE:
                return
            original_state = (run.status, run.error_code)
            nodes = run.snapshot['nodes']
            a = await session.get(WorkflowAttempt, run.selected.get(nodes[run.cursor]['id'])) if run.cursor < len(nodes) and nodes[run.cursor]['id'] in run.selected else None
            g = await session.get(Generation, a.generation_id) if a and a.generation_id else None
            if run.status != 'stopping':
                try:
                    await resource_check(session, run)
                except HTTPException as exc:
                    run.status, run.error_code = 'stopping', str(exc.detail)
                    stop = (run.conversation_id, run.chain_id)
            if run.status == 'stopping':
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == a.execution_id)) if a and a.execution_id else None
                if (g and g.status in ('queued', 'running')) or (execution and execution.status in ('queued', 'running')):
                    if g and g.stop_requested_at is None:
                        stop = (run.conversation_id, run.chain_id)
                else:
                    if a and a.status not in ('completed', 'failed', 'stopped', 'interrupted'):
                        a.status, a.ended_at = execution.status if execution else 'stopped', now()
                        a.error_code = execution.error_code if execution else None
                    run.status = 'blocked' if run.error_code else 'stopped'
                if original_state != (run.status, run.error_code):
                    evs.append(await changed(session, run))
            elif run.cursor >= len(nodes):
                run.status = 'completed'
                evs.append(await changed(session, run))
            elif a and a.generation_id:
                execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == a.execution_id))
                # generation 终态早于 lease/finally 与队列终态；等待原 execution 收口再交接。
                if execution and execution.status in ('completed', 'failed', 'stopped', 'interrupted'):
                    a.status, a.error_code, a.ended_at = execution.status, execution.error_code, now()
                    if a.status == 'completed':
                        run.cursor += 1
                        run.status = 'queued' if run.rerun_downstream else 'stopped'
                    else:
                        run.status, run.error_code = a.status, a.error_code
                    evs.append(await changed(session, run))
                elif g and g.status == 'queued':
                    job = await session.scalar(select(QueueJob.id).where(QueueJob.generation_id == g.id, QueueJob.status == 'queued'))
                    if job:
                        enqueue = (run.conversation_id, job)
                elif a.status != 'running':
                    a.status = 'running'
                    evs.append(await changed(session, run))
            elif run.status != 'waiting':
                node = nodes[run.cursor]
                if a is None:
                    a = await new_attempt(session, run, node)
                    run.selected = {**run.selected, node['id']: a.id}
                if node['kind'] == 'approval':
                    a.status, run.status = 'waiting', 'waiting'
                else:
                    task = '\n'.join(part for part in [run.input_text, node['task'],
                        ('预期产出：' + node['expected_output']) if node['expected_output'] else '', a.instruction] if part)
                    message = Message(conversation_id=run.conversation_id, sender_type='user', sender_id=run.owner_id,
                        parts_json=[{'type': 'text', 'text': task}], mentions_json=[node['role_id']],
                        status='done', revision=0, chain_id=run.chain_id, meta_json={'workflow_run_id': run.id, 'workflow_attempt_id': a.id}, created_at=now())
                    session.add(message); await session.flush()
                    g = Generation(conversation_id=run.conversation_id, stream_epoch=current_epoch(), status='queued', run_id=run.chain_id)
                    session.add(g); await session.flush()
                    conv = await session.get(Conversation, run.conversation_id)
                    e = AgentExecution(execution_id=uuid4().hex, conversation_id=run.conversation_id, generation_id=g.id,
                        chain_id=run.chain_id, role_id=node['role_id'], execution_kind='single' if conv.type == 'single' else 'group_role',
                        attempt=a.number, status='queued', created_at=now())
                    session.add(e)
                    job = QueueJob(conversation_id=run.conversation_id, generation_id=g.id, status='queued',
                        payload_json={'current_message_id': message.id, 'triggered_by_user_id': run.owner_id,
                            'allow_dangerous': True, 'request_id': run.snapshot.get('request_id')}, attempts=0, cancel_requested=False, created_at=now())
                    session.add(job); await session.flush()
                    a.input_message_id, a.generation_id, a.execution_id, a.status = message.id, g.id, e.execution_id, 'queued'
                    run.status = 'running'
                    conv.last_message_at = now()
                    enqueue = (run.conversation_id, job.id)
                    from ..services.chat import message_payload
                    evs.append(await events.append_event(session, run.conversation_id, 'message_created', {'message': message_payload(message)}, revision=0))
                evs.append(await changed(session, run))
            await session.commit()
        if evs:
            await events.publish_events(*evs)
        if stop:
            await conversation_scheduler.stop_chain(*stop)
        if enqueue and _accepting:
            await conversation_scheduler.enqueue(*enqueue)


async def permit_generation(gid):
    """worker 实际启动前复核流程仍允许该尝试，不因入队时的授权绕过撤销。"""
    async with control_lock:
        async with SessionLocal() as session:
            a = await session.scalar(select(WorkflowAttempt).where(WorkflowAttempt.generation_id == gid))
            if a is None:
                return True
            run = await session.get(WorkflowRun, a.run_id)
            if run is None or run.status != 'running' or run.selected.get(a.node_id) != a.id:
                return False
            try:
                await resource_check(session, run)
                return True
            except HTTPException:
                return False


async def initialize():
    """恢复展示，不恢复执行；人工等待可继续但继续前重新授权。"""
    global _task, _accepting, control_lock
    control_lock = asyncio.Lock()
    async with SessionLocal() as session:
        runs = (await session.scalars(select(WorkflowRun).where(WorkflowRun.status.in_(('queued', 'running', 'stopping'))))).all()
        for run in runs:
            run.status, run.error_code = 'interrupted', 'WORKFLOW_INTERRUPTED'
            run.revision += 1
            rows = (await session.scalars(select(WorkflowAttempt).where(WorkflowAttempt.run_id == run.id,
                WorkflowAttempt.status.in_(('pending', 'queued', 'running'))))).all()
            for a in rows:
                a.status, a.error_code, a.ended_at = 'interrupted', 'WORKFLOW_INTERRUPTED', now()
        await session.commit()
    _accepting = True
    _task = asyncio.create_task(coordinate())


async def shutdown():
    global _task, _accepting
    _accepting = False
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None


async def coordinate():
    """已提交状态驱动有限轮询；失败只记录固定安全事件，不输出任务或异常原文。"""
    while True:
        try:
            async with SessionLocal() as session:
                ids = list((await session.scalars(select(WorkflowRun.id).where(WorkflowRun.status.in_(ACTIVE)))).all())
            for rid in ids:
                try:
                    await with_locked_retry(lambda: advance(rid))
                except asyncio.CancelledError:
                    raise
                except HTTPException as exc:
                    async with control_lock:
                        async with SessionLocal() as session:
                            row = await session.get(WorkflowRun, rid)
                            if row and row.status in ACTIVE:
                                row.status, row.error_code = 'blocked', str(exc.detail)
                                ev = await changed(session, row)
                                await session.commit()
                                await events.publish_events(ev)
                except Exception:
                    logger.warning('workflow.coordination_failed', extra={'error_code': 'WORKFLOW_STATE_UNAVAILABLE', 'status': 'failed'})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('workflow.state_unavailable', extra={'error_code': 'WORKFLOW_STATE_UNAVAILABLE', 'status': 'failed'})
        await asyncio.sleep(.2)
