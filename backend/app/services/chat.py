from __future__ import annotations

from contextlib import aclosing

import asyncio
import json
import logging
from functools import wraps
import uuid
from contextvars import copy_context
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Conversation, ConversationMember, ExecutionWorkspace, Generation, Message, ModelConfig, Role, ToolCall, ToolExecutionDetail
from ..config.logging import set_log_context
from ..context import ContextBudgetExceeded, ContextBuildRequest, build_context
from ..context.domain import ContextBuildError, ContextBuildResult
from ..agent import providers
from ..agent.domain import ToolCallsNotDispatched, MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from ..agent.fake_provider import fake_reply_model
from ..agent.loop import run_agent
from ..agent.tools import guard_tools
from ..realtime import store as event_store
from ..workspaces.tools import create_workspace_tools, retain_execution_workspace
from .tool_details import update_detail
from ..workspaces.catalog import WORKSPACE_CAPTURE_TOOLS
from .execution_evidence import message_stop_reason, tool_evidence

logger = logging.getLogger("roleplex.chat")

# 流式内容先保存在内存，按节流间隔落库，避免逐片段写事务。
_PERSIST_INTERVAL_SECONDS = 1.0


def _context_log_fields(context: ContextBuildResult) -> dict[str, object]:
    """提取允许进入日志的 ContextBuilder 诊断字段。

    Args:
        context：本轮唯一 ContextBuilder 结果；不得从 Prompt 原文重新计算诊断值。

    Returns:
        只含版本、不可逆 hash、计数和本地预算元数据的安全字段。
    """
    estimate = context.budget.estimate
    fingerprints = context.fingerprints
    return {
        "context_schema_version": context.context_schema_version,
        "runtime_prefix_hash": fingerprints.runtime_prefix_hash,
        "role_prefix_hash": fingerprints.role_prefix_hash,
        "conversation_prefix_hash": fingerprints.conversation_prefix_hash,
        # Checkpoint 在 C3 前不存在；无值字段必须省略，不能用空串伪造一个版本。
        "tool_policy_hash": fingerprints.tool_policy_hash,
        "context_message_count": context.budget.included_message_count,
        "context_truncated_message_count": context.budget.truncated_message_count,
        "estimated_context_tokens": estimate.estimated_tokens,
        "input_budget_tokens": context.budget.input_budget_tokens,
        "estimator_kind": estimate.estimator_kind,
        "estimator_version": estimate.estimator_version,
        "estimator_is_provider_exact": estimate.is_provider_exact,
        "safety_margin_tokens": estimate.safety_margin_tokens,
    }


def message_payload(message: Message) -> dict:
    """把消息 ORM 记录转换为公开事件和 REST 响应共用的结构。"""
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "reply_to_id": message.reply_to_id,
        "mentions": message.mentions_json,
        "parts_json": message.parts_json,
        "status": message.status,
        "revision": message.revision,
        "chain_id": message.chain_id,
        "created_at": message.created_at.isoformat(),
        'timeline_version': (message.meta_json or {}).get('timeline_version', 0),
        'stop_reason': message_stop_reason(message),
    }


async def resolve_reply_role(session, conversation_id: int, role_id: int | None = None) -> Role | None:
    """返回该会话中可回复的角色；墓碑或停用角色不能触发生成。

    角色成员关系会为历史保留，不能仅凭成员表判断可用性；必须同时检查角色当前仍存活
    且启用。这个判断在服务端执行，前端的输入禁用只用于改善用户体验。

    Args:
        session：用于读取会话成员与角色状态的数据库会话。
        conversation_id：目标会话标识。
        role_id：群聊指定的目标角色；单聊为空时返回稳定成员顺序中的唯一角色。
    """
    conditions = [
        ConversationMember.conversation_id == conversation_id,
        Role.deleted_at.is_(None),
        Role.active.is_(True),
    ]
    if role_id is not None:
        conditions.append(Role.id == role_id)
    return await session.scalar(
        select(Role)
        .join(
            ConversationMember,
            (ConversationMember.member_id == Role.id) & (ConversationMember.member_type == "role"),
        )
        .where(*conditions)
        .order_by(ConversationMember.id.asc())
    )


async def build_agent_inputs(session, role: Role, prompt: str, *, allow_dangerous: bool):
    """按角色配置构造本次生成使用的模型与工具集合。

    Args:
        session：数据库会话，用于读取角色引用的厂商配置。
        role：负责回复的角色。
        prompt：用户当前消息文本，fake provider 用它拼出确定性回复。
        allow_dangerous：本次调用链是否允许执行 dangerous 工具。

    Returns:
        `(模型, 工具列表)`。正常运行默认使用真实 provider；自动化测试通过显式配置切换
        到确定性 fake。无论走哪条路径，都经过同一个 Agent 循环与防腐层。
    """
    if settings.agent_use_fake_provider:
        model = fake_reply_model(prompt)
    else:
        model_config = await session.get(ModelConfig, role.model_config_id)
        if model_config is None:
            raise ValueError("角色引用的模型配置不存在")
        model = providers.build_chat_model(role, model_config)
    # 当前里程碑没有已实现的内置工具；包装层保持在链路上，工具接入见后续里程碑。
    tools = guard_tools([], allow_dangerous=allow_dangerous)
    return model, tools


async def _provider_log_fields(session, role: Role) -> dict[str, str]:
    """解析本轮 Provider 类型和实际脱敏基址，不读取或返回 API Key。

    Args:
        session：读取角色模型配置的数据库会话。
        role：本轮执行角色。

    Returns:
        可以绑定到 Provider 调用链的安全日志字段。
    """
    if settings.agent_use_fake_provider:
        return {
            "provider_mode": "fake",
            "provider_type": "fake",
            "base_url": "fake://local",
            "base_url_source": "fake",
        }
    model_config = await session.get(ModelConfig, role.model_config_id)
    if model_config is None:
        raise ValueError("角色引用的模型配置不存在")
    return {
        "provider_mode": "real",
        "provider_type": model_config.provider_type,
        **providers.provider_base_url_metadata(model_config.provider_type, model_config.base_url),
    }


async def _record_tool_call(
    event: ToolCallFinished,
    *,
    args_summary: str,
    conversation_id: int,
    message_id: int | None,
    role_id: int | None,
    triggered_by_user_id: int | None,
    execution_id: str,
) -> None:
    """把一次工具调用写入审计表。

    审计要能回答"谁通过哪个角色调了什么工具、结果如何"；参数与输出只保存专用允许字段摘要，
    不保存凭据或完整敏感内容。

    Args:
        event：防腐层产出的工具结束事件。
        args_summary：对应开始事件记录的参数摘要。
        conversation_id：所属会话。
        message_id：本次生成的角色消息。
        role_id：执行工具的角色。
        triggered_by_user_id：触发本条链路的真人，权限与配额按它判定。
        execution_id：本次持久 execution 身份，用于关联 workspace lease。
    """
    async with SessionLocal() as session:
        lease = await session.scalar(select(ExecutionWorkspace).where(
            ExecutionWorkspace.execution_id == execution_id,
        ))
        session.add(
            ToolCall(
                conversation_id=conversation_id,
                message_id=message_id,
                role_id=role_id,
                triggered_by_user_id=triggered_by_user_id,
                execution_id=execution_id,
                workspace_binding_id=lease.workspace_binding_id if lease else None,
                tool_name=event.tool_name,
                args_summary=args_summary,
                status=event.status,
                duration_ms=event.duration_ms,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _with_text_part(parts: list[dict], text: str) -> list[dict]:
    """按已封闭段的长度更新尾部文本，旧消息沿用兼容表示。

    Args:
        parts：消息当前 parts。
        text：最新累计文本。

    Returns:
        已封闭文本和工具位置不变，只更新或追加末尾文本段。
    """
    parts = [part for part in parts if part.get('type') != 'execution_summary']
    if not any(part.get('type') == 'text' and part.get('part_id') for part in parts):
        return [{"type": "text", "text": text}, *[part for part in parts if part.get("type") != "text"]]
    updated = [dict(part) for part in parts]
    has_tail = bool(updated) and updated[-1].get('type') == 'text'
    sealed = updated[:-1] if has_tail else updated
    offset = sum(len(part.get('text', '')) for part in sealed if part.get('type') == 'text')
    if has_tail:
        updated[-1]['text'] = text[offset:]
    elif len(text) > offset:
        updated.append({'type': 'text', 'part_id': f'text-{len(updated)}', 'text': text[offset:]})
    return updated


def retry_message_write(operation):
    """复用数据库适配层的有限锁重试，取消原样传播。

    Args:
        operation：原消息所有者的短事务操作。
    """
    @wraps(operation)
    async def run(*args, **kwargs):
        """Args:
            args：原操作位置参数。
            kwargs：原操作关键字参数。
        """
        from ..db import with_locked_retry
        return await with_locked_retry(lambda: operation(*args, **kwargs))
    return run


@retry_message_write
async def _update_tool_part(
    *,
    conversation_id: int,
    message_id: int,
    generation_id: int,
    call_id: str,
    tool_name: str,
    status: str,
    duration_ms: int | None = None,
    command_summary: dict | None = None,
    accumulated_text: str | None = None,
    execution_id: str | None = None,
    triggered_by_user_id: int | None = None,
    private_input: dict | None = None,
    private_output: dict | None = None,
) -> None:
    """更新角色消息中的工具过程 part，并按事件先落库后广播。

    Args:
        conversation_id：工具调用所属会话。
        message_id：当前角色回复消息。
        generation_id：关联 generation。
        call_id：本轮内配对工具开始与结束的标识。
        tool_name：允许向会话成员展示的工具名称。
        status：`running/success/failed/rejected/cancelled`。
        duration_ms：结束时的耗时；开始事件为空。
        command_summary：防腐层已按允许字段提取的命令摘要，不包含路径或输出。
        accumulated_text：消息所有者的完整正文，用于在工具边界封闭文本段。
        execution_id：持久执行身份，供私有详情关联。
        triggered_by_user_id：原始触发者，详情仅为 Owner 保存。
        private_input：白名单工具的有界输入，不进入公开 part。
        private_output：白名单工具的有界输出，不进入公开 part。
    """
    async with SessionLocal() as session:
        message = await session.get(Message, message_id)
        if message is None or message.status != "generating":
            return
        parts = [dict(part) for part in (message.parts_json or []) if part.get("type") != "execution_summary"]
        if accumulated_text is not None:
            parts = _with_text_part(parts, accumulated_text)
        replacement = {
            "type": "tool_call",
            "call_id": call_id,
            "tool_name": tool_name,
            "status": status,
        }
        if duration_ms is not None:
            replacement["duration_ms"] = duration_ms
        index = next((
            index for index, part in enumerate(parts)
            if part.get("type") == "tool_call" and part.get("call_id") == call_id
        ), None)
        if index is None:
            parts.append(replacement)
        else:
            replacement = {**parts[index], **replacement}
            parts[index] = replacement
        replacement.update(command_summary or {})
        evidence = tool_evidence(tool_name, status, private_output)
        # 已确认提交不可被迟到的空采集或取消事件降格为未知。
        if replacement.get('effect_state') == 'applied' and evidence['effect_state'] == 'unknown':
            evidence = {key: replacement[key] for key in ('effect_state', 'confirmed_applied_items')}
        replacement.update(evidence)
        has_detail = await update_detail(session, message_id=message_id, call_id=call_id, tool_name=tool_name,
            status=status, execution_id=execution_id, user_id=triggered_by_user_id,
            private_input=private_input, private_output=private_output)
        if has_detail:
            replacement['detail_available'] = True
        message.parts_json = parts
        message.revision += 1
        pending = await event_store.append_event(
            session,
            conversation_id,
            "message_part_update",
            {"message": message_payload(message)},
            revision=message.revision,
            generation_id=generation_id,
        )
        await session.commit()
    await event_store.publish_events(pending)


@retry_message_write
async def _finalize(generation_id: int, status: str, text: str, error_code: str | None = None,
                    *, stop_reason: str | None = None) -> dict:
    """原所有者在同一短事务保存消息终态与停止原因，重复终态不重写。

    Args:
        generation_id：持久生成身份。
        status：已有生成终态，不新增状态值。
        text：已观察模型正文。
        error_code：已登记错误码。
        stop_reason：领域停止原因，不从正文推断。
    """
    async with SessionLocal() as session:
        generation = await session.get(Generation, generation_id)
        if generation is None or generation.status in {"completed", "stopped", "failed"}:
            return {}
        message = await session.get(Message, generation.assistant_message_id) if generation.assistant_message_id else None
        generation.status = status
        generation.error_code = error_code
        generation.ended_at = datetime.now(timezone.utc)
        events_to_publish = []
        if message is not None:
            message.parts_json = _with_text_part(message.parts_json or [], text)
            for part in message.parts_json:
                if part.get('type') == 'tool_call' and part.get('status') == 'running':
                    part.update(status='interrupted', error_code='EXECUTION_INTERRUPTED')
            from sqlalchemy import update
            await session.execute(update(ToolExecutionDetail).where(
                ToolExecutionDetail.message_id == message.id, ToolExecutionDetail.status == 'running',
            ).values(status='interrupted'))
            reason = stop_reason or {'completed': None, 'stopped': 'user_cancelled', 'failed': 'provider_failed'}[status]
            message.meta_json = {**(message.meta_json or {}), 'stop_reason': None if reason == 'completed' else reason}
            message.status = {"completed": "done", "stopped": "stopped", "failed": "error"}[status]
            message.revision += 1
            events_to_publish.append(
                await event_store.append_event(
                    session,
                    generation.conversation_id,
                    "message_done",
                    {"message": message_payload(message), "error_code": error_code},
                    revision=message.revision,
                    generation_id=generation.id,
                )
            )
        await session.commit()
    await event_store.publish_events(*events_to_publish)
    if not events_to_publish:
        return {}
    event = events_to_publish[-1]
    return {
        "stream_epoch": event.stream_epoch,
        "event_seq": event.event_seq,
        "message_revision": event.revision,
        "delta_seq": event.delta_seq,
    }


async def _finish_tool_event(
    event: ToolCallFinished, *, args_summary: str, conversation_id: int, message_id: int,
    generation_id: int, role_id: int, triggered_by_user_id: int | None, execution_id: str,
    accumulated_text: str | None = None,
) -> None:
    """由消息所有者顺序等待审计、过程卡与完成日志落地。

    Args:
        event：已观察到真实结果的领域事件。
        args_summary：开始时提取的允许字段。
        conversation_id：所属会话。
        message_id：本轮角色消息。
        generation_id：本轮生成。
        role_id：执行角色。
        triggered_by_user_id：原始触发者。
        execution_id：持久执行身份。
        accumulated_text：所有者已收到的正文，工具结束时同步封闭。
    """
    await _record_tool_call(event, args_summary=args_summary, conversation_id=conversation_id,
        message_id=message_id, role_id=role_id, triggered_by_user_id=triggered_by_user_id, execution_id=execution_id)
    status = {'ok': 'success', 'error': 'failed', 'rejected': 'rejected'}[event.status]
    await _update_tool_part(conversation_id=conversation_id, message_id=message_id, generation_id=generation_id,
        call_id=event.call_id, tool_name=event.tool_name, status=status, duration_ms=event.duration_ms,
        command_summary=event.command_summary, accumulated_text=accumulated_text,
        execution_id=execution_id, triggered_by_user_id=triggered_by_user_id, private_output=event.private_output)
    logger.info('tool.call_completed', extra={
        'conversation_id': conversation_id, 'generation_id': generation_id,
        'tool_name': event.tool_name, 'tool_call_id': event.call_id,
        'status': 'timeout' if event.command_summary.get('command_status') == 'timed_out' else status,
        'error_code': event.command_summary.get('error_code'), 'duration_ms': event.duration_ms,
    })


@retry_message_write
async def _persist_text_delta(
    message_id: int, conversation_id: int, generation_id: int, text: str, delta: str, delta_seq: int,
) -> None:
    """节流批次或工具边界统一落库并广播，不为每个 Provider 分片开事务。

    Args:
        message_id：角色消息。
        conversation_id：所属会话。
        generation_id：本轮生成。
        text：消息所有者完整正文快照。
        delta：自上一批次之后的增量。
        delta_seq：持久事件批次序号。
    """
    async with SessionLocal() as session:
        message = await session.get(Message, message_id)
        if message is None or message.status != 'generating':
            return
        parts = _with_text_part(message.parts_json or [], text)
        message.parts_json = parts
        message.revision += 1
        text_index = max(index for index, part in enumerate(parts) if part.get('type') == 'text')
        pending = await event_store.append_event(session, conversation_id, 'message_delta',
            {'message_id': message_id, 'text': delta, 'part_id': parts[text_index]['part_id'], 'part_index': text_index},
            revision=message.revision, delta_seq=delta_seq, generation_id=generation_id)
        await session.commit()
    await event_store.publish_events(pending)


async def run_scheduled_generation(
    generation_id: int,
    conversation_id: int,
    current_message_id: int,
    *,
    target_role_id: int,
    triggered_by_user_id: int | None = None,
    allow_dangerous: bool = False,
    execution_id: str,
    execution_kind: str = "single",
) -> None:
    """执行一次 Agent 生成，并按事件协议广播增量与终态。

    只消费 `app.agent.domain` 的领域事件：模型框架的私有事件形态被防腐层挡在外面，
    因此更换或升级框架不会影响本函数。

    Args:
        generation_id：生成记录标识，同时用于停止生成。
        conversation_id：所属会话。
        current_message_id：当前用户消息 ID；不接收旁路文本，避免当前消息与数据库历史漂移。
        target_role_id：本次 generation 唯一允许回复的角色 ID。
        triggered_by_user_id：触发者，写入工具审计。
        allow_dangerous：触发者是否可以执行 dangerous 工具。
        execution_id：本次角色执行标识；群聊各角色独立，chain ID 仍共享。
        execution_kind：ContextBuilder 运行类型，单聊为 single、群聊为 group_role。
    """
    set_log_context(
        user_id=triggered_by_user_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )
    task_started = perf_counter()
    accumulated = ""
    delta_seq = 0
    tool_args: dict[str, str] = {}
    command_calls: dict[str, tuple[ToolCallStarted, float]] = {}
    from ..agent.write_capture import WriteCaptureScope, write_capture_scope
    write_captures = WriteCaptureScope()
    write_capture_token = write_capture_scope.set(write_captures)
    async def finish_pending(pending_status: str) -> None:
        """回收后消费仍由原任务持有的凭据，不重放工具。

        Args:
            pending_status：取消或中断状态，与提交状态独立。
        """
        # 工具可能先于事件消费者完成；取消时也要消费尚未交付的实际提交凭据。
        for call_id in list(write_captures.receipts):
            if call_id not in command_calls and call_id in write_captures.started:
                command_calls[call_id]=write_captures.started[call_id]
        for call_id, (started_event, started_at) in list(command_calls.items()):
            duration_ms = int((perf_counter() - started_at) * 1000)
            await _record_tool_call(
                ToolCallFinished(call_id, started_event.tool_name, pending_status, duration_ms, '{}'),
                args_summary=started_event.args_summary, conversation_id=conversation_id,
                message_id=assistant_id, role_id=target_role_id, triggered_by_user_id=triggered_by_user_id,
                execution_id=execution_id,
            )
            await _update_tool_part(
                conversation_id=conversation_id, message_id=assistant_id, generation_id=generation_id,
                call_id=call_id, tool_name=started_event.tool_name, status=pending_status, duration_ms=duration_ms,
                command_summary=({'error_code': 'EXECUTION_INTERRUPTED'} if pending_status == 'interrupted' else
                    {'command_status': 'cancelled', 'exit_code': None} if started_event.tool_name in {'workspace_run_command', 'workspace_run_shell'} else {}),
                accumulated_text=accumulated,
                execution_id=execution_id, triggered_by_user_id=triggered_by_user_id,
                private_input=started_event.private_input,
                private_output=write_captures.take(call_id) if started_event.tool_name in WORKSPACE_CAPTURE_TOOLS else None,
            )
            logger.info('tool.call_completed', extra={
                'tool_call_id': call_id, 'tool_name': started_event.tool_name,
                'duration_ms': duration_ms, 'status': pending_status,
            })
        command_calls.clear()

    provider_call_count = 0
    first_ttft_ms: int | None = None
    usage_summary: dict[str, int | float | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cache_hit_tokens": None,
        "cache_write_tokens": None,
        "cache_hit_ratio": None,
    }
    context_fields: dict[str, object] = {}
    try:
        async with SessionLocal() as session:
            generation = await session.get(Generation, generation_id)
            if generation is None:
                logger.warning("generation.record_missing")
                return
            if generation.status != "queued":
                logger.info("generation.skipped", extra={"status": "cancelled", "reason": "not_queued"})
                return
            set_log_context(chain_id=generation.run_id, execution_id=execution_id)
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.deleted_at is not None:
                await _finalize(generation_id, "failed", "", error_code="CONVERSATION_NOT_FOUND")
                logger.warning(
                    "generation.conversation_unavailable",
                    extra={"error_code": "CONVERSATION_NOT_FOUND", "status": "rejected"},
                )
                return
            role = await resolve_reply_role(session, conversation_id, target_role_id)
            if role is None:
                await _finalize(generation_id, "failed", "", error_code="CONVERSATION_HAS_NO_ROLE")
                logger.warning(
                    "generation.role_unavailable",
                    extra={"error_code": "CONVERSATION_HAS_NO_ROLE", "status": "rejected"},
                )
                return
            set_log_context(role_id=role.id)
            assistant = Message(
                conversation_id=conversation_id,
                sender_type="role",
                sender_id=role.id,
                parts_json=[{"type": "text", "part_id": "text-0", "text": ""}],
                meta_json={'timeline_version': 1},
                status="generating",
                revision=0,
                chain_id=generation.run_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(assistant)
            await session.flush()
            generation.assistant_message_id = assistant.id
            generation.status = "running"
            generation.started_at = datetime.now(timezone.utc)
            created_event = await event_store.append_event(
                session,
                conversation_id,
                "message_created",
                {"message": message_payload(assistant)},
                generation_id=generation.id,
            )
            await session.commit()
            assistant_id = assistant.id
            set_log_context(message_id=assistant_id)
            role_id = role.id
        await event_store.publish_events(created_event)
        logger.info(
            "generation.started",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "message_id": assistant_id,
                "stream_epoch": created_event.stream_epoch,
                "event_seq": created_event.event_seq,
                "message_revision": created_event.revision,
                "startup_duration_ms": round((perf_counter() - task_started) * 1000, 2),
            },
        )

        # ContextBuilder 在用户消息与角色占位消息都已落库后读取，但以 current_message_id 为严格截止点，
        # 因此不会把当前消息重复放进 history，也不会读取正在生成的空 assistant 占位。
        async with SessionLocal() as session:
            context = await build_context(
                session,
                ContextBuildRequest(
                    role_id=role_id,
                    conversation_id=conversation_id,
                    current_message_id=current_message_id,
                    triggered_by_user_id=triggered_by_user_id,
                    execution_kind=execution_kind,
                ),
            )
            context_fields = _context_log_fields(context)
            set_log_context(**context_fields)
            logger.info("context.loaded", extra=context_fields)
            role = await session.get(Role, role_id)
            if role is None:
                raise ValueError("ROLE_NOT_AVAILABLE")
            from .agent_budget import limit_for
            decision_limit = await limit_for(session, generation.run_id, conversation_id)
            provider_fields = await _provider_log_fields(session, role)
            set_log_context(**provider_fields)
            model, tools = await build_agent_inputs(
                session, role, context.current_message, allow_dangerous=allow_dangerous,
            )
            tools.extend(await create_workspace_tools(
                session,
                execution_id=execution_id,
                conversation_id=conversation_id,
                role=role,
                triggered_by_user_id=triggered_by_user_id,
                allow_dangerous=allow_dangerous,
            ))
        if settings.agent_use_fake_provider:
            logger.info(
                "provider.built",
                extra={**provider_fields, "model": role.model_name},
            )

        last_persist = asyncio.get_running_loop().time() - _PERSIST_INTERVAL_SECONDS
        pending_text = ''
        failed_code: str | None = None
        failure_details: dict = {}
        stop_reason = 'completed'
        from .agent_budget import consume
        async def authorize_decision(index: int) -> bool:
            """Args:
                index：本次 execution 的模型决策序号。
            """
            return await consume(execution_id, index)

        # 消费者在事件处理期间取消，也必须先关闭图与工具任务，不能依赖垃圾回收。
        async with aclosing(run_agent(
            model=model,
            tools=tools,
            prompt=context.current_message,
            system_prompt=context.system_prompt,
            history=context.history,
            decision_limit=decision_limit, before_decision=authorize_decision,
        )) as agent_events:
            async for event in agent_events:
                usage_cancelled=False
                if isinstance(event, ProviderCallCompleted):
                    from .execution_usage import record as record_usage
                    # 先于其他持久化 await 交接已返回用量；取消不能让已知统计变成缺失。
                    recording=asyncio.create_task(record_usage(execution_id,event,provider_fields.get('provider_mode','unknown'),role.model_name,completed=True),context=copy_context())
                    while not recording.done():
                        try:await asyncio.shield(recording)
                        except asyncio.CancelledError:usage_cancelled=True
                    recording.result()
                if not isinstance(event, TextDelta) and pending_text and not usage_cancelled:
                    delta_seq += 1
                    await _persist_text_delta(assistant_id, conversation_id, generation_id, accumulated, pending_text, delta_seq)
                    pending_text = ''
                    last_persist = asyncio.get_running_loop().time()
                if isinstance(event, TextDelta):
                    accumulated += event.text
                    pending_text += event.text
                    now = asyncio.get_running_loop().time()
                    if now - last_persist >= _PERSIST_INTERVAL_SECONDS:
                        delta_seq += 1
                        await _persist_text_delta(assistant_id, conversation_id, generation_id, accumulated, pending_text, delta_seq)
                        pending_text = ''
                        last_persist = now
                elif isinstance(event, ToolCallStarted):
                    tool_args[event.call_id] = event.args_summary
                    command_calls[event.call_id] = (event, perf_counter())
                    command_summary = {}
                    if event.tool_name == 'workspace_run_command':
                        command_summary = json.loads(event.args_summary)
                    await _update_tool_part(
                        conversation_id=conversation_id,
                        message_id=assistant_id,
                        generation_id=generation_id,
                        call_id=event.call_id,
                        tool_name=event.tool_name,
                        status="running",
                        command_summary=command_summary,
                        accumulated_text=accumulated, execution_id=execution_id,
                        triggered_by_user_id=triggered_by_user_id, private_input=event.private_input,
                    )
                    logger.info(
                        "tool.call_started",
                        extra={
                            "conversation_id": conversation_id, "generation_id": generation_id,
                            "tool_name": event.tool_name, "tool_call_id": event.call_id,
                        },
                    )
                elif isinstance(event, ToolCallsNotDispatched):
                    async def record_undispatched():
                        """由原消息所有者完整记录同响应提议，不调用工具或创建审批。"""
                        for proposal in event.calls:
                            await _record_tool_call(
                                ToolCallFinished(proposal.call_id, proposal.tool_name, 'not_executed', 0, '{}'),
                                args_summary=proposal.args_summary, conversation_id=conversation_id, message_id=assistant_id,
                                role_id=role_id, triggered_by_user_id=triggered_by_user_id, execution_id=execution_id)
                            await _update_tool_part(conversation_id=conversation_id, message_id=assistant_id, generation_id=generation_id,
                                call_id=proposal.call_id, tool_name=proposal.tool_name, status='not_executed', accumulated_text=accumulated,
                                execution_id=execution_id, triggered_by_user_id=triggered_by_user_id,
                                command_summary={'not_executed_reason': event.reason, **({'error_code':proposal.argument_error['error_code']} if proposal.argument_error else {})}, private_input=proposal.private_input,
                                private_output={'format': 'not-dispatched-v1', 'reason': event.reason, **({'argument_error':proposal.argument_error} if proposal.argument_error else {})})
                            logger.info('tool.call_not_dispatched', extra={'tool_call_id': proposal.call_id,
                                'tool_name': proposal.tool_name, 'status': 'not_executed', 'reason': event.reason,
                                **({'error_code':proposal.argument_error['error_code']} if proposal.argument_error else {})})
                    recording = asyncio.create_task(record_undispatched(), context=copy_context())
                    cancelled = False
                    while not recording.done():
                        try:
                            await asyncio.shield(recording)
                        except asyncio.CancelledError:
                            cancelled = True
                    recording.result()
                    if cancelled:
                        raise asyncio.CancelledError
                elif isinstance(event, ToolCallFinished):
                    completion = _finish_tool_event(
                        event, args_summary=tool_args.pop(event.call_id, ''), conversation_id=conversation_id,
                        message_id=assistant_id, generation_id=generation_id, role_id=role_id,
                        triggered_by_user_id=triggered_by_user_id, execution_id=execution_id,
                        accumulated_text=accumulated,
                    )
                    if event.call_id in command_calls:
                        # 已观察到结果后，取消不能把审计提交与消息更新切断或补造第二条取消事实。
                        completed_task = asyncio.create_task(completion, context=copy_context())
                        cancelled = False
                        while not completed_task.done():
                            try:
                                await asyncio.shield(completed_task)
                            except asyncio.CancelledError:
                                cancelled = True
                        completed_task.result()
                        command_calls.pop(event.call_id, None)
                        write_captures.take(event.call_id)
                        if cancelled:
                            raise asyncio.CancelledError
                    else:
                        await completion
                elif isinstance(event, ProviderCallCompleted):
                    provider_call_count += 1
                    if first_ttft_ms is None:
                        first_ttft_ms = event.ttft_ms
                    logger.info(
                        "provider.call_completed",
                        extra={
                            "provider_call_index": event.call_index,
                            **provider_fields,
                            "model": role.model_name,
                            "ttft_ms": event.ttft_ms,
                            "duration_ms": event.duration_ms,
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "total_tokens": event.total_tokens,
                            "cache_hit_tokens": event.cache_hit_tokens,
                            "cache_write_tokens": event.cache_write_tokens,
                            "cache_hit_ratio": event.cache_hit_ratio,
                            "usage_source": "provider" if any(
                                value is not None for value in (
                                    event.input_tokens, event.output_tokens,
                                    event.total_tokens, event.cache_hit_tokens, event.cache_write_tokens,
                                )
                            ) else None,
                            "total_tokens_derived": event.total_tokens_derived,
                            "status": "success",
                            **context_fields,
                        },
                    )
                    if usage_cancelled:raise asyncio.CancelledError
                elif isinstance(event, ProviderCallStarted):
                    from .execution_usage import record as record_usage
                    await record_usage(execution_id,event,provider_fields.get('provider_mode','unknown'),role.model_name)
                    logger.info(
                        "provider.call_started",
                        extra={
                            "provider_call_index": event.call_index,
                            **provider_fields,
                            "model": role.model_name,
                            **context_fields,
                        },
                    )
                elif isinstance(event, MessageDone):
                    usage_summary.update(event.usage)
                    stop_reason = event.stop_reason
                elif isinstance(event, ProviderError):
                    failed_code = event.code
                    failure_details = {key:value for key,value in {'error_type':event.error_type,'error_phase':event.error_phase}.items() if value is not None}
                    stop_reason = event.stop_reason

        # 已派发但未收到结束事件：先消费现有凭据，不把未知副作用变成未执行。
        if command_calls:
            await finish_pending('interrupted')
        if failed_code:
            terminal = await _finalize(generation_id, "failed", accumulated, error_code=failed_code,
                                       stop_reason=stop_reason)
            logger.warning(
                "generation.failed",
                extra={
                    "conversation_id": conversation_id,
                    "generation_id": generation_id,
                    "error_code": failed_code,
                    **failure_details,
                    "status": "timeout" if failed_code == "PROVIDER_TIMEOUT" else "failed",
                    "provider_call_count": provider_call_count,
                    "ttft_ms": first_ttft_ms,
                    **usage_summary,
                    "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                    **context_fields,
                    **terminal,
                },
            )
            return

        budget_stopped = stop_reason in {'graph_budget', 'decision_budget'}
        terminal = await _finalize(generation_id, "stopped" if budget_stopped else "completed", accumulated,
                                   stop_reason=stop_reason)
        logger.info(
            "generation.budget_stopped" if budget_stopped else "generation.completed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "delta_count": delta_seq,
                "status": "cancelled" if budget_stopped else "success",
                "reason": stop_reason,
                "provider_call_count": provider_call_count,
                "ttft_ms": first_ttft_ms,
                **usage_summary,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
    except ContextBudgetExceeded as exc:
        terminal = await _finalize(generation_id, "failed", "", error_code=exc.error_code, stop_reason="context_rejected")
        logger.warning(
            "generation.failed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "error_code": exc.error_code,
                "status": "rejected",
                "provider_call_count": 0,
                "estimated_context_tokens": exc.estimated_tokens,
                "safety_margin_tokens": exc.safety_margin_tokens,
                "input_budget_tokens": exc.input_budget_tokens,
                "estimator_kind": exc.estimator_kind,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
        return
    except ContextBuildError as exc:
        reported = str(exc)
        expected_codes = {"CONVERSATION_NOT_FOUND", "ROLE_NOT_AVAILABLE", "TEXT_PART_REQUIRED"}
        error_code = reported if reported in expected_codes else "REQUEST_FAILED"
        terminal = await _finalize(generation_id, "failed", "", error_code=error_code, stop_reason="context_rejected")
        log_method = logger.warning if error_code in expected_codes else logger.exception
        log_method(
            "generation.failed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "error_code": error_code,
                "status": "rejected" if error_code in expected_codes else "failed",
                "provider_call_count": 0,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
        return
    except asyncio.CancelledError:
        # 用户主动停止属于预期结果，按 stopped 落库而不是未处理异常。
        await finish_pending('cancelled')
        terminal = await _finalize(generation_id, "stopped", accumulated)
        logger.info(
            "generation.cancelled",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "delta_count": delta_seq,
                "status": "cancelled",
                "reason": "user_stop",
                "provider_call_count": provider_call_count,
                "ttft_ms": first_ttft_ms,
                **usage_summary,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
        raise
    except Exception as exc:
        from ..agent.argument_errors import safe_exception_type
        await finish_pending('interrupted')
        terminal = await _finalize(generation_id, "failed", accumulated, error_code="AGENT_RUNTIME_ERROR", stop_reason='protocol_error')
        logger.exception(
            "generation.failed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "error_code": "AGENT_RUNTIME_ERROR",
                "error_type": safe_exception_type(exc), "error_phase": "runtime",
                "status": "failed",
                "provider_call_count": provider_call_count,
                "ttft_ms": first_ttft_ms,
                **usage_summary,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
    finally:
        write_captures.clear()
        write_capture_scope.reset(write_capture_token)
        await retain_execution_workspace(execution_id)
        from .execution_usage import finish as finish_usage
        await finish_usage(generation_id)


async def build_snapshot(session, conversation_id: int) -> dict:
    """构建断线恢复使用的完整会话快照。

    先读事件序号再读消息：SQLite 下多条 SELECT 不共享同一个读事务，两次读取之间可能
    有生成任务提交新事件。按这个顺序，游标只会比消息更旧，客户端最多重复收到已应用过的
    事件（协议要求按 `event_seq` 幂等去重）；反过来则会漏事件。
    """
    conversation = await session.get(Conversation, conversation_id)
    event_seq = conversation.event_seq if conversation else 0
    messages = (await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    )).all()
    active_ids = list((await session.scalars(
        select(Generation.id)
        .where(Generation.conversation_id == conversation_id, Generation.status.in_(["queued", "running"]))
        .order_by(Generation.id.asc())
    )).all())
    return {
        "conversation_id": conversation_id,
        "event_seq": event_seq,
        "messages": [message_payload(message) for message in messages],
        "active_generation_id": active_ids[0] if active_ids else None,
        "active_generation_ids": active_ids,
    }


def new_run_id() -> str:
    """生成一次 Agent 执行的链路标识。"""
    return uuid.uuid4().hex
