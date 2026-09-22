"""Owner 的共享材料与按角色请求预览；读取不调用模型或改写聊天。"""
from typing import Annotated
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from ..context import build_context, ContextBuildRequest
from ..context.domain import ContextBuildError
from ..db import SessionLocal
from ..models import AgentExecution, ConversationContext, ConversationContextEntry as Entry, ConversationMember, Message, ModelCallUsage, Role, User
from ..security import require_owner
from ..workflows.service import owned

router = APIRouter(tags=['conversation-context'])
Owner = Annotated[User, Depends(require_owner)]


def utc_time(value):
    """SQLite 读回的无时区时间仍是服务端 UTC，显式标注供浏览器正确本地化。"""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class PreviewInput(BaseModel):
    """普通角色下一次输入；草稿不落库，也不获得任何工作流授权。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    role_id: int = Field(gt=0)
    draft: str = Field(default='', max_length=100_000)
    include_content: bool = False


async def context_view(session, cid):
    """用聚合读取来源数量和同步边界；不加载全部正文。"""
    row = await session.get(ConversationContext, cid)
    if row is None:
        raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
    pending = (Entry.state == 'pending') & Message.status.in_(['pending', 'generating'])
    invalid = await session.scalar(select(Entry.message_id).join(Message, Message.id == Entry.message_id).where(
        Entry.conversation_id == cid, or_(Message.conversation_id != cid, and_(~pending,
            or_(Entry.source_revision != Message.revision, Entry.source_status != Message.status)))).limit(1))
    if invalid is not None:
        # 投影不是权限事实；缓存错误或绕过 ORM 的旧写入不能返回其他会话/旧版本正文。
        raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
    revision = row.revision
    counts = {'included': 0, 'pending': 0, 'excluded': 0}
    reasons, text_bytes, through = {}, 0, 0
    rows = (await session.execute(select(Entry.state, Entry.reason, func.count(), func.sum(Entry.text_bytes),
        func.max(Entry.message_id)).where(Entry.conversation_id == cid).group_by(Entry.state, Entry.reason))).all()
    for state, reason, count, size, last in rows:
        counts[state] = counts.get(state, 0) + count
        if reason:
            reasons[reason] = reasons.get(reason, 0) + count
        text_bytes += int(size or 0)
        through = max(through, last or 0)
    from ..context.summaries import current_summary
    from ..models import ContextCompression, ContextCompressionSource
    summary, unavailable = await current_summary(session, cid)
    active_summary = None
    represented = 0
    if summary:
        job = await session.get(ContextCompression, summary.id)
        represented = int(await session.scalar(select(func.sum(Entry.text_bytes)).where(Entry.conversation_id == cid,
            Entry.message_id.in_(select(ContextCompressionSource.message_id).where(ContextCompressionSource.compression_id == summary.id)))) or 0)
        active_summary = {'id': summary.id, 'source_count': job.source_count, 'through_message_id': job.through_message_id,
            'text': summary.text, 'text_bytes': summary.text_bytes, 'created_at': utc_time(summary.created_at)}
    return {'conversation_id': cid, 'revision': revision, 'projection_version': row.projection_version,
        'updated_at': utc_time(row.updated_at), 'counts': counts, 'excluded_reasons': reasons,
        'text_bytes': text_bytes, 'through_message_id': through, 'active_summary': active_summary,
        'summary_unavailable': unavailable, 'material_text_bytes': text_bytes - represented + (summary.text_bytes if summary else 0)}


async def verify_revision(session, cid, revision):
    if revision != await session.scalar(select(ConversationContext.revision).where(ConversationContext.conversation_id == cid)):
        raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')


async def verify_access(cid, uid, rid=None):
    """读后以新事务复核撤权/删除，长预览不能返回失效范围的私有材料。"""
    async with SessionLocal() as fresh:
        await owned(fresh, cid, uid)
        if rid is not None:
            from ..context.access import require_context_access
            await require_context_access(fresh, conversation_id=cid, role_id=rid, user_id=uid)


@router.get('/api/conversations/{conversation_id}/context')
async def get_context(conversation_id: int, response: Response, user: Owner,
                      before: int | None = Query(default=None, gt=0),
                      expected_revision: int | None = Query(default=None, ge=0),
                      limit: int = Query(default=30, ge=1, le=100)):
    """按业务消息 ID 翻页查看共享来源；版本变化时拒绝拼接旧页。"""
    response.headers['Cache-Control'] = 'no-store'
    try:
        async with SessionLocal() as session:
            await owned(session, conversation_id, user.id)
            view = await context_view(session, conversation_id)
            if expected_revision is not None and expected_revision != view['revision']:
                raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
            query = select(Entry.message_id, Entry.source_revision, Entry.source_status, Entry.sender_type,
                Entry.sender_id, Entry.state, Entry.reason, Entry.text_bytes, Entry.created_at,
                func.substr(Entry.text, 1, 4001).label('text')).where(Entry.conversation_id == conversation_id)
            if before is not None:
                query = query.where(Entry.message_id < before)
            rows = (await session.execute(query.order_by(Entry.message_id.desc()).limit(limit + 1))).all()
            entries = [{'message_id': row.message_id, 'revision': row.source_revision, 'status': row.source_status,
                'sender_type': row.sender_type, 'sender_id': row.sender_id, 'state': row.state, 'reason': row.reason,
                'text': row.text[:4000], 'text_truncated': len(row.text) > 4000,
                'text_bytes': row.text_bytes, 'created_at': utc_time(row.created_at)} for row in rows[:limit]]
            await verify_revision(session, conversation_id, view['revision'])
            await verify_access(conversation_id, user.id)
            return {**view, 'entries': entries, 'next_before': entries[-1]['message_id'] if len(rows) > limit else None}
    except SQLAlchemyError:
        raise HTTPException(503, 'CONTEXT_STORAGE_UNAVAILABLE') from None


async def latest_call(session, cid, rid):
    """最新实际模型调用与首次组装来源；运行中工具轮增长不与累计用量混用。"""
    row = (await session.execute(select(ModelCallUsage, AgentExecution).join(AgentExecution,
        AgentExecution.execution_id == ModelCallUsage.execution_id).where(
        AgentExecution.conversation_id == cid, AgentExecution.role_id == rid)
        .order_by(ModelCallUsage.id.desc()).limit(1))).first()
    if row is None:
        return None
    call, execution = row
    return {'execution_id': execution.execution_id, 'execution_kind': execution.execution_kind,
        'execution_status': execution.status, 'call_index': call.call_index, 'status': call.status,
        'recorded_at': utc_time(call.recorded_at), 'model_name': call.model_name, 'provider_mode': call.provider_mode,
        'input_estimate': call.input_estimate_json,
        'provider_usage': {'input_tokens': call.input_tokens, 'output_tokens': call.output_tokens,
            'cache_hit_tokens': call.cache_hit_tokens, 'cache_write_tokens': call.cache_write_tokens},
        'snapshot': execution.context_snapshot_json}


@router.post('/api/conversations/{conversation_id}/context/preview')
async def preview_context(conversation_id: int, payload: PreviewInput, response: Response, user: Owner):
    """与实际派发共用 ContextBuilder；大规则仍可预览超预算原因，草稿不会发送。"""
    response.headers['Cache-Control'] = 'no-store'
    # 正常消息恰好收口时用新的短读事务重试，避免混用两个材料版本。
    try:
        for attempt in range(3):
            try:
                async with SessionLocal() as session:
                    conversation = await owned(session, conversation_id, user.id)
                    role = await session.scalar(select(Role).join(ConversationMember,
                        (ConversationMember.member_type == 'role') & (ConversationMember.member_id == Role.id)).where(
                        ConversationMember.conversation_id == conversation_id, Role.id == payload.role_id,
                        Role.created_by == user.id, Role.active.is_(True), Role.deleted_at.is_(None)))
                    if role is None:
                        raise HTTPException(404, 'ROLE_NOT_FOUND')
                    built = await build_context(session, ContextBuildRequest(role_id=role.id,
                        conversation_id=conversation.id, current_message_id=None, triggered_by_user_id=user.id,
                        draft_text=payload.draft), enforce_budget=False)
                    view = await context_view(session, conversation_id)
                    if view['revision'] != built.material_snapshot['revision']:
                        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
                    previous = await latest_call(session, conversation_id, role.id)
                    await verify_access(conversation_id, user.id, role.id)
                    return {'role_id': role.id, 'role_name': role.name, 'model_name': role.model_name,
                        'shared': view, 'material': built.material_snapshot, 'request': built.request_estimate,
                        'latest_call': previous,
                        'messages': [{'type': 'system', 'content': built.system_prompt},
                            *[{'type': message.type, 'content': message.content} for message in built.history],
                            {'type': 'human', 'content': built.current_message}] if payload.include_content else None}
            except ContextBuildError as exc:
                if str(exc) == 'CONTEXT_SOURCE_CHANGED' and attempt < 2:
                    continue
                code = str(exc) if str(exc) in {'CONTEXT_SOURCE_CHANGED', 'ROLE_NOT_AVAILABLE', 'CONVERSATION_NOT_FOUND'} else 'CONTEXT_PREVIEW_UNAVAILABLE'
                raise HTTPException(409 if code == 'CONTEXT_SOURCE_CHANGED' else 404 if code == 'CONVERSATION_NOT_FOUND' else 422, code) from None
    except SQLAlchemyError:
        raise HTTPException(503, 'CONTEXT_STORAGE_UNAVAILABLE') from None
