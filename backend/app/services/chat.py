from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Conversation, ConversationMember, Generation, Message, ModelConfig, Role, ToolCall
from ..config.logging import set_log_context
from ..context import ContextBudgetExceeded, ContextBuildRequest, build_context
from ..context.domain import ContextBuildError, ContextBuildResult
from ..agent import providers
from ..agent.domain import MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from ..agent.fake_provider import fake_reply_model
from ..agent.loop import run_agent
from ..agent.tools import guard_tools
from ..realtime import store as event_store

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
) -> None:
    """把一次工具调用写入审计表。

    审计要能回答"谁通过哪个角色调了什么工具、结果如何"；参数与输出只保存截断摘要，
    不保存凭据或完整敏感内容。

    Args:
        event：防腐层产出的工具结束事件。
        args_summary：对应开始事件记录的参数摘要。
        conversation_id：所属会话。
        message_id：本次生成的角色消息。
        role_id：执行工具的角色。
        triggered_by_user_id：触发本条链路的真人，权限与配额按它判定。
    """
    async with SessionLocal() as session:
        session.add(
            ToolCall(
                conversation_id=conversation_id,
                message_id=message_id,
                role_id=role_id,
                triggered_by_user_id=triggered_by_user_id,
                tool_name=event.tool_name,
                args_summary=args_summary,
                status=event.status,
                duration_ms=event.duration_ms,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _with_text_part(parts: list[dict], text: str) -> list[dict]:
    """替换文本 part，同时保留同一消息中的工具过程卡片。

    Args:
        parts：消息当前 parts。
        text：最新累计文本。

    Returns:
        文本 part 位于首位、其他 part 保持原顺序的新数组。
    """
    return [{"type": "text", "text": text}, *[part for part in parts if part.get("type") != "text"]]


async def _update_tool_part(
    *,
    conversation_id: int,
    message_id: int,
    generation_id: int,
    call_id: str,
    tool_name: str,
    status: str,
    duration_ms: int | None = None,
) -> None:
    """更新角色消息中的工具过程 part，并按事件先落库后广播。

    Args:
        conversation_id：工具调用所属会话。
        message_id：当前角色回复消息。
        generation_id：关联 generation。
        call_id：本轮内配对工具开始与结束的标识。
        tool_name：允许向会话成员展示的工具名称。
        status：`running/success/failed/rejected`。
        duration_ms：结束时的耗时；开始事件为空。
    """
    async with SessionLocal() as session:
        message = await session.get(Message, message_id)
        if message is None:
            return
        parts = [dict(part) for part in (message.parts_json or [])]
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
            parts[index] = replacement
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


async def _finalize(generation_id: int, status: str, text: str, error_code: str | None = None) -> dict:
    """把生成终态写入数据库并广播，返回终态事件的可观测字段。"""
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
                parts_json=[{"type": "text", "text": ""}],
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
            provider_fields = await _provider_log_fields(session, role)
            set_log_context(**provider_fields)
            model, tools = await build_agent_inputs(
                session, role, context.current_message, allow_dangerous=allow_dangerous,
            )
        if settings.agent_use_fake_provider:
            logger.info(
                "provider.built",
                extra={**provider_fields, "model": role.model_name},
            )

        last_persist = asyncio.get_running_loop().time()
        failed_code: str | None = None
        async for event in run_agent(
            model=model,
            tools=tools,
            prompt=context.current_message,
            system_prompt=context.system_prompt,
            history=context.history,
        ):
            if isinstance(event, TextDelta):
                accumulated += event.text
                delta_seq += 1
                now = asyncio.get_running_loop().time()
                should_persist = now - last_persist >= _PERSIST_INTERVAL_SECONDS
                async with SessionLocal() as session:
                    message = await session.get(Message, assistant_id)
                    if message is None:
                        return
                    if should_persist:
                        message.parts_json = _with_text_part(message.parts_json or [], accumulated)
                        last_persist = now
                    message.revision += 1
                    delta_event = await event_store.append_event(
                        session,
                        conversation_id,
                        "message_delta",
                        {"message_id": assistant_id, "text": event.text},
                        revision=message.revision,
                        delta_seq=delta_seq,
                        generation_id=generation_id,
                    )
                    await session.commit()
                await event_store.publish_events(delta_event)
            elif isinstance(event, ToolCallStarted):
                tool_args[event.call_id] = event.args_summary
                await _update_tool_part(
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    generation_id=generation_id,
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    status="running",
                )
                logger.info(
                    "tool.call_started",
                    extra={
                        "conversation_id": conversation_id, "generation_id": generation_id,
                        "tool_name": event.tool_name, "tool_call_id": event.call_id,
                    },
                )
            elif isinstance(event, ToolCallFinished):
                await _record_tool_call(
                    event, args_summary=tool_args.pop(event.call_id, ""),
                    conversation_id=conversation_id, message_id=assistant_id,
                    role_id=role_id, triggered_by_user_id=triggered_by_user_id,
                )
                await _update_tool_part(
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    generation_id=generation_id,
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    status={"ok": "success", "error": "failed", "rejected": "rejected"}[event.status],
                    duration_ms=event.duration_ms,
                )
                logger.info(
                    "tool.call_completed",
                    extra={
                        "conversation_id": conversation_id, "generation_id": generation_id,
                        "tool_name": event.tool_name, "tool_call_id": event.call_id,
                        "status": {"ok": "success", "error": "failed", "rejected": "rejected"}[event.status],
                        "duration_ms": event.duration_ms,
                    },
                )
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
            elif isinstance(event, ProviderCallStarted):
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
            elif isinstance(event, ProviderError):
                failed_code = event.code

        if failed_code:
            terminal = await _finalize(generation_id, "failed", accumulated, error_code=failed_code)
            logger.warning(
                "generation.failed",
                extra={
                    "conversation_id": conversation_id,
                    "generation_id": generation_id,
                    "error_code": failed_code,
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

        terminal = await _finalize(generation_id, "completed", accumulated)
        logger.info(
            "generation.completed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "delta_count": delta_seq,
                "status": "success",
                "provider_call_count": provider_call_count,
                "ttft_ms": first_ttft_ms,
                **usage_summary,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )
    except ContextBudgetExceeded as exc:
        terminal = await _finalize(generation_id, "failed", "", error_code=exc.error_code)
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
        terminal = await _finalize(generation_id, "failed", "", error_code=error_code)
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
    except Exception:
        terminal = await _finalize(generation_id, "failed", accumulated, error_code="PROVIDER_ERROR")
        logger.exception(
            "generation.failed",
            extra={
                "conversation_id": conversation_id,
                "generation_id": generation_id,
                "error_code": "PROVIDER_ERROR",
                "status": "failed",
                "provider_call_count": provider_call_count,
                "ttft_ms": first_ttft_ms,
                **usage_summary,
                "duration_ms": round((perf_counter() - task_started) * 1000, 2),
                **context_fields,
                **terminal,
            },
        )


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
