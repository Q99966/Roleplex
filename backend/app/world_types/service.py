"""当前类型的配置、初始化与输入适配；读取不启动初始化或模型。"""
import asyncio
import json

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import InstanceSettings, WorldTypeState
from ..context.domain import ContextBuildError
from ..context.fingerprint import stable_hash
from .contracts import Initialization, WorldContext, WorldMaterial
from .registry import resolve

_identity = ('default', 'general', 1)
_initialize_lock = asyncio.Lock()


def configure(world_name, world_type='general', type_version=1):
    """仅在宿主启动或隔离测试装配时调用；浏览器不能热切换存档类型。"""
    global _identity, _initialize_lock
    resolve(world_type, type_version)
    _identity = (world_name, world_type, type_version)
    _initialize_lock = asyncio.Lock()


def current():
    name, kind, version = _identity
    return {'world_name': name, 'world_type': kind, 'type_version': version}


def descriptor():
    return resolve(_identity[1], _identity[2])


def configuration(row):
    data = row.configuration_json if row and row.initialized_type in (None, _identity[1]) else {}
    try:
        return descriptor().configuration_model.model_validate(data).model_dump(mode='json')
    except ValidationError:
        raise HTTPException(422, 'WORLD_TYPE_CONFIG_INVALID') from None


def context(uid, *, owner_id=None, **fields):
    return WorldContext(**current(), owner_id=uid if owner_id is None else owner_id, user_id=uid, **fields)


async def view(session, uid):
    """返回 Owner 当前配置和类型提供的有界概览；不调用初始化器。"""
    row = await session.get(WorldTypeState, 1)
    matching = row is not None and row.initialized_type == _identity[1] and row.initialized_version == _identity[2]
    settings = configuration(row)
    result = {**current(), 'descriptor': descriptor().view(), 'revision': row.revision if row else 0,
        'configuration': settings, 'initialization': {'status': row.status if matching else 'pending',
            'resources': row.resources_json if matching else {}, 'required_fields': row.required_fields_json if matching else [],
            'error_code': row.error_code if matching else None}, 'overview': None}
    if matching and row.status == 'ready' and descriptor().overview:
        try:
            result['overview'] = await descriptor().overview(session, context(uid), settings, row.resources_json)
            if not isinstance(result['overview'], dict) or len(json.dumps(result['overview'], ensure_ascii=False)) > 100_000:
                raise ValueError()
        except Exception:
            raise HTTPException(503, 'WORLD_TYPE_OVERVIEW_UNAVAILABLE') from None
    return result


async def _locked_state(session):
    # 初始化尚无独立行时，先以现有单例串行认领，适用于 SQLite 与 PostgreSQL。
    await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1).values(budget_revision=InstanceSettings.budget_revision))
    row = await session.get(WorldTypeState, 1, populate_existing=True)
    if row is None:
        row = WorldTypeState(id=1, configuration_json={}, revision=0, initialized_version=0, status='pending',
            resources_json={}, required_fields_json=[], updated_at=now_utc())
        session.add(row); await session.flush()
    return row


async def initialize(expected_revision=None):
    """同一短事务初始化资源和凭据；失败回滚资源，另记安全状态，重复调用不重复播种。"""
    observed = None
    async def operation():
        nonlocal observed
        async with SessionLocal() as session:
            row = await _locked_state(session)
            if expected_revision is not None and row.revision != expected_revision:
                raise HTTPException(409, 'WORLD_TYPE_REVISION_CONFLICT')
            observed = row.revision
            world = await session.get(InstanceSettings, 1)
            if world.owner_user_id is None:
                row.status = 'needs_owner'
                await session.commit()
                return
            matching = row.initialized_type == _identity[1] and row.initialized_version == _identity[2]
            if matching and row.status == 'ready':
                await session.commit()
                return
            config = configuration(row)
            resources = row.resources_json if matching else {}
            result = await descriptor().initializer(session, context(world.owner_user_id), config, resources) if descriptor().initializer else Initialization()
            if not isinstance(result, Initialization) or result.status not in {'ready', 'needs_configuration'}:
                raise ValueError('WORLD_TYPE_INITIALIZATION_INVALID')
            # 防止扩展把 ORM 对象或其他不可序列化值提交为资源引用。
            json.dumps(result.resources)
            row.configuration_json = config
            row.initialized_type = _identity[1]; row.initialized_version = _identity[2]
            row.resources_json = result.resources; row.required_fields_json = list(result.required_fields)
            row.status = result.status; row.error_code = None; row.revision += 1; row.updated_at = now_utc()
            await session.commit()
    async with _initialize_lock:
        try:
            await with_locked_retry(operation)
        except HTTPException:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            # 扩展/SQL 异常可能包含配置正文，既不回显也不记录原异常。
            async def failed():
                async with SessionLocal() as session:
                    row = await _locked_state(session)
                    if observed is not None and row.revision == observed:
                        row.status = 'failed'; row.error_code = 'WORLD_TYPE_INITIALIZATION_FAILED'
                        row.initialized_type = _identity[1]; row.initialized_version = _identity[2]
                        row.revision += 1; row.updated_at = now_utc()
                        await session.commit()
            await with_locked_retry(failed)


async def save_configuration(uid, expected_revision, values):
    """保存与初始化分开收口；初始化失败时仍能查看已保存设置并明确重试。"""
    try:
        config = descriptor().configuration_model.model_validate(values).model_dump(mode='json')
    except ValidationError:
        raise HTTPException(422, 'WORLD_TYPE_CONFIG_INVALID') from None
    async def operation():
        async with SessionLocal() as session:
            row = await _locked_state(session)
            if row.revision != expected_revision:
                raise HTTPException(409, 'WORLD_TYPE_REVISION_CONFLICT')
            if row.initialized_type not in (None, _identity[1]):
                row.resources_json = {}
            row.initialized_type = _identity[1]; row.initialized_version = _identity[2]
            row.configuration_json = config; row.status = 'pending'; row.error_code = None
            row.revision += 1; row.updated_at = now_utc()
            revision = row.revision
            await session.commit()
            return revision
    revision = await with_locked_retry(operation)
    await initialize(revision)


async def materials(session, *, uid, role_id, conversation_id, execution_id, execution_kind, boundary):
    """当前会话已鉴权后调用；返回临时正文与无正文凭据，均纳入同一预算。"""
    definition = descriptor()
    if definition.build_context is None:
        return [], None
    row = await session.get(WorldTypeState, 1)
    if row is None or row.status != 'ready' or row.initialized_type != definition.id or row.initialized_version != definition.version:
        raise ContextBuildError('WORLD_TYPE_NOT_READY')
    actor = await execution_context(session, uid, role_id=role_id, conversation_id=conversation_id, execution_id=execution_id,
        execution_kind=execution_kind, through_message_id=boundary)
    try:
        contributions = await definition.build_context(session, actor, configuration(row))
    except Exception:
        raise ContextBuildError('WORLD_TYPE_CONTEXT_INVALID') from None
    if not isinstance(contributions, list) or len(contributions) > 32 or any(not isinstance(item, WorldMaterial) for item in contributions):
        raise ContextBuildError('WORLD_TYPE_CONTEXT_INVALID')
    receipt = {'id': definition.id, 'version': definition.version, 'configuration_revision': row.revision,
        'sources': [{'source_id': item.source_id, 'revision': item.revision, 'fingerprint': stable_hash(item.text)} for item in contributions]}
    if not await _sources_valid(definition, session, actor, receipt):
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
    return contributions, receipt


async def revalidate_material(session, request, material):
    """每次模型调用前复核类型配置与源版本；失效资料不继续进入请求。"""
    receipt = material.get('world_type')
    if receipt is None:
        return
    definition = descriptor()
    row = await session.get(WorldTypeState, 1)
    if (receipt['id'] != definition.id or receipt['version'] != definition.version or not row
        or row.status != 'ready' or row.revision != receipt['configuration_revision']):
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
    actor = await execution_context(session, request.triggered_by_user_id,
        role_id=request.role_id, conversation_id=request.conversation_id,
        execution_id=request.execution_id, execution_kind=request.execution_kind,
        through_message_id=material['visible_through_message_id'])
    if not await _sources_valid(definition, session, actor, receipt):
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')


async def _sources_valid(definition, session, actor, receipt):
    try:
        return await definition.validate_sources(session, actor, receipt) is True
    except Exception:
        raise ContextBuildError('WORLD_TYPE_CONTEXT_INVALID') from None


async def execution_context(session, uid, **fields):
    """所有类型扩展共用相同的实际父子与额度身份；预览无 execution 时不伪造任务。"""
    from ..models import AgentExecution, WorkflowBudget, WorldCoordinationGrant, WorldTask, WorldTaskChild
    execution_id = fields.get('execution_id')
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id)) if execution_id else None
    budget = await session.get(WorkflowBudget, execution.chain_id) if execution else None
    grant = await session.get(WorldCoordinationGrant, execution_id) if execution_id else None
    child = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.chain_id == execution.chain_id)) if execution else None
    task_id = grant.task_id if grant else child.task_id if child else None
    task = await session.get(WorldTask, task_id) if task_id else None
    return context(uid, owner_id=(await session.get(InstanceSettings, 1)).owner_user_id, **fields,
        parent_execution_id=execution.parent_execution_id if execution else None,
        world_task_id=task_id, budget_chain_id=execution.chain_id if execution else None,
        root_budget_chain_id=(budget.root_chain_id or budget.chain_id) if budget else None,
        appointment_revision=grant.appointment_revision if grant else task.appointment_revision if task else None)


async def assert_active(actor):
    """供类型长操作在副作用边界复核取消/撤权；不把检查当作宿主进程沙箱。"""
    if current() != {'world_name': actor.world_name, 'world_type': actor.world_type, 'type_version': actor.type_version} or not actor.execution_id:
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
    from ..context.automatic import validate_parent
    async with SessionLocal() as session:
        await validate_parent(session, actor.execution_id, actor.conversation_id, actor.user_id)
