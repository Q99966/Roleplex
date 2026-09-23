"""工具关闭的分段压缩 runner；每次调用复用原 execution 的预算、usage 和取消。"""
import asyncio
import json
import logging
from time import perf_counter
from contextlib import aclosing
from dataclasses import replace

from fastapi import HTTPException
from sqlalchemy import func, select, update
from pydantic import ValidationError

from ..agent.domain import MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError
from ..agent.loop import run_agent
from ..config import settings
from ..db import SessionLocal, now_utc, with_locked_retry
from ..models import (AgentExecution, ContextCompression, ContextCompressionSource as Source, ContextSummary,
    ConversationContext, ConversationContextEntry as Entry, Generation, ModelConfig, Role)
from ..realtime import store as events
from ..services import agent_budget, execution_usage
from .access import require_context_access
from .budget import estimate_text_tokens, token_estimate
from .compaction import changed, lock_material, model_identity, TERMINAL
from .compaction_schema import SummaryContent, private_text
from .domain import ContextBuildError, CONTEXT_SCHEMA_VERSION
from .summaries import plain_text, sources_valid

logger = logging.getLogger('roleplex.context.compaction')


class CompactionFailure(Exception):
    """只有固定错误码跨越 runner 边界，不携带模型正文或 SQL 参数。"""
    def __init__(self, code, status='failed'):
        self.code, self.status = code, status
        super().__init__(code)


async def authorized(session, job):
    if job.cancel_requested or job.status in TERMINAL:
        raise asyncio.CancelledError()
    await require_context_access(session, conversation_id=job.conversation_id, role_id=job.role_id, user_id=job.owner_id)
    from ..workflows.service import owned
    await owned(session, job.conversation_id, job.owner_id)
    if job.trigger == 'automatic':
        from .automatic import validate_job
        await validate_job(session, job)
    if job.scope == 'conversation' and not await sources_valid(session, job):
        raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_CHANGED', 'stale')
    config = await session.get(ModelConfig, job.model_snapshot_json['model_config_id'])
    if config is None or config.created_by != job.owner_id or model_identity(config) != job.model_snapshot_json['config_hash']:
        raise CompactionFailure('CONTEXT_COMPRESSION_MODEL_CHANGED')


def prompt(job, items):
    return json.dumps({'purpose': 'context_compaction', 'target_tokens': job.target_tokens,
        'conversation_requirements': job.prompt_snapshot_json['conversation_requirements'],
        'instructions': job.instructions, 'items': items}, ensure_ascii=False, separators=(',', ':'))


def fits(job, items):
    count = estimate_text_tokens(job.prompt_snapshot_json['rules'], structural_tokens=8) + estimate_text_tokens(prompt(job, items), structural_tokens=8)
    estimate = token_estimate(count)
    return count + estimate.safety_margin_tokens + job.target_tokens <= job.model_snapshot_json['context_window_tokens']


async def units(job):
    """逐消息读取已冻结且仍有效的来源；模型等待期间不持有数据库事务。"""
    if job.model_snapshot_json['use_base_summary']:
        async with SessionLocal() as session:
            base = await session.get(ContextSummary, job.base_summary_id)
            if base is None:
                raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_CHANGED', 'stale')
            content = base.content_json
        yield {'summary': content, 'sources': sorted({mid for items in content.values() for item in items for mid in item['sources']})}
    after = 0
    while True:
        async with SessionLocal() as session:
            query = select(Source.message_id, Entry.text_bytes).join(Entry, Entry.message_id == Source.message_id).where(
                Source.compression_id == job.id, Source.message_id > after)
            if job.model_snapshot_json['use_base_summary']:
                from sqlalchemy.orm import aliased
                previous = aliased(Source)
                query = query.where(~select(previous.message_id).where(previous.compression_id == job.base_summary_id,
                    previous.message_id == Source.message_id).exists())
            rows = (await session.execute(query.order_by(Source.message_id).limit(100))).all()
        if not rows:
            return
        for mid, size in rows:
            if size >= job.model_snapshot_json['context_window_tokens'] - job.target_tokens:
                raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_TOO_LARGE')
            async with SessionLocal() as session:
                entry = await session.get(Entry, mid)
                unit = {'sources': [mid], 'sender_type': entry.sender_type, 'sender_id': entry.sender_id,
                    'status': entry.source_status, 'text': entry.text, 'execution_facts': entry.execution_facts_json}
            after = mid
            yield unit


def decode(text, allowed, target):
    try:
        text = text.strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        value = SummaryContent.model_validate_json(text).model_dump()
        facts = [item for items in value.values() for item in items]
        if not facts or any(not item['text'].strip() or not set(item['sources']).issubset(allowed) for item in facts):
            raise ValueError()
        size = estimate_text_tokens(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
        if size > target:
            raise CompactionFailure('CONTEXT_COMPRESSION_OUTPUT_TOO_LARGE')
        return value
    except (ValueError, ValidationError):
        raise CompactionFailure('CONTEXT_COMPRESSION_RESULT_INVALID') from None


async def record_event(job, event, index, *, completed, receipt=None):
    task = asyncio.create_task(execution_usage.record(job.execution_id, replace(event, call_index=index),
        'fake' if settings.agent_use_fake_provider else 'real', job.model_snapshot_json['model_name'],
        completed=completed, context_snapshot=receipt))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError()


async def call_model(job, items, index):
    """单次压缩也走同一 Agent 防腐层，只有一轮无工具请求；不另建调用计量。"""
    if not fits(job, items):
        raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_TOO_LARGE')
    async with SessionLocal() as session:
        current = await session.get(ContextCompression, job.id)
        await authorized(session, current)
        snapshot = job.model_snapshot_json
        role = Role(id=job.role_id, created_by=job.owner_id, model_config_id=snapshot['model_config_id'],
            model_name=snapshot['model_name'], context_window_tokens=snapshot['context_window_tokens'], params_json=snapshot['params'])
        from ..config.logging import set_log_context
        from ..services.chat import _provider_log_fields
        set_log_context(**await _provider_log_fields(session, role), context_schema_version=CONTEXT_SCHEMA_VERSION)
        if settings.agent_use_fake_provider:
            from ..agent.fake_provider import ContextCompactionModel
            model = ContextCompactionModel()
        else:
            from ..agent.providers import build_chat_model
            model = build_chat_model(role, await session.get(ModelConfig, role.model_config_id))

    denial = None
    async def permit(_index):
        nonlocal denial
        try:
            async with SessionLocal() as session:
                await authorized(session, await session.get(ContextCompression, job.id))
            if not await agent_budget.consume(job.execution_id, index, reserve=1 if job.trigger == 'automatic' else 0):
                raise CompactionFailure('CONTEXT_COMPRESSION_BUDGET_EXCEEDED')
            return True
        except CompactionFailure as exc:
            denial = exc
            return False
        except (HTTPException, ContextBuildError):
            denial = CompactionFailure('CONTEXT_COMPRESSION_SOURCE_CHANGED', 'stale')
            return False

    result = None
    async with aclosing(run_agent(model=model, tools=[], system_prompt=job.prompt_snapshot_json['rules'],
        prompt=prompt(job, items), decision_limit=1, before_decision=permit, provider_call_index_offset=index - 1)) as stream:
        async for event in stream:
            if isinstance(event, ProviderCallStarted):
                estimate = event.input_estimate or {}
                receipt = {'context_schema_version': CONTEXT_SCHEMA_VERSION, 'material': {'scope': 'context_compaction',
                    'compression_id': job.id, 'revision': job.source_revision, 'compression_scope': job.scope}, 'request': {
                    **estimate, 'effective_context_window': job.model_snapshot_json['context_window_tokens'],
                    'output_reserved_tokens': job.target_tokens}}
                await record_event(job, event, index, completed=False, receipt=receipt)
                logger.info('provider.call_started', extra={'provider_call_index': index, 'model': job.model_snapshot_json['model_name']})
            elif isinstance(event, ProviderCallCompleted):
                await record_event(job, event, index, completed=True)
                logger.info('provider.call_completed', extra={'provider_call_index': index, 'duration_ms': event.duration_ms,
                    'ttft_ms': event.ttft_ms, 'input_tokens': event.input_tokens, 'output_tokens': event.output_tokens,
                    'cache_hit_tokens': event.cache_hit_tokens, 'cache_write_tokens': event.cache_write_tokens})
            elif isinstance(event, ProviderError):
                raise CompactionFailure(event.code)
            elif isinstance(event, MessageDone):
                if denial:
                    raise denial
                if event.stop_reason != 'completed':
                    raise CompactionFailure('CONTEXT_COMPRESSION_BUDGET_EXCEEDED')
                result = event.text
    if result is None:
        raise CompactionFailure('CONTEXT_COMPRESSION_RESULT_INVALID')
    allowed = {mid for unit in items for mid in unit['sources']}
    value = decode(result, allowed, job.target_tokens)
    async def progress():
        async with SessionLocal() as session:
            row = await session.get(ContextCompression, job.id)
            row.completed_calls = index; row.updated_at = now_utc()
            pending = await changed(session, row.conversation_id, row)
            await session.commit()
        await events.publish_events(pending)
    await with_locked_retry(progress)
    return {'summary': value, 'sources': sorted({mid for items in value.values() for item in items for mid in item['sources']})}


async def finish(job_id, status, error_code=None, *, content=None):
    """发布和任务终态同事务；新消息可追加，原来源或活动指针变化则拒绝采用。"""
    async def operation():
        async with SessionLocal() as session:
            row = await session.get(ContextCompression, job_id)
            if row is None or row.status in TERMINAL:
                return
            await lock_material(session, row.conversation_id)
            row = await session.get(ContextCompression, job_id, populate_existing=True)
            if row.status in TERMINAL:
                return
            final, code = status, error_code
            if row.cancel_requested:
                final, code, content_to_publish = 'cancelled', None, None
            else:
                content_to_publish = content
            if content_to_publish is not None:
                try:
                    await authorized(session, row)
                    pointer = await session.scalar(select(ContextSummary.id).where(ContextSummary.active_conversation_id == row.conversation_id))
                    state = await session.get(ConversationContext, row.conversation_id)
                    if row.scope == 'conversation' and (pointer != row.base_summary_id or state.summary_revision != row.base_summary_revision):
                        raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_CHANGED', 'stale')
                    # 执行事实来自服务器，不交给摘要模型删改或解释成整体成功。
                    facts = (await session.execute(select(Source.message_id, Entry.execution_facts_json).join(Entry,
                        Entry.message_id == Source.message_id).where(Source.compression_id == row.id,
                        Entry.execution_facts_json.is_not(None)).order_by(Source.message_id))).all()
                    execution_facts = [
                        {'text': json.dumps(fact, ensure_ascii=False, separators=(',', ':')), 'sources': [mid]}
                        for mid, fact in facts if fact]
                    if execution_facts:
                        content_to_publish = {**content_to_publish, 'execution_facts': execution_facts}
                    text = private_text(content_to_publish, row.runtime_json.get('execution_facts', [])) if row.scope == 'execution' else plain_text(content_to_publish)
                    size = estimate_text_tokens(text, structural_tokens=8 if row.scope == 'execution' else 0)
                    row.output_tokens_estimate = size
                    wrapper = private_text({}, []) if row.scope == 'execution' else plain_text({})
                    if size > row.target_tokens + estimate_text_tokens(wrapper, structural_tokens=8 if row.scope == 'execution' else 0):
                        final, code = 'unchanged', 'CONTEXT_COMPRESSION_REQUIRED_FACTS_TOO_LARGE'
                    elif size >= row.input_tokens_estimate:
                        final, code = 'unchanged', 'CONTEXT_COMPRESSION_NO_GAIN'
                    elif row.scope == 'conversation':
                        await session.execute(update(ContextSummary).where(ContextSummary.active_conversation_id == row.conversation_id).values(active_conversation_id=None))
                        session.add(ContextSummary(id=row.id, conversation_id=row.conversation_id, active_conversation_id=row.conversation_id,
                            content_json=content_to_publish, text=text, text_bytes=size, created_at=now_utc()))
                        state.revision += 1; state.summary_revision += 1; state.updated_at = now_utc()
                        if row.trigger == 'automatic':
                            remaining = await session.scalar(select(func.sum(Entry.text_bytes + 8)).where(
                                Entry.conversation_id == row.conversation_id, Entry.state == 'included',
                                Entry.message_id <= row.runtime_json['boundary'],
                                ~select(Source.message_id).where(Source.compression_id == row.id, Source.message_id == Entry.message_id).exists())) or 0
                            total = size + int(remaining)
                            target = row.runtime_json['target_material_tokens']
                            row.runtime_json = {**row.runtime_json, 'outcome': {'target_tokens': target,
                                'material_tokens': total, 'target_reached': total <= target}}
                except CompactionFailure as exc:
                    final, code = exc.status, exc.code
                except (HTTPException, ContextBuildError):
                    final, code = 'stale', 'CONTEXT_COMPRESSION_SOURCE_CHANGED'
            row.status = final; row.error_code = code; row.phase = 'finished'; row.active_conversation_id = None; row.updated_at = now_utc()
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == row.execution_id))
            generation = await session.get(Generation, execution.generation_id)
            generation.status = 'completed' if final in {'completed', 'unchanged'} else 'stopped' if final == 'cancelled' else 'failed'
            generation.error_code = code; generation.ended_at = now_utc()
            if row.trigger == 'automatic':
                execution.status = generation.status; execution.error_code = code; execution.ended_at = generation.ended_at
            pending = await changed(session, row.conversation_id, row)
            await session.commit()
        await events.publish_events(pending)
    # runner 可能已因停止标志自行抛出 CancelledError，随后调度器又发送 task.cancel。
    # 终态发布由本调用拥有并等待完成，重复取消不能把任务永久留在 stopping。
    task = asyncio.create_task(with_locked_retry(operation))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError()


async def run(*, generation_id, conversation_id, execution_id, triggered_by_user_id, target_role_id, private_units=None):
    """手动排队与自动子任务共用的有预算分段/归并执行器。

    Args:
        private_units：宿主传入的执行内单元，仅留在内存；不发布到共享摘要。
    """
    job_id = None
    started_at = perf_counter()
    logger.info('generation.started', extra={'reason': 'context_compaction'})
    try:
        async with SessionLocal() as session:
            job = await session.scalar(select(ContextCompression).where(ContextCompression.execution_id == execution_id))
            if (job is None or job.conversation_id != conversation_id or job.owner_id != triggered_by_user_id
                or job.role_id != target_role_id):
                raise CompactionFailure('CONTEXT_COMPRESSION_NOT_FOUND')
            job_id = job.id
            await authorized(session, job)
            job.status = 'running'; job.phase = 'summarizing'; job.updated_at = now_utc()
            generation = await session.get(Generation, generation_id)
            generation.status = 'running'; generation.started_at = now_utc()
            await session.commit()
        outputs, chunk, index = [], [], 0
        if not fits(job, []):
            raise CompactionFailure('CONTEXT_COMPRESSION_BUDGET_EXCEEDED')
        async def input_units():
            if private_units is not None:
                for unit in private_units:
                    yield unit
            else:
                async for unit in units(job):
                    yield unit
        async for unit in input_units():
            if not fits(job, [unit]):
                raise CompactionFailure('CONTEXT_COMPRESSION_SOURCE_TOO_LARGE')
            if chunk and not fits(job, [*chunk, unit]):
                index += 1; outputs.append(await call_model(job, chunk, index)); chunk = []
            chunk.append(unit)
        if chunk:
            index += 1; outputs.append(await call_model(job, chunk, index))
        if not outputs:
            raise CompactionFailure('CONTEXT_NOTHING_TO_COMPRESS')
        while len(outputs) > 1:
            groups, chunk = [], []
            for unit in outputs:
                if chunk and not fits(job, [*chunk, unit]):
                    groups.append(chunk); chunk = []
                chunk.append(unit)
            if chunk:
                groups.append(chunk)
            if len(groups) >= len(outputs):
                # 连两段都无法合并时停止，不在“不限决策”配置下重复花费模型调用。
                raise CompactionFailure('CONTEXT_COMPRESSION_MERGE_TOO_LARGE')
            next_outputs = []
            for chunk in groups:
                if len(chunk) == 1:
                    next_outputs.append(chunk[0])
                else:
                    index += 1; next_outputs.append(await call_model(job, chunk, index))
            outputs = next_outputs
        await finish(job.id, 'completed', content=outputs[0]['summary'])
        if job.scope == 'execution':
            async with SessionLocal() as session:
                row = await session.get(ContextCompression, job.id)
                if row.status == 'completed':
                    return outputs[0]['summary']
    except asyncio.CancelledError:
        if job_id:
            await finish(job_id, 'interrupted', 'EXECUTION_INTERRUPTED')
        raise
    except CompactionFailure as exc:
        if job_id:
            await finish(job_id, exc.status, exc.code)
    except (HTTPException, ContextBuildError):
        if job_id:
            await finish(job_id, 'stale', 'CONTEXT_COMPRESSION_SOURCE_CHANGED')
    except Exception as exc:
        from ..agent.argument_errors import safe_exception_type
        logger.warning('generation.failed', extra={'reason': 'context_compaction', 'status': 'failed',
            'error_code': 'CONTEXT_COMPRESSION_FAILED', 'error_type': safe_exception_type(exc)})
        if job_id:
            await finish(job_id, 'failed', 'CONTEXT_COMPRESSION_FAILED')
    finally:
        await execution_usage.finish(generation_id)
        if job_id:
            async with SessionLocal() as session:
                row = await session.get(ContextCompression, job_id)
                if row:
                    logger.info('generation.completed' if row.status in {'completed', 'unchanged'} else 'generation.cancelled' if row.status in {'cancelled', 'interrupted'} else 'generation.failed',
                        extra={'reason': 'context_compaction', 'status': 'success' if row.status in {'completed', 'unchanged'} else 'cancelled' if row.status in {'cancelled', 'interrupted'} else 'rejected' if row.status == 'stale' else 'failed',
                            'error_code': row.error_code, 'duration_ms': round((perf_counter() - started_at) * 1000)})
