"""Owner 管理自动压缩策略；读取不压缩，更新仅影响后续调用边界。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from ..context import policy
from ..context.compaction import lock_material
from ..db import SessionLocal, with_locked_retry
from ..models import Conversation, InstanceSettings, User
from ..security import require_owner
from ..workflows.service import owned

router = APIRouter(tags=['context-policy'])
Owner = Annotated[User, Depends(require_owner)]


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0, le=2**31 - 1)
    policy: policy.Policy | None


@router.get('/api/context-policy')
async def get_world(response: Response, user: Owner):
    """读取当前物理 World 的默认策略，不触发维护或模型调用。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return await policy.world_view(session)


@router.get('/api/conversations/{conversation_id}/context/policy')
async def get_conversation(conversation_id: int, response: Response, user: Owner):
    """校验 Owner 成员身份，并根据当前有效角色动态重算上下限。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        await owned(session, conversation_id, user.id)
        return await policy.resolve(session, conversation_id)


async def save(payload, uid, cid=None):
    """短事务 CAS 保存原配置；会话显式阈值不可超过当前最小窗口。"""
    async def operation():
        async with SessionLocal() as session:
            if cid is not None:
                await owned(session, cid, uid)
                await lock_material(session, cid)
                view = await policy.resolve(session, cid)
                if payload.policy is not None:
                    if payload.policy.trigger_tokens is not None and payload.policy.trigger_tokens > view['limits']['ceiling_tokens']:
                        raise HTTPException(422, 'CONTEXT_THRESHOLD_EXCEEDS_WINDOW')
                    if payload.policy.model_role_id is not None and payload.policy.model_role_id not in {r['role_id'] for r in view['limits']['roles']}:
                        raise HTTPException(422, 'CONTEXT_COMPRESSION_MODEL_UNAVAILABLE')
            table, identity = (Conversation, cid) if cid is not None else (InstanceSettings, 1)
            changed = await session.execute(update(table).where(table.id == identity,
                table.context_policy_revision == payload.expected_revision).values(
                context_policy_json=payload.policy.model_dump() if payload.policy else None,
                context_policy_revision=table.context_policy_revision + 1))
            if changed.rowcount != 1:
                raise HTTPException(409, 'CONTEXT_POLICY_CONFLICT')
            result = await policy.resolve(session, cid) if cid is not None else await policy.world_view(session)
            await session.commit()
            return result
    try:
        return await with_locked_retry(operation)
    except SQLAlchemyError:
        raise HTTPException(503, 'CONTEXT_STORAGE_UNAVAILABLE') from None


@router.put('/api/context-policy')
async def put_world(payload: Update, response: Response, user: Owner):
    """修改本世界默认，继承它的会话在后续调用边界取得新策略。"""
    response.headers['Cache-Control'] = 'no-store'
    return await save(payload, user.id)


@router.put('/api/conversations/{conversation_id}/context/policy')
async def put_conversation(conversation_id: int, payload: Update, response: Response, user: Owner):
    """保存会话覆盖或恢复继承，不改写历史消息和已发出的请求。"""
    response.headers['Cache-Control'] = 'no-store'
    return await save(payload, user.id, conversation_id)
