"""从原始任务关联修正历史来源；不按正文/时间猜测，也不删除旧输入或重放执行。"""
from sqlalchemy import select, or_
from ..db import SessionLocal, with_locked_retry
from ..models import (Message, WorkflowAttempt, WorkflowRun, CoordinationSession, AgentExecution, Generation,
    ExecutionInput, WorldTaskChild, WorldCoordinationGrant)
from .service import actor, envelope, stamp, save_input, report_targets


async def planning_origin(session, grant, *, historical=False, child_id=None):
    """以准确世界子关系辨认委派者；当前任命不用于倒填历史作者。"""
    child = await session.get(WorldTaskChild, child_id) if child_id else await session.scalar(select(WorldTaskChild).where(WorldTaskChild.coordination_id == grant.id))
    world_grant = await session.get(WorldCoordinationGrant, child.parent_execution_id) if child else None
    sender = await actor(session, 'world_manager', world_grant.role_id, historical=historical) if world_grant else await actor(session, 'user', grant.owner_id, historical=historical)
    if not world_grant and grant.feedback_ids_json:
        from ..models import WorkflowFeedbackEvent
        records = (await session.scalars(select(WorkflowFeedbackEvent).where(
            WorkflowFeedbackEvent.feedback_id.in_(grant.feedback_ids_json), WorkflowFeedbackEvent.action == 'coordinate'))).all()
        if any(row.data_json.get('actor_kind') == 'system' and row.data_json.get('coordination_session_id') == grant.id for row in records):
            sender = await actor(session, 'system')
    return envelope('delegation' if world_grant else 'coordination_request', sender,
        [await actor(session, 'role', grant.role_id, historical=historical, duty='group_coordinator')], owner_id=grant.owner_id,
        source={'coordination_id': grant.id, 'world_child_id': child.id if child else None}, report_to=[sender])


async def correct(session, message):
    from ..context.projection import parts_text
    metadata = message.meta_json or {}
    attempt = await session.get(WorkflowAttempt, metadata['workflow_attempt_id']) if metadata.get('workflow_attempt_id') else None
    grant = await session.get(CoordinationSession, metadata['coordination_session_id']) if metadata.get('coordination_session_id') else None
    run = await session.get(WorkflowRun, metadata['workflow_run_id']) if metadata.get('workflow_run_id') else None
    execution = None
    if attempt and attempt.input_message_id == message.id:
        run = await session.get(WorkflowRun, attempt.run_id)
        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == attempt.execution_id)) if attempt.execution_id else None
        if not run or run.conversation_id != message.conversation_id: return False
        targets = [await actor(session, 'role', execution.role_id, historical=True)] if execution else []
        coordinator = run.snapshot.get('coordinator_role_id')
        communication = envelope('legacy_execution_input', await actor(session, 'system'), targets, owner_id=run.owner_id,
            source={'run_id': run.id, 'attempt_id': attempt.id, 'graph_revision': attempt.graph_revision, 'phase': attempt.phase},
            report_to=await report_targets(session, run, attempt.phase, historical=True),
            via=await actor(session, 'role', coordinator, historical=True, duty='group_coordinator') if coordinator else await actor(session, 'user', run.owner_id, historical=True))
    elif grant and grant.trigger_message_id == message.id and grant.conversation_id == message.conversation_id:
        communication = await planning_origin(session, grant, historical=True)
        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == grant.execution_id)) if grant.execution_id else None
    elif run and run.trigger_message_id == message.id and run.conversation_id == message.conversation_id:
        manager = await session.get(CoordinationSession, run.snapshot.get('coordination_session_id')) if run.snapshot.get('coordination_session_id') else None
        communication = envelope('workflow_started' if manager else 'workflow_goal',
            await actor(session, 'system') if manager else await actor(session, 'user', run.owner_id, historical=True), owner_id=run.owner_id,
            source={'run_id': run.id, 'goal_message_id': manager.trigger_message_id if manager else message.id})
    elif message.sender_type in {'role', 'orchestrator'}:
        execution = await session.scalar(select(AgentExecution).join(Generation, Generation.id == AgentExecution.generation_id)
            .where(Generation.assistant_message_id == message.id, AgentExecution.conversation_id == message.conversation_id))
        if not execution: return False
        input_row = await session.get(ExecutionInput, execution.execution_id)
        world = await session.get(WorldCoordinationGrant, execution.execution_id)
        if not input_row and not world: return False
        sender = await actor(session, 'world_manager' if world else 'role', execution.role_id, historical=True)
        communication = envelope('coordination_reply' if world else 'workflow_result', sender,
            input_row.source_json.get('communication', {}).get('report_to', []) if input_row else [await actor(session, 'user', world.owner_id, historical=True)],
            owner_id=world.owner_id if world else input_row.owner_id, source={'execution_id': execution.execution_id})
        execution = None  # 角色回复不是新的输入记录。
    elif metadata.get('workflow_attempt_id') or metadata.get('workflow_run_id') or metadata.get('coordination_session_id'):
        communication = envelope('legacy_execution_input' if metadata.get('workflow_attempt_id') else 'legacy_workflow_record',
            {'kind': 'system', 'id': None, 'name': '历史工作流记录（来源未完整记录）', 'historical': True})
    else:
        return False
    communication['legacy'] = True
    if execution and not await session.get(ExecutionInput, execution.execution_id):
        await save_input(session, execution, message, communication['authorized_by_user_id'], parts_text(message.parts_json),
            {**metadata, 'communication': communication})
    stamp(message, communication)
    message.revision += 1
    return True


async def run():
    """启动接收请求前分批修复；每条新来源有持久标记，重启只处理未完成部分。"""
    after = 0
    affected = set()
    while True:
        async def batch():
            async with SessionLocal() as session:
                workflow_replies = select(Generation.assistant_message_id).join(AgentExecution, AgentExecution.generation_id == Generation.id).where(or_(
                    AgentExecution.execution_kind == 'world_coord', AgentExecution.execution_id.in_(select(WorkflowAttempt.execution_id)),
                    AgentExecution.execution_id.in_(select(CoordinationSession.execution_id))))
                rows = (await session.scalars(select(Message).where(Message.id > after,
                    Message.meta_json['communication'].as_string().is_(None), or_(
                        Message.meta_json['workflow_run_id'].as_string().is_not(None),
                        Message.meta_json['workflow_attempt_id'].as_string().is_not(None),
                        Message.meta_json['coordination_session_id'].as_string().is_not(None), Message.id.in_(workflow_replies)))
                    .order_by(Message.id).limit(100))).all()
                changed = set()
                for row in rows:
                    if await correct(session, row): changed.add(row.conversation_id)
                await session.commit()
                return rows[-1].id if rows else None, changed
        last, changed = await with_locked_retry(batch)
        affected.update(changed)
        if last is None: break
        after = last
    if affected:
        from ..realtime import store as events
        async with SessionLocal() as session:
            pending = [await events.append_event(session, cid, 'communication_updated', {'reload_history': True}) for cid in sorted(affected)]
            await session.commit()
        await events.publish_events(*pending)
