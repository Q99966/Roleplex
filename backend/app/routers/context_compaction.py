"""Owner 的主动压缩、进度、停止与版本回退；维护请求不发送聊天消息。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from ..context import compaction
from ..context.compaction_schema import Start, Restore
from ..context.domain import ContextBuildError
from ..db import SessionLocal
from ..models import User
from ..security import require_owner
from .conversation_context import context_view

router = APIRouter(tags=['context-compaction'])
Owner = Annotated[User, Depends(require_owner)]


@router.post('/api/conversations/{conversation_id}/context/compressions', status_code=202)
async def start(conversation_id: int, payload: Start, request: Request, response: Response, user: Owner):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await compaction.start(conversation_id, user.id, payload, getattr(request.state, 'request_id', None))
    except ContextBuildError:
        raise HTTPException(404, 'CONTEXT_COMPRESSION_MODEL_UNAVAILABLE') from None
    except SQLAlchemyError:
        raise HTTPException(503, 'CONTEXT_STORAGE_UNAVAILABLE') from None


@router.get('/api/conversations/{conversation_id}/context/compressions')
async def listing(conversation_id: int, response: Response, user: Owner, request_key: str | None = Query(default=None, max_length=128)):
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return await compaction.listing(session, conversation_id, user.id, request_key)


@router.post('/api/conversations/{conversation_id}/context/compressions/{compression_id}/cancel')
async def cancel(conversation_id: int, compression_id: str, response: Response, user: Owner):
    response.headers['Cache-Control'] = 'no-store'
    return await compaction.cancel(conversation_id, user.id, compression_id)


@router.post('/api/conversations/{conversation_id}/context/restore')
async def restore(conversation_id: int, payload: Restore, response: Response, user: Owner):
    response.headers['Cache-Control'] = 'no-store'
    try:
        await compaction.restore(conversation_id, user.id, payload)
        async with SessionLocal() as session:
            return await context_view(session, conversation_id)
    except SQLAlchemyError:
        raise HTTPException(503, 'CONTEXT_STORAGE_UNAVAILABLE') from None
