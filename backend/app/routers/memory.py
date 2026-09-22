"""Owner 在目标角色/回复会话的权限交集内搜索、回读及查看工具来源记录。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import AgentExecution, MemoryReference, User
from ..security import require_owner
from ..memory import schemas, service
from ..memory.access import scope_for

router = APIRouter(tags=['memory'])
Owner = Annotated[User, Depends(require_owner)]


class SearchInput(schemas.Search):
    role_id: int = Field(gt=0, le=2**31 - 1)


class ReadInput(schemas.Read):
    role_id: int = Field(gt=0, le=2**31 - 1)


@router.post('/api/conversations/{conversation_id}/memory/search')
async def search(conversation_id: int, payload: SearchInput, user: Owner, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    try:
        async with SessionLocal() as session:
            scope = await scope_for(session, conversation_id=conversation_id, role_id=payload.role_id, user_id=user.id)
        return await service.search(scope, payload)
    except SQLAlchemyError:
        raise HTTPException(503, 'MEMORY_STORAGE_UNAVAILABLE') from None


@router.post('/api/conversations/{conversation_id}/memory/read')
async def read(conversation_id: int, payload: ReadInput, user: Owner, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    try:
        async with SessionLocal() as session:
            scope = await scope_for(session, conversation_id=conversation_id, role_id=payload.role_id, user_id=user.id)
        return await service.read(scope, payload)
    except SQLAlchemyError:
        raise HTTPException(503, 'MEMORY_STORAGE_UNAVAILABLE') from None


@router.get('/api/conversations/{conversation_id}/memory/references')
async def references(conversation_id: int, role_id: int, user: Owner, response: Response):
    """只显示当前仍可查看的来源标题，失效来源保留工具读取事实而不回显旧正文。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        scope = await scope_for(session, conversation_id=conversation_id, role_id=role_id, user_id=user.id)
        rows = (await session.scalars(select(MemoryReference).join(AgentExecution,
            AgentExecution.execution_id == MemoryReference.execution_id).where(AgentExecution.conversation_id == conversation_id,
            AgentExecution.role_id == role_id).order_by(MemoryReference.created_at.desc(), MemoryReference.id).limit(50))).all()
        items = []
        for row in rows:
            value = {'id': row.id, 'execution_id': row.execution_id, 'tool_call_id': row.tool_call_id,
                'action': row.action, 'created_at': service.utc(row.created_at), 'available': False}
            try:
                source = await service.document(session, scope, row.source_kind, row.source_id, row.source_revision)
                source.pop('text')
                value.update(available=True, source=source)
            except HTTPException:
                pass
            items.append(value)
        return {'items': items}
