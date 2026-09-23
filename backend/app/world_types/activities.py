"""类型活动准备与实际群执行衔接；不再创建另一套模型循环或业务 Trace。"""
from uuid import uuid4
from pydantic import create_model, Field
from fastapi import HTTPException
from sqlalchemy import select

from ..db import SessionLocal, now_utc
from ..models import Conversation, ConversationMember, WorldTaskChild, WorldTypeState
from ..context.fingerprint import stable_hash
from ..agent.tool_definitions import tool_definition
from . import service
from .contracts import ActivityPlan


def definitions():
    descriptor = service.descriptor()
    return {f'world_activity_{descriptor.id}_{item.name}': item for item in descriptor.activities}


def schema(name):
    definition = definitions()[name]
    return create_model(name + '_request', __base__=definition.parameters_model,
        request_key=(str, Field(min_length=1, max_length=128, description='当前世界任务内的幂等身份；重试使用相同值。')))


def specs():
    return [tool_definition(name, definition.description + ' 由宿主准备并派发，使用原世界任务的额度和取消范围。', schema(name))
        for name, definition in definitions().items()]


async def start(execution_id, name, values):
    from ..world_orchestrator import service as coordinator, tasks
    definition = definitions().get(name)
    if definition is None:
        raise HTTPException(404, 'WORLD_TYPE_UNAVAILABLE')
    payload = schema(name).model_validate(values)
    parameters = definition.parameters_model.model_validate(payload.model_dump(exclude={'request_key'}))
    digest = stable_hash([name, payload.model_dump()])
    async with coordinator.control_lock, SessionLocal() as session:
        grant, task = await tasks.authorized(session, execution_id, writing=True)
        existing = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.task_id == task.id,
            WorldTaskChild.request_key == payload.request_key))
        if existing:
            if existing.request_digest != digest:
                raise HTTPException(409, 'WORLD_TASK_REQUEST_CONFLICT')
            return next(c for c in await tasks.children(session, task) if c['id'] == existing.id)
        state = await session.get(WorldTypeState, 1)
        if not state or state.status != 'ready':
            raise HTTPException(409, 'WORLD_TYPE_NOT_READY')
        actor = await service.execution_context(session, grant.owner_id, role_id=grant.role_id, conversation_id=grant.conversation_id,
            execution_id=execution_id, execution_kind='world_coord')
        plan = await definition.prepare(session, actor, parameters, service.configuration(state), state.resources_json)
        if not isinstance(plan, ActivityPlan):
            raise HTTPException(422, 'WORLD_TYPE_ACTIVITY_INVALID')
        group = await session.get(Conversation, plan.conversation_id)
        member = await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == plan.conversation_id,
            ConversationMember.member_type == 'user', ConversationMember.member_id == grant.owner_id))
        if not group or not member or group.created_by != grant.owner_id or group.type != 'group' or group.deleted_at or not group.orchestrator_enabled:
            raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
        child = WorldTaskChild(id=uuid4().hex, task_id=task.id, parent_execution_id=execution_id,
            request_key=payload.request_key, request_digest=digest, kind='activity', conversation_id=group.id,
            status='pending_dispatch', reference_json={'world_type': service.current()['world_type'], 'operation': definition.name,
                'activity': plan.reference, 'definition_id': plan.definition_id}, created_at=now_utc())
        session.add(child); await session.flush()
        role_id = group.orchestrator_role_id
        pending = await tasks.changed(session, task)
        await session.commit()
    from ..realtime import store as events
    await events.publish_events(pending)
    from ..workflows import planning
    from ..workflows.graph_schemas import Coordinate
    try:
        await planning.start(plan.conversation_id, grant.owner_id, Coordinate(role_id=role_id, mode='execute',
            goal=plan.goal, definition_id=plan.definition_id, expected_graph_revision=plan.graph_revision,
            request_key='world-' + child.id), world_child_id=child.id)
    except BaseException:
        async with SessionLocal() as session:
            row = await session.get(WorldTaskChild, child.id)
            if not row.coordination_id and row.status == 'pending_dispatch':
                row.status = 'failed'; row.error_code = 'WORLD_TASK_DISPATCH_FAILED'; await session.commit()
        raise
    async with SessionLocal() as session:
        return next(c for c in await tasks.children(session, task) if c['id'] == child.id)
