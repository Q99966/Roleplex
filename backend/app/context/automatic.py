"""自动维护在原任务拥有的子任务内运行，不排到同会话队列后等待自身。"""
import asyncio
import hashlib
import json
from weakref import WeakValueDictionary
from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal, now_utc
from ..models import (AgentExecution, ContextCompression, ConversationContext, ConversationContextEntry as Entry,
    Generation, Message, Role)
from . import compaction, policy
from .access import require_context_access
from .compaction_schema import Start
from .domain import ContextBuildError
from .fingerprint import stable_hash

# 进程内调度下仅共享会话材料采用 single-flight；私有工具输入从不跨 execution 复用。
_shared_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_inline: dict[int, asyncio.Task] = {}


async def validate_parent(session, execution_id, cid, uid):
    """创建及每次收费调用前复核原任务、停止标志和成员授权。"""
    parent = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
    generation = await session.get(Generation, parent.generation_id) if parent else None
    if parent is None or parent.conversation_id != cid or parent.status != 'running' or generation is None or generation.stop_requested_at:
        raise asyncio.CancelledError()
    await require_context_access(session, conversation_id=cid, role_id=parent.role_id, user_id=uid)
    from ..workflows.allocations import allowed
    if not await allowed(session, execution_id):
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
    from ..memory.service import revalidate_execution
    await revalidate_execution(session, execution_id, cid, parent.role_id, uid,
        (parent.context_snapshot_json or {}).get('material'))
    return parent


async def validate_job(session, job):
    """原任务、策略、模型与来源版本都仍适用时才继续收费或采用结果。"""
    from .compaction_runner import CompactionFailure
    runtime = job.runtime_json
    await validate_parent(session, runtime['parent_execution_id'], job.conversation_id, job.owner_id)
    view = await policy.resolve(session, job.conversation_id)
    if not view['effective']['enabled'] or view['stamp'] != runtime['policy_stamp']:
        raise CompactionFailure('CONTEXT_POLICY_CHANGED', 'stale')
    role = await session.get(Role, job.role_id)
    if role.model_name != job.model_snapshot_json['model_name'] or role.revision != job.model_snapshot_json['role_revision']:
        raise CompactionFailure('CONTEXT_COMPRESSION_MODEL_CHANGED', 'stale')
    for source in runtime.get('sources', []):
        row = await session.get(Message, source['message_id'])
        if row is None or row.conversation_id != job.conversation_id or row.revision != source['revision']:
            raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_CHANGED', 'stale')


async def stop_inline(generation_id):
    """取消等待摘要的模型子任务，并等它完成 usage 与终态收口。"""
    task = _inline.get(generation_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def execute(job_id, *, private_units=None):
    """根执行取消向子任务传播；Owner 单独取消维护时原任务仍可继续。"""
    from .compaction_runner import run
    async with SessionLocal() as session:
        job = await session.get(ContextCompression, job_id)
        if job.status != 'queued':
            return None
        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == job.execution_id))
        generation = await session.get(Generation, execution.generation_id)
        execution.status = generation.status = 'running'
        execution.started_at = generation.started_at = now_utc()
        await session.commit()
    async def child():
        from ..config.logging import set_log_context
        set_log_context(execution_id=execution.execution_id, parent_execution_id=execution.parent_execution_id,
            generation_id=generation.id, chain_id=execution.chain_id, role_id=job.role_id)
        return await run(generation_id=generation.id, conversation_id=job.conversation_id, execution_id=job.execution_id,
            triggered_by_user_id=job.owner_id, target_role_id=job.role_id, private_units=private_units)
    task = asyncio.create_task(child())
    _inline[generation.id] = task
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return None
    finally:
        _inline.pop(generation.id, None)


async def shared(request, context):
    """在构建后、发出原请求前压缩公共历史；返回是否应重新构建相同任务输入。"""
    if context.material_snapshot['scope'] != 'conversation' or context.request_estimate['blocked']:
        return False
    cid, uid = request.conversation_id, request.triggered_by_user_id
    async with SessionLocal() as session:
        view = await policy.resolve(session, cid)
    effective = view['effective']
    amount = context.request_estimate['before_truncation_tokens'] - sum(
        context.request_estimate['breakdown'][key] for key in ['system', 'tools', 'current', 'interruption'])
    if not effective['enabled'] or amount < effective['trigger_tokens']:
        return False
    lock = _shared_locks.setdefault(cid, asyncio.Lock())
    async with lock:
        async with SessionLocal() as session:
            state = await session.get(ConversationContext, cid)
            # 其他维护已经发布时重新组装，不能继续按旧压力重复收费。
            if state.revision != context.material_snapshot['revision']:
                return True
            latest = await session.scalar(select(ContextCompression).where(ContextCompression.conversation_id == cid,
                ContextCompression.trigger == 'automatic', ContextCompression.scope == 'conversation')
                .order_by(ContextCompression.created_at.desc()).limit(1))
            if latest:
                elapsed = (now_utc() - latest.updated_at.replace(tzinfo=timezone.utc)).total_seconds()
                new_size = await session.scalar(select(func.sum(Entry.text_bytes)).where(Entry.conversation_id == cid,
                    Entry.state == 'included', Entry.message_id > latest.runtime_json['boundary'],
                    Entry.message_id < request.current_message_id)) or 0
                if elapsed < effective['cooldown_seconds'] or new_size < effective['min_new_tokens']:
                    return False
            if await session.scalar(select(ContextCompression.id).where(ContextCompression.active_conversation_id == cid)):
                # 手动维护可能排在当前任务后面，不能在这里等待它而锁死队列。
                return False
            revision = state.revision
            # 所有来源版本参与幂等身份；pending 占位的增量不改变这个身份。
            digest, after = hashlib.sha256(), 0
            while True:
                rows = (await session.execute(select(Entry.message_id, Entry.source_revision, Entry.text_hash).where(
                    Entry.conversation_id == cid, Entry.state == 'included', Entry.pinned.is_(False),
                    Entry.message_id > after, Entry.message_id < request.current_message_id).order_by(Entry.message_id).limit(200))).all()
                if not rows:
                    break
                for row in rows:
                    digest.update(json.dumps(list(row), separators=(',', ':')).encode())
                after = rows[-1].message_id
            signature = digest.hexdigest()
        runtime = {'parent_execution_id': request.execution_id, 'policy_stamp': view['stamp'],
            'boundary': request.current_message_id - 1, 'source_signature': signature,
            'target_material_tokens': effective['target_tokens']}
        try:
            value = await compaction.start(cid, uid, Start(request_key='auto:' + stable_hash([signature, view['stamp']]),
                expected_revision=revision, role_id=effective['model_role_id'] or request.role_id,
                keep_recent=effective['keep_recent'], target_tokens=min(effective['summary_tokens'], max(128, effective['target_tokens'])),
                instructions=effective['instructions']), runtime=runtime)
            if value['status'] == 'queued':
                await execute(value['id'])
            return True
        except (HTTPException, ContextBuildError, SQLAlchemyError):
            # 配置/来源竞争保留原材料；后续实际输入仍经过硬预算拒绝。
            return False


async def prepare(request):
    """压缩前以未裁剪需求判断压力，压缩后再组装；模型等待不占用读写事务。"""
    from .builder import build_context
    async def read():
        for attempt in range(3):
            try:
                async with SessionLocal() as session:
                    return await build_context(session, request, enforce_budget=False)
            except ContextBuildError as exc:
                if str(exc) != 'CONTEXT_SOURCE_CHANGED' or attempt == 2:
                    raise
    context = await read()
    from .budget import token_estimate
    from .domain import ContextBudgetExceeded
    fixed = token_estimate(sum(context.request_estimate['breakdown'][key] for key in ['system', 'tools', 'current']))
    if fixed.estimated_tokens + fixed.safety_margin_tokens > context.budget.input_budget_tokens:
        raise ContextBudgetExceeded(estimated_tokens=fixed.estimated_tokens, safety_margin_tokens=fixed.safety_margin_tokens,
            input_budget_tokens=context.budget.input_budget_tokens, estimator_kind=fixed.estimator_kind)
    if await shared(request, context):
        context = await read()
    return context


async def close_parent(execution_id):
    """覆盖创建已提交但根任务尚未开始 await 子任务就取消的间隙。"""
    from .compaction_runner import finish
    async with SessionLocal() as session:
        rows = (await session.execute(select(ContextCompression.id, AgentExecution.generation_id).join(
            AgentExecution, AgentExecution.execution_id == ContextCompression.execution_id).where(
            AgentExecution.parent_execution_id == execution_id, ContextCompression.trigger == 'automatic',
            ContextCompression.status.not_in(compaction.TERMINAL)))).all()
    for job_id, generation_id in rows:
        await stop_inline(generation_id)
        await finish(job_id, 'interrupted', 'EXECUTION_INTERRUPTED')
