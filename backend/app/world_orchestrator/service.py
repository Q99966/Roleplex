"""世界任命及协调会话的唯一控制入口；旧任命的执行授权不可再次复活。"""
import asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select, update, func

from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import (AgentExecution, Conversation, ConversationMember, Generation, InstanceSettings, ModelConfig,
    Role, User, WorldOrchestrator, WorldCoordinationGrant)
from ..context.domain import ContextBuildError
from ..realtime import store as events

control_lock = asyncio.Lock()
DUTY = '你是当前世界的固定世界管理者。与 Owner 澄清目标、组织任务并交接事实。岗位身份不代表无限工具权限；只有本次实际工具返回的记录能证明操作已执行，未知结果保持未知。'


async def view(session, uid):
    """返回当前任命和协调会话元数据，角色失效仍保留原历史引用。"""
    from ..config import settings
    state = await session.get(WorldOrchestrator, 1)
    role = await session.get(Role, state.role_id) if state and state.role_id else None
    valid = bool(role and role.active and not role.deleted_at and role.created_by == uid and role.model_config_id)
    conversation = await session.get(Conversation, state.conversation_id) if state and state.conversation_id else None
    member = await session.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation.id,
        ConversationMember.member_type == 'user', ConversationMember.member_id == uid)) if conversation else None
    from ..routers.conversations import response
    from ..routers.roles import to_response
    from ..models import WorldTask
    active_count = await session.scalar(select(func.count()).select_from(WorldTask).where(WorldTask.owner_id == uid,
        WorldTask.status.not_in(['completed', 'stopped', 'failed', 'interrupted'])))
    return {'world_name': settings.world_name, 'role_id': state.role_id if state else None,
        'revision': state.revision if state else 0, 'available': valid, 'active_task_count': active_count,
        'fixed_identity': True, 'enabled': bool(role and role.active),
        'status': 'needs_owner' if not role else 'disabled' if not role.active else 'ready' if valid else 'needs_configuration',
        'profile': to_response(role).model_dump(mode='json') if role else None,
        'role_name': role.name if role else None, 'model_name': role.model_name if valid else None,
        'conversation': (await response(session, conversation, member)).model_dump(mode='json') if conversation and member else None}


async def validate_member(session, cid, rid, uid):
    """协调材料只面向当前 Owner 和当前任职角色；普通角色关系不授予世界用途。"""
    state = await session.get(WorldOrchestrator, 1)
    user = await session.get(User, uid) if uid else None
    role = await session.get(Role, rid) if rid else None
    conversation = await session.get(Conversation, cid)
    if (not state or state.conversation_id != cid or state.role_id != rid or not user or not user.is_owner
        or not conversation or conversation.purpose != 'world_coord' or conversation.created_by != uid or conversation.deleted_at
        or not role or role.created_by != uid or role.managed_kind != 'world_manager' or not role.active or role.deleted_at or not role.model_config_id):
        raise ContextBuildError('WORLD_ORCHESTRATOR_REVOKED')
    return state


async def authorized(session, execution_id):
    """每次派发/调用复核宿主持久 grant 和当前任命，版本阻止 A→B→A 复活。"""
    from ..runtime.models import RuntimeGate
    gate = await session.get(RuntimeGate, 1)
    if gate and gate.closing:
        raise ContextBuildError('WORLD_ORCHESTRATOR_REVOKED')
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
    grant = await session.get(WorldCoordinationGrant, execution_id)
    if not execution or execution.execution_kind != 'world_coord' or not grant:
        raise ContextBuildError('WORLD_ORCHESTRATOR_REVOKED')
    state = await validate_member(session, grant.conversation_id, grant.role_id, grant.owner_id)
    generation = await session.get(Generation, execution.generation_id)
    if (state.revision != grant.appointment_revision or execution.role_id != grant.role_id
        or execution.conversation_id != grant.conversation_id or execution.status not in {'queued', 'running'}
        or generation is None or generation.stop_requested_at is not None):
        raise ContextBuildError('WORLD_ORCHESTRATOR_REVOKED')
    return grant


async def _revoke(session, state):
    """先持久封闭旧派发，返回实际取消的 generation，提交后再发送取消信号。"""
    ids = list((await session.scalars(select(AgentExecution.generation_id).join(WorldCoordinationGrant,
        WorldCoordinationGrant.execution_id == AgentExecution.execution_id).where(
        WorldCoordinationGrant.conversation_id == state.conversation_id,
        WorldCoordinationGrant.appointment_revision < state.revision,
        AgentExecution.status.in_(['queued', 'running'])))).all()) if state.conversation_id else []
    if ids:
        await session.execute(update(Generation).where(Generation.id.in_(ids)).values(stop_requested_at=now_utc()))
    return ids


PROFILE_FIELDS = ('avatar', 'description', 'tags_json', 'system_prompt', 'model_config_id', 'model_name',
    'context_window_tokens', 'params_json', 'skills_json', 'builtin_tools_json', 'mcp_servers_json', 'mcp_tools_cache_json')


async def initialize():
    """Owner 就绪后建立专用身份；旧绑定复制配置并沿用岗位会话，原普通角色不变。"""
    from copy import deepcopy
    async def operation():
        async with SessionLocal() as session:
            await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1).values(budget_revision=InstanceSettings.budget_revision))
            world = await session.get(InstanceSettings, 1)
            state = await session.get(WorldOrchestrator, 1)
            if state is None:
                state = WorldOrchestrator(id=1, revision=0, updated_at=now_utc())
                session.add(state)
            if not world.owner_user_id:
                await session.commit()
                return
            uid = world.owner_user_id
            role = await session.scalar(select(Role).where(Role.created_by == uid, Role.managed_kind == 'world_manager'))
            if role is not None and state.role_id == role.id and state.conversation_id:
                return
            old = await session.get(Role, state.role_id) if state.role_id else None
            if role is None:
                names = set((await session.scalars(select(Role.name).where(Role.created_by == uid, Role.deleted_at.is_(None)))).all())
                name, number = '世界管理者', 1
                while name in names:
                    number += 1; name = f'世界管理者（{number}）'
                role = Role(created_by=uid, name=name, managed_kind='world_manager', system_prompt='协助 Owner 管理当前世界，依据真实任务和工具结果组织协作。',
                    model_config_id=None, model_name='', builtin_tools_json=['memory_search', 'memory_read'],
                    active=not bool(state.conversation_id and not state.role_id), created_at=now_utc(), updated_at=now_utc())
                if old and old.created_by == uid and not old.deleted_at:
                    for field in PROFILE_FIELDS: setattr(role, field, deepcopy(getattr(old, field)))
                    role.active = old.active
                    config = await session.get(ModelConfig, role.model_config_id) if role.model_config_id else None
                    if not config or config.created_by != uid:
                        role.model_config_id = None
                session.add(role); await session.flush()
            conversation = await session.get(Conversation, state.conversation_id) if state.conversation_id else None
            if conversation is None:
                conversation = Conversation(type='single', purpose='world_coord', title='世界协调', created_by=uid, created_at=now_utc())
                session.add(conversation); await session.flush()
                session.add(ConversationMember(conversation_id=conversation.id, member_type='user', member_id=uid, joined_at=now_utc()))
                state.conversation_id = conversation.id
            if state.role_id is not None:
                state.revision += 1
            state.role_id = role.id; state.updated_at = now_utc()
            await _revoke(session, state)
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == conversation.id,
                ConversationMember.member_type == 'role'))
            session.add(ConversationMember(conversation_id=conversation.id, member_type='role', member_id=role.id, joined_at=now_utc()))
            conversation.revision += 1
            await session.commit()
    async with control_lock:
        await with_locked_retry(operation)


async def appoint(uid, role_id, expected_revision):
    """旧写入口明确拒绝替换稳定身份，客户端改用配置导入或启停。"""
    raise HTTPException(409, 'WORLD_ORCHESTRATOR_MANAGED_IDENTITY')


async def change(uid, *, source_role_id=None, expected_revision=None, enabled=None, profile=None):
    """短事务更新专用配置并撤销旧执行；岗位/历史身份不随配置导入变化。"""
    from copy import deepcopy
    from ..routers.roles import owned_model_config, flush_role
    async def operation():
        async with SessionLocal() as session:
            await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1).values(budget_revision=InstanceSettings.budget_revision))
            state = await session.get(WorldOrchestrator, 1)
            role = await session.get(Role, state.role_id) if state and state.role_id else None
            if not role or role.created_by != uid or role.managed_kind != 'world_manager':
                raise HTTPException(409, 'WORLD_ORCHESTRATOR_UNAVAILABLE')
            if profile is None and state.revision != expected_revision:
                raise HTTPException(409, 'WORLD_ORCHESTRATOR_REVISION_CONFLICT')
            if source_role_id is not None:
                source = await session.get(Role, source_role_id)
                if not source or source.created_by != uid or source.deleted_at or not source.active or source.managed_kind or not source.model_config_id:
                    raise HTTPException(422, 'WORLD_ORCHESTRATOR_ROLE_UNAVAILABLE')
                await owned_model_config(session, uid, source.model_config_id)
                for field in PROFILE_FIELDS: setattr(role, field, deepcopy(getattr(source, field)))
                role.active = True
            if profile is not None:
                if profile.expected_revision != role.revision:
                    raise HTTPException(409, 'ROLE_REVISION_CONFLICT')
                await owned_model_config(session, uid, profile.model_config_id)
                for key in ('name', 'avatar', 'description', 'system_prompt', 'model_config_id', 'model_name', 'context_window_tokens'):
                    setattr(role, key, getattr(profile, key))
                for key in ('tags', 'params', 'builtin_tools', 'skills', 'mcp_servers'):
                    if key in profile.model_fields_set: setattr(role, key + '_json', deepcopy(getattr(profile, key)))
                if profile.active is not None: role.active = profile.active
            if enabled is not None: role.active = enabled
            role.revision += 1; role.updated_at = now_utc()
            state.revision += 1; state.updated_at = now_utc()
            ids = await _revoke(session, state)
            conversation = await session.get(Conversation, state.conversation_id)
            conversation.revision += 1
            pending = await events.append_event(session, conversation.id, 'conversation_updated',
                {'revision': conversation.revision, 'world_orchestrator_revision': state.revision}, revision=conversation.revision)
            await flush_role(session)
            result = await view(session, uid)
            await session.commit()
            return result, conversation.id, ids, pending
    async with control_lock:
        result, cid, ids, pending = await with_locked_retry(operation)
    await events.publish_events(pending)
    if ids:
        from ..scheduling import conversation_scheduler
        await conversation_scheduler.stop_generations(cid, ids)
    return result


async def send(session, conversation_id, payload, user):
    """沿用原消息 reducer、预算与队列，额外固定本次世界任命凭据。"""
    from ..routers.messages import _send_message
    async with control_lock:
        if payload.client_message_id:
            from ..models import Message
            existing = await session.scalar(select(Message.id).where(Message.conversation_id == conversation_id,
                Message.sender_id == user.id, Message.client_message_id == payload.client_message_id))
            if existing:
                return await _send_message(conversation_id, payload, user, session)
        state = await session.get(WorldOrchestrator, 1, populate_existing=True)
        if not state or state.conversation_id != conversation_id:
            raise HTTPException(404, 'CONVERSATION_NOT_FOUND')
        try:
            await validate_member(session, conversation_id, state.role_id, user.id)
        except ContextBuildError:
            raise HTTPException(409, 'WORLD_ORCHESTRATOR_UNAVAILABLE') from None
        if payload.mentions and payload.mentions not in ([state.role_id], ['all']):
            raise HTTPException(422, 'WORLD_ORCHESTRATOR_ROLE_UNAVAILABLE')
        if payload.expected_appointment_revision is not None and payload.expected_appointment_revision != state.revision:
            raise HTTPException(409, 'WORLD_ORCHESTRATOR_REVISION_CONFLICT')
        task = None
        if payload.world_task_id:
            from .tasks import continue_in_session
            task = await continue_in_session(session, payload, user.id, state)
        return await _send_message(conversation_id, payload, user, session,
            world_appointment_revision=state.revision, world_task=task)


async def invalidate_role_in_session(session, role_id):
    """角色停用/删除的同一事务撤销世界任命；提交后由调用方完成取消。"""
    state = await session.get(WorldOrchestrator, 1)
    if not state or state.role_id != role_id:
        return None
    state.role_id = None; state.revision += 1; state.updated_at = now_utc()
    if state.conversation_id:
        await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == state.conversation_id,
            ConversationMember.member_type == 'role'))
    ids = await _revoke(session, state)
    return state.conversation_id, ids


async def finish_revocation(value):
    if value and value[1]:
        from ..scheduling import conversation_scheduler
        await conversation_scheduler.stop_generations(*value)
