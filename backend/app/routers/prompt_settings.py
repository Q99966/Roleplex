"""Owner 的当前 World/会话提示词配置与按角色的生效预览。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings
from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import AgentExecution, Conversation, ConversationMember, InstanceSettings, Role, User
from ..security import require_owner
from ..context.prompts import PLATFORM_DEFAULT, PLATFORM_TEMPLATE_VERSION, RUNTIME_RULES, resolve_prompt_layers
from ..agent.capabilities import resolve_capabilities
from ..workflows.service import owned

router = APIRouter(tags=['prompt-settings'])
Owner = Annotated[User, Depends(require_owner)]


class WorldPromptUpdate(BaseModel):
    """null 继承平台默认；空字符串显式清空该层。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0, le=2**31 - 1)
    platform_override: str | None = Field(max_length=100_000)
    world_prompt: str = Field(max_length=100_000)


class ConversationPromptUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0, le=2**31 - 1)
    prompt: str = Field(max_length=100_000)


def world_view(row: InstanceSettings):
    return {'world_name': settings.world_name, 'revision': row.prompt_revision,
        'platform_override': row.platform_prompt_override, 'world_prompt': row.world_prompt,
        'platform_default': PLATFORM_DEFAULT, 'template_version': PLATFORM_TEMPLATE_VERSION,
        'runtime_rules': RUNTIME_RULES, 'updated_at': row.prompt_updated_at}


def conversation_view(row: Conversation):
    return {'conversation_id': row.id, 'revision': row.prompt_revision, 'prompt': row.system_prompt, 'updated_at': row.prompt_updated_at}


@router.get('/api/prompt-settings')
async def get_world_prompts(response: Response, user: Owner):
    """仅返回当前物理 World 配置，读取不创建版本或调用模型。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return world_view(await session.get(InstanceSettings, 1))


@router.put('/api/prompt-settings')
async def put_world_prompts(payload: WorldPromptUpdate, response: Response, user: Owner):
    """在短事务中 CAS 更新两层配置，成功后供下一次输入构建使用。"""
    response.headers['Cache-Control'] = 'no-store'
    async def operation():
        async with SessionLocal() as session:
            changed = await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1,
                InstanceSettings.prompt_revision == payload.expected_revision).values(
                platform_prompt_override=payload.platform_override, world_prompt=payload.world_prompt,
                prompt_revision=InstanceSettings.prompt_revision + 1, prompt_updated_at=now_utc()))
            if changed.rowcount != 1:
                raise HTTPException(409, 'PROMPT_REVISION_CONFLICT')
            value = world_view(await session.get(InstanceSettings, 1))
            await session.commit()
            return value
    try:
        return await with_locked_retry(operation)
    except SQLAlchemyError:
        # SQL 参数可能含私有提示词，不把原异常交给通用错误日志。
        raise HTTPException(503, 'PROMPT_STORAGE_UNAVAILABLE') from None


@router.get('/api/conversations/{conversation_id}/prompt-settings')
async def get_conversation_prompts(conversation_id: int, response: Response, user: Owner):
    """Owner 仍须为有效会话成员；提示词不广播给 Guest。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return conversation_view(await owned(session, conversation_id, user.id))


@router.put('/api/conversations/{conversation_id}/prompt-settings')
async def put_conversation_prompts(conversation_id: int, payload: ConversationPromptUpdate, response: Response, user: Owner):
    """提交只修改目标会话提示词；消息、工具分配及已经发出的请求保持原事实。"""
    response.headers['Cache-Control'] = 'no-store'
    async def operation():
        from ..workflows.service import control_lock
        async with control_lock:
            async with SessionLocal() as session:
                await owned(session, conversation_id, user.id)
                changed = await session.execute(update(Conversation).where(Conversation.id == conversation_id,
                    Conversation.prompt_revision == payload.expected_revision, Conversation.deleted_at.is_(None)).values(
                    system_prompt=payload.prompt, prompt_revision=Conversation.prompt_revision + 1, prompt_updated_at=now_utc()))
                if changed.rowcount != 1:
                    raise HTTPException(409, 'PROMPT_REVISION_CONFLICT')
                value = conversation_view(await session.get(Conversation, conversation_id))
                await session.commit()
                return value
    try:
        return await with_locked_retry(operation)
    except SQLAlchemyError:
        raise HTTPException(503, 'PROMPT_STORAGE_UNAVAILABLE') from None


@router.get('/api/conversations/{conversation_id}/prompt-preview')
async def preview_prompts(conversation_id: int, role_id: int, response: Response, user: Owner):
    """预览普通角色下一次组装的指令来源与能力；不是工作流任务授权或完整历史预览。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        conversation = await owned(session, conversation_id, user.id)
        role = await session.scalar(select(Role).join(ConversationMember,
            (ConversationMember.member_type == 'role') & (ConversationMember.member_id == Role.id)).where(
            ConversationMember.conversation_id == conversation_id, Role.id == role_id,
            Role.created_by == user.id, Role.active.is_(True), Role.deleted_at.is_(None)))
        if role is None:
            raise HTTPException(404, 'ROLE_NOT_FOUND')
        prompts = await resolve_prompt_layers(session, conversation, role)
        capabilities = await resolve_capabilities(session, conversation=conversation, role=role, triggered_by_user_id=user.id)
        previous = await session.scalar(select(AgentExecution).where(AgentExecution.conversation_id == conversation_id,
            AgentExecution.execution_kind != 'context_compact',
            AgentExecution.role_id == role.id, AgentExecution.context_snapshot_json.is_not(None)).order_by(AgentExecution.id.desc()).limit(1))
        return {'world_name': settings.world_name, 'conversation_id': conversation_id, 'role_id': role.id,
            'role_name': role.name, 'configured_tools': role.builtin_tools_json or [],
            'revisions': prompts.revisions, 'layers': prompts.layers, 'capabilities': capabilities.view(),
            'latest_execution': {'execution_id': previous.execution_id, 'status': previous.status,
                'snapshot': previous.context_snapshot_json} if previous else None}
