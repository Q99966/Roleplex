"""压缩维护请求的版本、幂等、发布和取消；模型调用在独立的 runner 中完成。"""
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, insert, literal, select, update

from ..config import settings
from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import (AgentExecution, ContextCompression, ContextCompressionSource as Source, ContextSummary,
    Conversation, ConversationContext, ConversationContextEntry as Entry, Generation, InstanceSettings, ModelConfig, QueueJob, Role, WorkflowBudget)
from ..realtime import store as events
from ..realtime.events import current_epoch
from ..workflows.service import owned
from .access import require_context_access
from .budget import estimate_text_tokens
from .compaction_schema import PROMPT_VERSION, RULES
from .fingerprint import stable_hash
from .summaries import current_summary, sources_valid

TERMINAL = {'completed', 'unchanged', 'failed', 'stale', 'cancelled', 'interrupted'}


def model_identity(config):
    return stable_hash([config.id, config.provider_type, config.base_url, config.api_key_encrypted, config.capability_overrides_json])


def job_view(job):
    from ..routers.conversation_context import utc_time
    return {key: getattr(job, key) for key in ('id', 'request_key', 'role_id', 'execution_id', 'source_revision', 'base_summary_id',
        'through_message_id', 'keep_recent', 'target_tokens', 'instructions', 'source_count', 'input_tokens_estimate',
        'output_tokens_estimate', 'completed_calls', 'phase', 'status', 'error_code', 'cancel_requested')} | {
        'model_name': job.model_snapshot_json['model_name'], 'created_at': utc_time(job.created_at), 'updated_at': utc_time(job.updated_at)}


async def lock_material(session, cid):
    """与消息投影使用相同的会话行锁；只在短业务写事务内持有。"""
    await session.execute(update(Conversation).where(Conversation.id == cid).values(event_seq=Conversation.event_seq))


async def changed(session, cid, job=None):
    state = await session.get(ConversationContext, cid)
    return await events.append_event(session, cid, 'context_updated', {'revision': state.revision,
        'compression_id': job.id if job else None, 'status': job.status if job else 'restored'})


async def start(cid, uid, payload, request_id=None):
    """固定来源和模型并入既有队列；相同请求键只创建一次维护额度及 execution。"""
    request_hash = stable_hash(payload.model_dump(exclude={'request_key'}))
    async def operation():
        async with SessionLocal() as session:
            conversation = await owned(session, cid, uid)
            await lock_material(session, cid)
            existing = await session.scalar(select(ContextCompression).where(ContextCompression.conversation_id == cid,
                ContextCompression.owner_id == uid, ContextCompression.request_key == payload.request_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, 'CONTEXT_COMPRESSION_REQUEST_CONFLICT')
                return job_view(existing), None, None
            await require_context_access(session, conversation_id=cid, role_id=payload.role_id, user_id=uid)
            role = await session.get(Role, payload.role_id)
            config = await session.get(ModelConfig, role.model_config_id) if role.model_config_id else None
            if role.created_by != uid or config is None or config.created_by != uid:
                raise HTTPException(422, 'CONTEXT_COMPRESSION_MODEL_UNAVAILABLE')
            window = min(role.context_window_tokens, settings.max_context_tokens)
            if payload.target_tokens >= window:
                raise HTTPException(422, 'CONTEXT_COMPRESSION_BUDGET_EXCEEDED')
            state = await session.get(ConversationContext, cid)
            if state.revision != payload.expected_revision:
                raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
            if await session.scalar(select(ContextCompression.id).where(ContextCompression.active_conversation_id == cid)):
                raise HTTPException(409, 'CONTEXT_COMPRESSION_BUSY')
            pointer = await session.scalar(select(ContextSummary.id).where(ContextSummary.active_conversation_id == cid))
            base, _ = await current_summary(session, cid)
            selected = [Entry.conversation_id == cid, Entry.state == 'included', Entry.pinned.is_(False)]
            earliest_pending = await session.scalar(select(func.min(Entry.message_id)).where(Entry.conversation_id == cid, Entry.state == 'pending'))
            if earliest_pending:
                selected.append(Entry.message_id < earliest_pending)
            uncovered = ~select(Source.message_id).where(Source.compression_id == base.id, Source.message_id == Entry.message_id).exists() if base else True
            cutoff = payload.through_message_id
            if cutoff is not None:
                target = await session.get(Entry, cutoff)
                if target is None or target.conversation_id != cid or target.state != 'included':
                    raise HTTPException(422, 'CONTEXT_COMPRESSION_RANGE_INVALID')
            if cutoff is None:
                # 最近保留的是尚未压缩的完整消息；既有摘要作为不可拆分的输入单元。
                cutoff = await session.scalar(select(Entry.message_id).where(*selected, uncovered)
                    .order_by(Entry.message_id.desc()).offset(payload.keep_recent).limit(1))
                if cutoff is None and base:
                    cutoff = (await session.get(ContextCompression, base.id)).through_message_id
            if cutoff is None:
                raise HTTPException(422, 'CONTEXT_NOTHING_TO_COMPRESS')
            if earliest_pending and cutoff >= earliest_pending:
                raise HTTPException(409, 'CONTEXT_COMPRESSION_RANGE_PENDING')
            if base and cutoff < (await session.get(ContextCompression, base.id)).through_message_id:
                raise HTTPException(422, 'CONTEXT_COMPRESSION_RANGE_INVALID')
            selected.append(Entry.message_id <= cutoff)
            count, size = (await session.execute(select(func.count(), func.sum(Entry.text_bytes)).where(*selected))).one()
            if not count:
                raise HTTPException(422, 'CONTEXT_NOTHING_TO_COMPRESS')
            if base:
                size = int(await session.scalar(select(func.sum(Entry.text_bytes)).where(*selected, uncovered)) or 0) + base.text_bytes
            chain, execution_id, job_id = uuid4().hex, uuid4().hex, uuid4().hex
            now = now_utc()
            generation = Generation(conversation_id=cid, stream_epoch=current_epoch(), status='queued', run_id=chain)
            session.add(generation); await session.flush()
            execution = AgentExecution(execution_id=execution_id, generation_id=generation.id, conversation_id=cid,
                chain_id=chain, role_id=role.id, execution_kind='context_compact', status='queued', created_at=now)
            session.add(execution); await session.flush()
            policy = await session.get(InstanceSettings, 1)
            session.add(WorkflowBudget(chain_id=chain, conversation_id=cid, trigger_message_id=None,
                decision_limit=policy.decision_limit, configuration_revision=policy.budget_revision, used_decisions=0, created_at=now))
            from ..agent.providers import capabilities_for, filter_params
            params = filter_params(role.params_json or {}, provider_type=config.provider_type, capabilities=capabilities_for(config))
            job = ContextCompression(id=job_id, conversation_id=cid, owner_id=uid, role_id=role.id, execution_id=execution_id,
                request_key=payload.request_key, request_hash=request_hash, active_conversation_id=cid,
                source_revision=state.revision, base_summary_id=pointer, base_summary_revision=state.summary_revision,
                through_message_id=cutoff, keep_recent=payload.keep_recent,
                target_tokens=payload.target_tokens, instructions=payload.instructions, source_count=count,
                input_tokens_estimate=int(size or 0), status='queued', phase='queued', completed_calls=0, cancel_requested=False,
                prompt_snapshot_json={'version': PROMPT_VERSION, 'rules': RULES, 'conversation_revision': conversation.prompt_revision,
                    'conversation_requirements': conversation.system_prompt},
                model_snapshot_json={'model_config_id': config.id, 'config_hash': model_identity(config), 'model_name': role.model_name,
                    'context_window_tokens': window, 'params': {**params, 'max_tokens': payload.target_tokens},
                    'role_revision': role.revision, 'use_base_summary': base is not None}, created_at=now, updated_at=now)
            session.add(job); await session.flush()
            await session.execute(insert(Source).from_select(['compression_id', 'message_id', 'source_revision', 'source_status', 'text_hash'],
                select(literal(job.id), Entry.message_id, Entry.source_revision, Entry.source_status, Entry.text_hash).where(*selected)))
            queued = QueueJob(conversation_id=cid, generation_id=generation.id, status='queued', payload_json={
                'current_message_id': None, 'triggered_by_user_id': uid, 'allow_dangerous': False, 'request_id': request_id},
                attempts=0, cancel_requested=False, created_at=now)
            session.add(queued); await session.flush()
            pending = await changed(session, cid, job)
            await session.commit()
            return job_view(job), queued.id, pending
    value, queue_id, pending = await with_locked_retry(operation)
    if pending:
        await events.publish_events(pending)
    if queue_id:
        from ..scheduling import conversation_scheduler
        await conversation_scheduler.enqueue(cid, queue_id)
    return value


async def listing(session, cid, uid, request_key=None):
    await owned(session, cid, uid)
    query = select(ContextCompression).where(ContextCompression.conversation_id == cid)
    if request_key is not None:
        query = query.where(ContextCompression.request_key == request_key)
    jobs = (await session.scalars(query
        .order_by(ContextCompression.created_at.desc()).limit(30))).all()
    versions = []
    for row in (await session.scalars(select(ContextSummary).where(ContextSummary.conversation_id == cid)
        .order_by(ContextSummary.created_at.desc()).limit(30))).all():
        job = await session.get(ContextCompression, row.id)
        valid = await sources_valid(session, job)
        versions.append({'id': row.id, 'active': row.active_conversation_id == cid, 'valid': valid,
            'text': row.text if valid else None, 'source_count': job.source_count, 'through_message_id': job.through_message_id,
            'input_tokens_estimate': job.input_tokens_estimate, 'output_tokens_estimate': job.output_tokens_estimate})
    from ..services.execution_usage import summary as usage_summary
    job_views = []
    for job in jobs:
        value = job_view(job)
        value['usage'] = await usage_summary(session, cid, job.role_id, job.execution_id)
        job_views.append(value)
    return {'jobs': job_views, 'versions': versions}


async def restore(cid, uid, payload):
    """只移动摘要指针；CAS 冲突不覆盖，新消息和原消息都不倒退。"""
    async def operation():
        async with SessionLocal() as session:
            await owned(session, cid, uid)
            await lock_material(session, cid)
            state = await session.get(ConversationContext, cid)
            if state.revision != payload.expected_revision:
                raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
            target = await session.get(ContextSummary, payload.summary_id) if payload.summary_id else None
            if payload.summary_id and (target is None or target.conversation_id != cid):
                raise HTTPException(404, 'CONTEXT_SUMMARY_NOT_FOUND')
            if target and not await sources_valid(session, await session.get(ContextCompression, target.id)):
                raise HTTPException(409, 'CONTEXT_SOURCE_CHANGED')
            await session.execute(update(ContextSummary).where(ContextSummary.active_conversation_id == cid).values(active_conversation_id=None))
            if target:
                target.active_conversation_id = cid
            state.revision += 1; state.summary_revision += 1; state.updated_at = now_utc()
            pending = await changed(session, cid)
            await session.commit()
        await events.publish_events(pending)
    await with_locked_retry(operation)


async def cancel(cid, uid, job_id):
    async def operation():
        async with SessionLocal() as session:
            await owned(session, cid, uid)
            await lock_material(session, cid)
            job = await session.get(ContextCompression, job_id)
            if job is None or job.conversation_id != cid:
                raise HTTPException(404, 'CONTEXT_COMPRESSION_NOT_FOUND')
            if job.status in TERMINAL:
                return job_view(job), None, None
            job.cancel_requested = True; job.updated_at = now_utc()
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == job.execution_id))
            generation = await session.get(Generation, execution.generation_id)
            generation.stop_requested_at = now_utc()
            if job.status == 'queued':
                job.status = 'cancelled'; job.phase = 'finished'; job.active_conversation_id = None
            else:
                job.status = 'stopping'
            pending = await changed(session, cid, job)
            await session.commit()
            return job_view(job), generation.id, pending
    value, generation_id, pending = await with_locked_retry(operation)
    if pending:
        await events.publish_events(pending)
    if generation_id:
        from ..scheduling import conversation_scheduler
        await conversation_scheduler.stop_generations(cid, [generation_id])
    return value


async def recover():
    """重启不重发模型调用；未完成任务明确中断，既有摘要及 usage 保留。"""
    async with SessionLocal() as session:
        await session.execute(update(ContextCompression).where(ContextCompression.status.in_(['queued', 'running', 'stopping']))
            .values(status='interrupted', phase='finished', error_code='EXECUTION_INTERRUPTED', active_conversation_id=None, updated_at=now_utc()))
        await session.commit()
