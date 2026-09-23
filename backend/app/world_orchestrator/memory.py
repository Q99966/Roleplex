"""世界约定的来源、版本与停用；模型整理保持 origin，不升级为平台规则。"""
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import Conversation, Message, MemoryReference, WorldMemory, WorldMemoryVersion, WorldTaskChild
from ..context.fingerprint import stable_hash
from . import service, tasks


class Save(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_key: str = Field(min_length=1, max_length=128)
    category: Literal['constraint', 'decision', 'preference', 'note', 'inference'] = 'note'
    text: str = Field(min_length=1, max_length=20000)


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20000)
    status: Literal['active', 'disabled']


async def view(session, row, *, full=False, admin=False):
    source_valid = True
    if row.source_message_id:
        source = await session.get(Message, row.source_message_id)
        conversation = await session.get(Conversation, source.conversation_id) if source else None
        source_valid = bool(source and source.revision == row.source_revision and conversation and
            conversation.created_by == row.owner_id and not conversation.deleted_at)
    valid = source_valid and row.status == 'active'
    return {'id': row.id, 'category': row.category, 'revision': row.revision, 'status': row.status, 'origin': row.origin,
        'source_message_id': row.source_message_id, 'source_revision': row.source_revision, 'source_role_id': row.source_role_id,
        'available': valid, 'text': (row.text if full else row.text[:400]) if source_valid and (valid or admin) else None,
        'updated_at': row.updated_at, 'notice': '模型整理，按原来源核对；推断不等于已确认事实。' if row.origin == 'agent' else 'Owner 保存的世界约定。'}


async def search(uid, query='', *, include_disabled=False, execution_id=None):
    """返回有界岗位条目；模型只使用有效来源，并记录实际采用版本。"""
    async with SessionLocal() as session:
        statement = select(WorldMemory).where(WorldMemory.owner_id == uid)
        if not include_disabled: statement = statement.where(WorldMemory.status == 'active')
        if query.strip(): statement = statement.where(WorldMemory.text.contains(query.strip(), autoescape=True))
        rows = (await session.scalars(statement.order_by(WorldMemory.updated_at.desc()).limit(50))).all()
        items = [await view(session, row, admin=include_disabled) for row in rows]
        if execution_id:
            await remember(session, execution_id, items, 'search')
            await session.commit()
        return {'items': items}


async def read(uid, identity, revision, *, admin=False, execution_id=None):
    """精确回读当前版本，源失效时不以历史正文降级返回。"""
    async with SessionLocal() as session:
        row = await session.get(WorldMemory, identity)
        if not row or row.owner_id != uid:
            raise HTTPException(404, 'WORLD_MEMORY_NOT_FOUND')
        value = await view(session, row, full=True, admin=admin)
        if row.revision != revision or value['text'] is None:
            raise HTTPException(409, 'WORLD_MEMORY_CHANGED')
        if execution_id:
            await remember(session, execution_id, [value], 'read')
            await session.commit()
        return value


async def save(uid, payload, *, execution_id=None):
    """幂等保存世界约定，模型来源固定为任务原要求，不伪装 Owner 手动确认。"""
    digest = stable_hash(payload.model_dump(exclude={'request_key'}))
    async def operation():
        async with SessionLocal() as session:
            # 同世界短写边界兼顾相同请求键的并发认领。
            from ..models import InstanceSettings
            await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1).values(budget_revision=InstanceSettings.budget_revision))
            old = await session.scalar(select(WorldMemory).where(WorldMemory.owner_id == uid, WorldMemory.request_key == payload.request_key))
            if old:
                if old.request_digest != digest:
                    raise HTTPException(409, 'WORLD_MEMORY_REQUEST_CONFLICT')
                return await view(session, old, full=True)
            source, grant, task = None, None, None
            if execution_id:
                grant, task = await tasks.authorized(session, execution_id, writing=True)
                if grant.owner_id != uid: raise HTTPException(403, 'WORLD_TASK_TARGET_FORBIDDEN')
                source = await session.get(Message, task.trigger_message_id)
            row = WorldMemory(id=uuid4().hex, owner_id=uid, category=payload.category, text=payload.text,
                status='active', revision=1, origin='agent' if execution_id else 'owner',
                source_message_id=source.id if source else None, source_revision=source.revision if source else None,
                source_role_id=grant.role_id if grant else None, request_key=payload.request_key, request_digest=digest,
                created_at=now_utc(), updated_at=now_utc())
            session.add(row)
            if task:
                session.add(WorldTaskChild(id=uuid4().hex, task_id=task.id, parent_execution_id=execution_id,
                    request_key='memory:' + row.id, request_digest=digest, kind='memory', status='completed',
                    reference_json={'memory_id': row.id, 'revision': row.revision}, created_at=now_utc()))
            await session.flush()
            await record_version(session, row)
            value = await view(session, row, full=True)
            await session.commit()
            return value
    return await with_locked_retry(operation)


async def edit(uid, identity, payload):
    """按 revision 原子编辑/停用并留存版本，只有正文改写才转换为手动来源。"""
    async def operation():
        async with SessionLocal() as session:
            row = await session.get(WorldMemory, identity)
            if not row or row.owner_id != uid:
                raise HTTPException(404, 'WORLD_MEMORY_NOT_FOUND')
            provenance = {'origin': 'owner', 'source_message_id': None, 'source_revision': None, 'source_role_id': None} if payload.text != row.text else {}
            updated = await session.execute(update(WorldMemory).where(WorldMemory.id == identity, WorldMemory.owner_id == uid,
                WorldMemory.revision == payload.expected_revision).values(text=payload.text, status=payload.status,
                    revision=WorldMemory.revision + 1, **provenance, updated_at=now_utc()))
            if updated.rowcount != 1: raise HTTPException(409, 'WORLD_MEMORY_CHANGED')
            row = await session.get(WorldMemory, identity, populate_existing=True)
            await record_version(session, row)
            value = await view(session, row, full=True, admin=True)
            await session.commit()
            return value
    return await with_locked_retry(operation)


async def record_version(session, row):
    snapshot = {key: getattr(row, key) for key in ('id', 'owner_id', 'category', 'text', 'status', 'revision', 'origin', 'source_message_id', 'source_revision', 'source_role_id')}
    session.add(WorldMemoryVersion(memory_id=row.id, revision=row.revision, snapshot_json=snapshot, created_at=now_utc()))


async def remember(session, execution_id, items, action):
    """复用 MemoryReference 记录被送入工具上下文的条目版本，正文不另存追踪表。"""
    from ..agent.tool_context import tool_call_id
    await service.authorized(session, execution_id)
    call_id = tool_call_id.get() or 'world-memory'
    for item in items:
        if not item['available'] or item['text'] is None:
            continue
        identity = stable_hash([execution_id, call_id, 'world_note', item['id'], item['revision'], action])
        if await session.get(MemoryReference, identity) is None:
            session.add(MemoryReference(id=identity, execution_id=execution_id, tool_call_id=call_id,
                source_kind='world_note', source_id=item['id'], source_revision=item['revision'],
                reference='world-note:' + item['id'], action=action, offset=0, characters=len(item['text']), created_at=now_utc()))


async def validate_reference(session, uid, identity, revision):
    """编辑、停用或原来源失效后拒绝旧工具结果，私有压缩也经过同一复核。"""
    row = await session.get(WorldMemory, identity)
    if not row or row.owner_id != uid or row.revision != revision or not (await view(session, row))['available']:
        raise HTTPException(409, 'WORLD_MEMORY_CHANGED')
    return row
