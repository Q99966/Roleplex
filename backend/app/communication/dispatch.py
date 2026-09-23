"""同图、同轮和同依赖阶段的公开派发批次；运行槽位不改变广播身份。"""
from sqlalchemy import select
from fastapi import HTTPException
from ..db import now_utc
from ..models import (WorkflowDispatchBatch, WorkflowActivation, WorkflowAttempt, WorkflowRun,
    Message)
from ..context.fingerprint import stable_hash
from ..realtime import store as events
from .service import actor, envelope, report_targets, stamp


def stage_key(run, activation, node, phase, retry):
    incoming = sorted(run.snapshot.get('incoming', {}).get(node['id'], []))
    rules = {(rule['source'], rule['target']): rule['when'] for rule in run.snapshot.get('edge_rules', [])}
    return [run.id, activation.graph_revision, activation.loop_id, activation.iteration, phase, retry,
        [(parent, rules.get((parent, node['id']), 'always')) for parent in incoming], sorted(node.get('inputs', []))]


async def ensure(session, run, activation, attempt, role_id):
    """首次认领时冻结同级分工；后续节点共享同一消息，重试和图修订另有身份。"""
    nodes = {node['id']: node for node in run.snapshot['nodes']}
    node = attempt.node_snapshot_json or nodes.get(activation.node_id, {'id': activation.node_id})
    # number 是整个 run/node 的序号；正常循环按轮次分离，只有显式重试另外分批。
    retry = attempt.id if attempt.retry_source_id else None
    key = stage_key(run, activation, node, attempt.phase, retry)
    identity = stable_hash(key)
    batch = await session.get(WorkflowDispatchBatch, identity)
    if batch:
        return batch, await session.get(Message, batch.message_id), None
    selected = [activation]
    if attempt.phase == 'work' and node.get('kind') == 'role' and not retry:
        selected = []
        rows = (await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id == run.id,
            WorkflowActivation.graph_revision == activation.graph_revision, WorkflowActivation.iteration == activation.iteration))).all()
        for candidate in rows:
            other = nodes.get(candidate.node_id)
            if not other or other['kind'] != 'role' or candidate.loop_id != activation.loop_id: continue
            if stage_key(run, candidate, other, 'work', None) == key:
                selected.append(candidate)
    members = []
    for item in selected:
        item_node = nodes.get(item.node_id, node)
        rid = role_id if item.id == activation.id else run.state_json.get('assignments', {}).get(item.node_id, {}).get('role_id')
        if rid is None: continue
        original = await session.get(WorkflowAttempt, item.selected_attempt_id) if item.selected_attempt_id else None
        members.append({'activation_id': item.id, 'node_id': item.node_id, 'title': item_node.get('title', item.node_id),
            'role': await actor(session, 'role', rid), 'attempt_id': original.id if original else None})
    recipients = list({m['role']['id']: m['role'] for m in members}.values())
    coordinator = run.snapshot.get('coordinator_role_id')
    via = await actor(session, 'role', coordinator, duty='group_coordinator') if coordinator else await actor(session, 'user', run.owner_id)
    source = {'run_id': run.id, 'graph_revision': activation.graph_revision, 'iteration': activation.iteration,
        'loop_id': activation.loop_id, 'phase': attempt.phase, 'batch_id': identity, 'goal_message_id': run.trigger_message_id}
    start_message = await session.get(Message, run.trigger_message_id) if run.trigger_message_id else None
    if start_message:
        source['goal_message_id'] = ((start_message.meta_json or {}).get('communication') or {}).get('source', {}).get('goal_message_id') or start_message.id
    communication = envelope('workflow_dispatch', await actor(session, 'system'), recipients, owner_id=run.owner_id,
        source=source, report_to=await report_targets(session, run, attempt.phase), via=via)
    phase_name = {'plan': '制定本次分工', 'summary': '汇总本次结果', 'judge': '核对本轮结果'}.get(attempt.phase, '按流程完成本批分工，分别提交结果。')
    message = Message(conversation_id=run.conversation_id, sender_type='system', sender_id=None, parts_json=[{'type': 'text', 'text': phase_name}],
        mentions_json=[r['id'] for r in recipients], status='done', revision=0, chain_id=run.chain_id,
        meta_json={'workflow_run_id': run.id}, reply_to_id=run.trigger_message_id, created_at=now_utc())
    stamp(message, communication)
    session.add(message); await session.flush()
    batch = WorkflowDispatchBatch(id=identity, run_id=run.id, message_id=message.id, members_json=members, created_at=now_utc())
    session.add(batch); await session.flush()
    from ..services.chat import message_payload
    event = await events.append_event(session, run.conversation_id, 'message_created', {'message': message_payload(message)}, revision=0)
    return batch, message, event


async def attach(session, batch, attempt):
    members = [dict(row) for row in batch.members_json]
    for member in members:
        if member['activation_id'] == attempt.activation_id and not member.get('attempt_id'):
            member['attempt_id'] = attempt.id
    batch.members_json = members


async def view(session, message):
    """公开卡片只投影准确尝试的状态和引用；执行专用正文经 Owner 详情读取。"""
    batch = await session.scalar(select(WorkflowDispatchBatch).where(WorkflowDispatchBatch.message_id == message.id))
    if not batch: raise HTTPException(404, 'WORKFLOW_DISPATCH_NOT_FOUND')
    run = await session.get(WorkflowRun, batch.run_id)
    if not run or run.conversation_id != message.conversation_id:
        raise HTTPException(404, 'WORKFLOW_DISPATCH_NOT_FOUND')
    items = []
    for member in batch.members_json:
        activation = await session.get(WorkflowActivation, member['activation_id']) if member['activation_id'] else None
        attempt = await session.get(WorkflowAttempt, member['attempt_id']) if member.get('attempt_id') else None
        if attempt is None and not member.get('attempt_id'):
            attempt = await session.scalar(select(WorkflowAttempt).where(WorkflowAttempt.activation_id == member['activation_id'])
                .order_by(WorkflowAttempt.number).limit(1))
        status = attempt.status if attempt else activation.status if activation else 'interrupted'
        if not attempt and activation and activation.graph_revision != run.graph_revision and activation.status == 'pending':
            status = 'superseded'
        items.append({**member, 'status': status, 'execution_id': attempt.execution_id if attempt else None,
            'error_code': attempt.error_code if attempt else activation.error_code if activation else 'EXECUTION_INTERRUPTED'})
    return {'id': batch.id, 'message_id': message.id, 'run_id': batch.run_id, 'revision': run.revision, 'items': items,
        'status': run.status}


async def legacy_notice(session, run, attempt, node):
    """兼容 v1 串行执行也保存独立输入；串行节点各有自己的可追溯派发卡。"""
    identity = stable_hash(['v1', run.id, attempt.id])
    role = await actor(session, 'role', node['role_id'])
    communication = envelope('workflow_dispatch', await actor(session, 'system'), [role], owner_id=run.owner_id,
        source={'run_id': run.id, 'batch_id': identity, 'graph_revision': run.definition_revision, 'iteration': 0, 'phase': 'work'},
        report_to=[await actor(session, 'user', run.owner_id)], via=await actor(session, 'user', run.owner_id))
    message = Message(conversation_id=run.conversation_id, sender_type='system', sender_id=None,
        parts_json=[{'type': 'text', 'text': node['title']}], mentions_json=[node['role_id']], status='done', revision=0,
        chain_id=run.chain_id, reply_to_id=run.trigger_message_id, meta_json={'workflow_run_id': run.id}, created_at=now_utc())
    stamp(message, communication); session.add(message); await session.flush()
    batch = WorkflowDispatchBatch(id=identity, run_id=run.id, message_id=message.id, created_at=now_utc(),
        members_json=[{'node_id': node['id'], 'activation_id': None, 'attempt_id': attempt.id, 'title': node['title'], 'role': role}])
    session.add(batch); await session.flush()
    return batch, message
