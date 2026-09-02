"""M4a 群聊成员、mentions 串行调度、停止整链和会话隔离测试。"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import BaseMessage
from sqlalchemy import select

from accounts import ensure_owner_async


async def _create_group(
    client: AsyncClient,
    *,
    role_order: list[int] | None = None,
) -> dict[str, Any]:
    """创建两个独立角色和一个群聊，返回认证头及资源标识。

    Args:
        client：当前测试使用的 ASGI HTTP 客户端。
        role_order：可选的成员顺序，用已有角色 ID 覆盖默认 A、B 顺序。
    """
    owner = await ensure_owner_async(client)
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    suffix = uuid.uuid4().hex[:8]
    config = await client.post("/api/model-configs", headers=headers, json={
        "name": f"group-cfg-{suffix}",
        "provider_type": "openai_compatible",
        "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text
    role_ids: list[int] = []
    for marker in ("A", "B"):
        role = await client.post("/api/roles", headers=headers, json={
            "name": f"群聊角色{marker}-{suffix}",
            "system_prompt": f"GROUP_ROLE_{marker}_{suffix}",
            "model_config_id": config.json()["id"],
            "model_name": "fake-model",
        })
        assert role.status_code == 201, role.text
        role_ids.append(role.json()["id"])
    members = role_order or role_ids
    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "group",
        "title": f"群聊-{suffix}",
        "role_ids": members,
    })
    assert conversation.status_code == 201, conversation.text
    return {
        "headers": headers,
        "owner_id": owner["user"]["id"],
        "conversation_id": conversation.json()["id"],
        "revision": conversation.json()["revision"],
        "role_ids": role_ids,
        "suffix": suffix,
    }


async def _wait_for_role_messages(
    client: AsyncClient,
    headers: dict[str, str],
    conversation_id: int,
    count: int,
    *,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    """等待指定数量的角色消息进入目标状态。

    Args:
        client：当前测试使用的 ASGI HTTP 客户端。
        headers：Owner 认证头。
        conversation_id：目标群聊 ID。
        count：期望角色消息数量。
        statuses：允许的状态集合；默认只接受 done。
    """
    accepted = statuses or {"done"}
    for _ in range(200):
        history = await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
        role_messages = [
            message for message in history.json()["items"]
            if message["sender_type"] == "role" and message["status"] in accepted
        ]
        if len(role_messages) >= count:
            return role_messages
        await asyncio.sleep(0.025)
    raise AssertionError("群聊角色回复没有在预期时间内进入终态")


async def _wait_for_queue_jobs(
    conversation_id: int,
    count: int,
    statuses: set[str],
) -> list[Any]:
    """等待持久队列任务全部进入允许终态。

    Args:
        conversation_id：目标会话 ID。
        count：预期任务数量。
        statuses：允许的任务状态集合。
    """
    from app.db import SessionLocal
    from app.models import QueueJob

    for _ in range(200):
        async with SessionLocal() as session:
            jobs = (await session.scalars(select(QueueJob).where(
                QueueJob.conversation_id == conversation_id,
            ).order_by(QueueJob.id))).all()
        if len(jobs) == count and all(job.status in statuses for job in jobs):
            return list(jobs)
        await asyncio.sleep(0.01)
    raise AssertionError("队列任务没有在预期时间内进入终态")


@pytest.mark.anyio
async def test_group_without_mentions_persists_message_without_generation():
    """群聊无 mentions 时只保存真人消息，不创建 generation 或调用 Provider。"""
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import AgentExecution, Generation, Message

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            sent = await client.post(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
                json={"parts": [{"type": "text", "text": "只记录，不触发"}], "mentions": []},
            )
            assert sent.status_code == 202, sent.text
            assert sent.json()["generation_id"] is None
            assert sent.json()["generation_ids"] == []
            await asyncio.sleep(0.1)
            async with SessionLocal() as session:
                generations = (await session.scalars(select(Generation).where(
                    Generation.conversation_id == group["conversation_id"],
                ))).all()
                executions = (await session.scalars(select(AgentExecution).where(
                    AgentExecution.conversation_id == group["conversation_id"],
                ))).all()
                messages = (await session.scalars(select(Message).where(
                    Message.conversation_id == group["conversation_id"],
                ))).all()
    assert generations == []
    assert executions == []
    assert [(message.sender_type, message.mentions_json) for message in messages] == [("user", [])]
    await engine.dispose()


@pytest.mark.anyio
async def test_group_rejects_unavailable_mention_before_message_is_persisted():
    """越权、非成员或不可用角色 mentions 必须在写入真人消息前拒绝。"""
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import Message

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            sent = await client.post(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
                json={"parts": [{"type": "text", "text": "@未知角色"}], "mentions": [999999]},
            )
            assert sent.status_code == 422, sent.text
            assert sent.json()["error"]["code"] == "ROLE_NOT_AVAILABLE"
            async with SessionLocal() as session:
                messages = (await session.scalars(select(Message).where(
                    Message.conversation_id == group["conversation_id"],
                ))).all()
    assert messages == []
    await engine.dispose()


@pytest.mark.anyio
async def test_group_requires_two_members_and_all_respects_chain_limit():
    """群聊至少两名角色，all 展开超过 20 个角色时必须在消息写入前拒绝。"""
    from app.db import SessionLocal, engine, now_utc
    from app.main import app
    from app.models import ConversationMember, Message, Role

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            one_member = await client.post("/api/conversations", headers=group["headers"], json={
                "type": "group",
                "title": f"人数不足-{group['suffix']}",
                "role_ids": [group["role_ids"][0]],
            })
            assert one_member.status_code == 422, one_member.text
            assert one_member.json()["error"]["code"] == "GROUP_CHAT_REQUIRES_MULTIPLE_ROLES"
            duplicate_member = await client.post("/api/conversations", headers=group["headers"], json={
                "type": "group",
                "title": f"重复成员-{group['suffix']}",
                "role_ids": [group["role_ids"][0], group["role_ids"][0]],
            })
            assert duplicate_member.status_code == 422, duplicate_member.text
            assert duplicate_member.json()["error"]["code"] == "ROLE_NOT_AVAILABLE"

            async with SessionLocal() as session:
                template = await session.get(Role, group["role_ids"][0])
                assert template is not None
                for index in range(19):
                    role = Role(
                        created_by=template.created_by,
                        name=f"链上限角色{index}-{group['suffix']}",
                        description=None,
                        tags_json=[],
                        system_prompt="链上限测试",
                        model_config_id=template.model_config_id,
                        model_name=template.model_name,
                        context_window_tokens=template.context_window_tokens,
                        params_json={},
                        skills_json=[],
                        builtin_tools_json=[],
                        mcp_servers_json=[],
                        mcp_tools_cache_json=[],
                        active=True,
                        created_at=now_utc(),
                        updated_at=now_utc(),
                    )
                    session.add(role)
                    await session.flush()
                    session.add(ConversationMember(
                        conversation_id=group["conversation_id"],
                        member_type="role",
                        member_id=role.id,
                        joined_at=now_utc(),
                    ))
                await session.commit()

            sent = await client.post(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
                json={"parts": [{"type": "text", "text": "全部"}], "mentions": ["all"]},
            )
            assert sent.status_code == 422, sent.text
            assert sent.json()["error"]["code"] == "CHAIN_LIMIT_EXCEEDED"
            async with SessionLocal() as session:
                messages = (await session.scalars(select(Message).where(
                    Message.conversation_id == group["conversation_id"],
                ))).all()
    assert messages == []
    await engine.dispose()


@pytest.mark.anyio
async def test_mentions_run_strictly_in_request_order_and_later_role_sees_prior_reply(
    monkeypatch: pytest.MonkeyPatch,
):
    """@A @B 必须严格串行，B 构建上下文时已经包含 A 的终态回复。"""
    from app.agent.domain import MessageDone, TextDelta
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import AgentExecution, Generation, Message
    from app.services import chat

    timeline: list[str] = []
    histories: dict[str, list[BaseMessage]] = {}

    async def scripted_agent(**kwargs: Any):
        """按 system marker 记录执行顺序和历史，再返回稳定文本。

        Args:
            **kwargs：生成服务传入的 ContextBuilder 结果。
        """
        marker = "A" if "GROUP_ROLE_A_" in kwargs["system_prompt"] else "B"
        timeline.append(f"start-{marker}")
        histories[marker] = list(kwargs["history"])
        reply = f"角色{marker}已完成"
        yield TextDelta(text=reply)
        yield MessageDone(text=reply)
        timeline.append(f"end-{marker}")

    monkeypatch.setattr(chat, "run_agent", scripted_agent)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            role_a, role_b = group["role_ids"]
            sent = await client.post(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
                json={
                    "parts": [{"type": "text", "text": "请依次处理"}],
                    "mentions": [role_a, role_b, role_a],
                },
            )
            assert sent.status_code == 202, sent.text
            assert len(sent.json()["generation_ids"]) == 2
            role_messages = await _wait_for_role_messages(
                client, group["headers"], group["conversation_id"], 2,
            )
            jobs = await _wait_for_queue_jobs(group["conversation_id"], 2, {"completed"})
            async with SessionLocal() as session:
                generations = (await session.scalars(select(Generation).where(
                    Generation.conversation_id == group["conversation_id"],
                ).order_by(Generation.id))).all()
                executions = (await session.scalars(select(AgentExecution).where(
                    AgentExecution.conversation_id == group["conversation_id"],
                ).order_by(AgentExecution.generation_id))).all()
                user_message = await session.scalar(select(Message).where(
                    Message.conversation_id == group["conversation_id"],
                    Message.sender_type == "user",
                ))

    assert timeline == ["start-A", "end-A", "start-B", "end-B"]
    assert [message["sender_id"] for message in role_messages] == [role_a, role_b]
    assert any("角色A已完成" in str(message.content) for message in histories["B"])
    assert len({generation.run_id for generation in generations}) == 1
    assert user_message is not None and user_message.chain_id == generations[0].run_id
    assert [job.status for job in jobs] == ["completed", "completed"]
    assert len({execution.execution_id for execution in executions}) == 2
    assert [execution.role_id for execution in executions] == [role_a, role_b]
    assert [execution.execution_kind for execution in executions] == ["group_role", "group_role"]
    assert [execution.status for execution in executions] == ["completed", "completed"]
    assert len({execution.chain_id for execution in executions}) == 1
    assert all(execution.attempt == 1 for execution in executions)
    assert all(set(job.payload_json) == {
        "current_message_id", "triggered_by_user_id", "allow_dangerous", "request_id",
    } for job in jobs)
    await engine.dispose()


@pytest.mark.anyio
async def test_scheduler_start_interrupts_stale_executions_without_replaying_provider():
    """服务重启只中断遗留 execution 并取消 job，不能重新调用 Provider。"""
    from app.db import SessionLocal, engine, now_utc
    from app.main import app
    from app.models import AgentExecution, Generation, QueueJob
    from app.realtime.events import current_epoch
    from app.scheduling.conversation import ConversationScheduler

    provider_calls = 0

    async def forbidden_runner(**_kwargs: Any) -> None:
        """记录任何不应发生的 Provider 重放。

        Args:
            **_kwargs：调度器传入的 generation 参数。
        """
        nonlocal provider_calls
        provider_calls += 1

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            async with SessionLocal() as session:
                generation = Generation(
                    conversation_id=group["conversation_id"],
                    stream_epoch=current_epoch(),
                    status="running",
                    run_id="restart-chain",
                    started_at=now_utc(),
                )
                session.add(generation)
                await session.flush()
                execution = AgentExecution(
                    execution_id="restart-execution",
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    chain_id="restart-chain",
                    role_id=group["role_ids"][0],
                    execution_kind="group_role",
                    attempt=1,
                    status="running",
                    created_at=now_utc(),
                    started_at=now_utc(),
                )
                job = QueueJob(
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    status="running",
                    payload_json={
                        "current_message_id": 1,
                        "triggered_by_user_id": 1,
                        "allow_dangerous": True,
                        "request_id": "restart-request",
                    },
                    attempts=1,
                    cancel_requested=False,
                    created_at=now_utc(),
                    started_at=now_utc(),
                )
                session.add_all([execution, job])
                orphan_generation = Generation(
                    conversation_id=group["conversation_id"],
                    stream_epoch=current_epoch(),
                    status="stopped",
                    run_id="orphan-execution-chain",
                    started_at=now_utc(),
                    ended_at=now_utc(),
                )
                session.add(orphan_generation)
                await session.flush()
                orphan_execution = AgentExecution(
                    execution_id="orphan-running-execution",
                    conversation_id=group["conversation_id"],
                    generation_id=orphan_generation.id,
                    chain_id="orphan-execution-chain",
                    role_id=group["role_ids"][1],
                    execution_kind="group_role",
                    attempt=1,
                    status="running",
                    created_at=now_utc(),
                    started_at=now_utc(),
                )
                session.add(orphan_execution)
                await session.commit()
                generation_id = generation.id
                job_id = job.id
                orphan_generation_id = orphan_generation.id

            scheduler = ConversationScheduler()
            await scheduler.start(forbidden_runner)
            await scheduler.shutdown()

            async with SessionLocal() as session:
                generation = await session.get(Generation, generation_id)
                execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == generation_id,
                ))
                orphan_execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == orphan_generation_id,
                ))
                job = await session.get(QueueJob, job_id)

    assert provider_calls == 0
    assert generation is not None and generation.status == "stopped"
    assert execution is not None and execution.status == "interrupted"
    assert execution.error_code == "EXECUTION_INTERRUPTED"
    assert execution.ended_at is not None
    assert orphan_execution is not None and orphan_execution.status == "interrupted"
    assert orphan_execution.error_code == "EXECUTION_INTERRUPTED"
    assert orphan_execution.ended_at is not None
    assert job is not None and job.status == "cancelled"
    assert job.cancel_requested is True
    await engine.dispose()


@pytest.mark.anyio
async def test_scheduler_refuses_generation_without_persisted_execution():
    """queue job 缺少 execution 权威行时必须失败，不能相信 payload 或调用 Provider。"""
    from app.db import SessionLocal, engine, now_utc
    from app.main import app
    from app.models import Generation, QueueJob
    from app.realtime.events import current_epoch
    from app.scheduling.conversation import ConversationScheduler

    provider_calls = 0

    async def forbidden_runner(**_kwargs: Any) -> None:
        """记录任何越过 execution 完整性检查的调用。

        Args:
            **_kwargs：本用例不应收到的 generation 参数。
        """
        nonlocal provider_calls
        provider_calls += 1

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            scheduler = ConversationScheduler()
            await scheduler.start(forbidden_runner)
            async with SessionLocal() as session:
                generation = Generation(
                    conversation_id=group["conversation_id"],
                    stream_epoch=current_epoch(),
                    status="queued",
                    run_id="missing-execution-chain",
                )
                session.add(generation)
                await session.flush()
                job = QueueJob(
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    status="queued",
                    payload_json={
                        "current_message_id": 1,
                        "triggered_by_user_id": 1,
                        "allow_dangerous": True,
                        "request_id": "missing-execution-request",
                    },
                    attempts=0,
                    cancel_requested=False,
                    created_at=now_utc(),
                )
                session.add(job)
                await session.commit()
                generation_id = generation.id
                job_id = job.id

            await scheduler.enqueue(group["conversation_id"], job_id)
            await _wait_for_queue_jobs(group["conversation_id"], 1, {"failed"})
            await scheduler.shutdown()
            async with SessionLocal() as session:
                generation = await session.get(Generation, generation_id)
                job = await session.get(QueueJob, job_id)

    assert provider_calls == 0
    assert generation is not None and generation.status == "failed"
    assert generation.error_code == "REQUEST_FAILED"
    assert job is not None and job.status == "failed"
    assert job.ended_at is not None
    await engine.dispose()


@pytest.mark.anyio
async def test_scheduler_fails_all_states_when_runner_returns_without_generation_terminal():
    """runner 未写 generation 终态时，job/generation/execution 必须一起失败。"""
    from app.db import SessionLocal, engine, now_utc
    from app.main import app
    from app.models import AgentExecution, Generation, QueueJob
    from app.realtime.events import current_epoch
    from app.scheduling.conversation import ConversationScheduler

    runner_calls = 0

    async def incomplete_runner(**_kwargs: Any) -> None:
        """模拟违反 generation reducer 契约、直接返回的 runner。

        Args:
            **_kwargs：调度器传入的 generation 参数。
        """
        nonlocal runner_calls
        runner_calls += 1

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            scheduler = ConversationScheduler()
            await scheduler.start(incomplete_runner)
            async with SessionLocal() as session:
                generation = Generation(
                    conversation_id=group["conversation_id"],
                    stream_epoch=current_epoch(),
                    status="queued",
                    run_id="incomplete-runner-chain",
                )
                session.add(generation)
                await session.flush()
                execution = AgentExecution(
                    execution_id="incomplete-runner-execution",
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    chain_id="incomplete-runner-chain",
                    role_id=group["role_ids"][0],
                    execution_kind="group_role",
                    attempt=1,
                    status="queued",
                    created_at=now_utc(),
                )
                job = QueueJob(
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    status="queued",
                    payload_json={
                        "current_message_id": 1,
                        "triggered_by_user_id": 1,
                        "allow_dangerous": True,
                        "request_id": "incomplete-runner-request",
                    },
                    attempts=0,
                    cancel_requested=False,
                    created_at=now_utc(),
                )
                session.add_all([execution, job])
                await session.commit()
                generation_id = generation.id
                job_id = job.id

            await scheduler.enqueue(group["conversation_id"], job_id)
            await _wait_for_queue_jobs(group["conversation_id"], 1, {"failed"})
            await scheduler.shutdown()
            async with SessionLocal() as session:
                generation = await session.get(Generation, generation_id)
                execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == generation_id,
                ))
                job = await session.get(QueueJob, job_id)

    assert runner_calls == 1
    assert generation is not None and generation.status == "failed"
    assert generation.error_code == "REQUEST_FAILED"
    assert generation.ended_at is not None
    assert execution is not None and execution.status == "failed"
    assert execution.error_code == "REQUEST_FAILED"
    assert execution.ended_at is not None
    assert job is not None and job.status == "failed"
    await engine.dispose()


@pytest.mark.anyio
async def test_scheduler_shutdown_reconciles_active_execution_after_runner_stops_generation():
    """服务关闭取消 runner 后，应立即把 active execution/job 与 stopped generation 对齐。"""
    from app.db import SessionLocal, engine, now_utc
    from app.main import app
    from app.models import AgentExecution, Generation, QueueJob
    from app.realtime.events import current_epoch
    from app.scheduling.conversation import ConversationScheduler

    runner_started = asyncio.Event()

    async def stopping_runner(**kwargs: Any) -> None:
        """模拟 chat reducer 在取消时先把 generation 写成 stopped。

        Args:
            **kwargs：包含待收口 generation ID 的调度参数。
        """
        runner_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            async with SessionLocal() as session:
                generation = await session.get(Generation, int(kwargs["generation_id"]))
                assert generation is not None
                generation.status = "stopped"
                generation.ended_at = now_utc()
                await session.commit()
            raise

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            scheduler = ConversationScheduler()
            await scheduler.start(stopping_runner)
            async with SessionLocal() as session:
                generation = Generation(
                    conversation_id=group["conversation_id"],
                    stream_epoch=current_epoch(),
                    status="queued",
                    run_id="shutdown-chain",
                )
                session.add(generation)
                await session.flush()
                execution = AgentExecution(
                    execution_id="shutdown-execution",
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    chain_id="shutdown-chain",
                    role_id=group["role_ids"][0],
                    execution_kind="group_role",
                    attempt=1,
                    status="queued",
                    created_at=now_utc(),
                )
                job = QueueJob(
                    conversation_id=group["conversation_id"],
                    generation_id=generation.id,
                    status="queued",
                    payload_json={
                        "current_message_id": 1,
                        "triggered_by_user_id": 1,
                        "allow_dangerous": True,
                        "request_id": "shutdown-request",
                    },
                    attempts=0,
                    cancel_requested=False,
                    created_at=now_utc(),
                )
                session.add_all([execution, job])
                await session.commit()
                generation_id = generation.id
                job_id = job.id

            await scheduler.enqueue(group["conversation_id"], job_id)
            await asyncio.wait_for(runner_started.wait(), timeout=2)
            await scheduler.shutdown()
            async with SessionLocal() as session:
                generation = await session.get(Generation, generation_id)
                execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == generation_id,
                ))
                job = await session.get(QueueJob, job_id)

    assert generation is not None and generation.status == "stopped"
    assert execution is not None and execution.status == "stopped"
    assert execution.error_code is None
    assert execution.ended_at is not None
    assert job is not None and job.status == "cancelled"
    assert job.cancel_requested is True
    assert job.ended_at is not None
    await engine.dispose()


@pytest.mark.anyio
async def test_all_uses_persisted_member_order_and_member_update_is_revision_guarded(
    monkeypatch: pytest.MonkeyPatch,
):
    """all 按持久成员顺序展开，成员调整使用 conversation revision 防覆盖。"""
    from app.agent.domain import MessageDone, TextDelta
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import EventLog
    from app.services import chat

    started_roles: list[int] = []

    async def ordered_agent(**kwargs: Any):
        """从 system marker 记录当前角色并立即结束。

        Args:
            **kwargs：生成服务传入的上下文和当前消息。
        """
        marker = 0 if "GROUP_ROLE_A_" in kwargs["system_prompt"] else 1
        started_roles.append(marker)
        yield TextDelta(text="完成")
        yield MessageDone(text="完成")

    monkeypatch.setattr(chat, "run_agent", ordered_agent)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            base = await _create_group(client)
            role_a, role_b = base["role_ids"]
            too_few = await client.put(
                f"/api/conversations/{base['conversation_id']}/members",
                headers=base["headers"],
                json={"role_ids": [role_a], "expected_revision": base["revision"]},
            )
            assert too_few.status_code == 422, too_few.text
            assert too_few.json()["error"]["code"] == "GROUP_CHAT_REQUIRES_MULTIPLE_ROLES"
            updated = await client.put(
                f"/api/conversations/{base['conversation_id']}/members",
                headers=base["headers"],
                json={"role_ids": [role_b, role_a], "expected_revision": base["revision"]},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["role_ids"] == [role_b, role_a]
            assert updated.json()["revision"] == base["revision"] + 1
            stale = await client.put(
                f"/api/conversations/{base['conversation_id']}/members",
                headers=base["headers"],
                json={"role_ids": [role_a, role_b], "expected_revision": base["revision"]},
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["error"]["code"] == "CONVERSATION_REVISION_CONFLICT"

            sent = await client.post(
                f"/api/conversations/{base['conversation_id']}/messages",
                headers=base["headers"],
                json={"parts": [{"type": "text", "text": "全部回复"}], "mentions": ["all", role_a]},
            )
            assert sent.status_code == 202, sent.text
            await _wait_for_role_messages(client, base["headers"], base["conversation_id"], 2)
            async with SessionLocal() as session:
                member_event = await session.scalar(select(EventLog).where(
                    EventLog.conversation_id == base["conversation_id"],
                    EventLog.event_type == "member_updated",
                ))
    assert started_roles == [1, 0]
    assert member_event is not None
    assert member_event.payload_json == {
        "role_ids": [role_b, role_a],
        "revision": base["revision"] + 1,
    }
    await engine.dispose()


@pytest.mark.anyio
async def test_different_conversations_execute_in_parallel(monkeypatch: pytest.MonkeyPatch):
    """不同会话各有独立 worker，一个会话的慢角色不能阻塞另一个会话。"""
    from app.agent.domain import MessageDone, TextDelta
    from app.db import engine
    from app.main import app
    from app.services import chat

    both_started = asyncio.Event()
    release = asyncio.Event()
    started_prompts: set[str] = set()

    async def barrier_agent(**kwargs: Any):
        """等待两个会话同时进入 Provider seam，再统一释放。

        Args:
            **kwargs：包含当前消息 prompt 的生成输入。
        """
        started_prompts.add(str(kwargs["prompt"]))
        if len(started_prompts) == 2:
            both_started.set()
        await release.wait()
        yield TextDelta(text="完成")
        yield MessageDone(text="完成")

    monkeypatch.setattr(chat, "run_agent", barrier_agent)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await _create_group(client)
            second = await _create_group(client)
            for group, prompt in ((first, "会话一"), (second, "会话二")):
                sent = await client.post(
                    f"/api/conversations/{group['conversation_id']}/messages",
                    headers=group["headers"],
                    json={"parts": [{"type": "text", "text": prompt}], "mentions": [group["role_ids"][0]]},
                )
                assert sent.status_code == 202, sent.text
            await asyncio.wait_for(both_started.wait(), timeout=2)
            release.set()
            await asyncio.gather(
                _wait_for_role_messages(client, first["headers"], first["conversation_id"], 1),
                _wait_for_role_messages(client, second["headers"], second["conversation_id"], 1),
            )
    assert started_prompts == {"会话一", "会话二"}
    await engine.dispose()


@pytest.mark.anyio
async def test_stop_cancels_current_generation_and_all_later_jobs(monkeypatch: pytest.MonkeyPatch):
    """停止群聊 chain 应停止当前角色并清空后续队列，不产生第二个空占位消息。"""
    from app.agent.domain import TextDelta
    from app.db import SessionLocal, engine
    from app.main import app
    from app.models import AgentExecution, Generation, Message, QueueJob
    from app.services import chat

    first_started = asyncio.Event()

    async def blocking_agent(**_kwargs: Any):
        """产出首个分片后阻塞，直到停止请求取消当前任务。

        Args:
            **_kwargs：本用例不关心具体 Agent 输入。
        """
        first_started.set()
        yield TextDelta(text="部分内容")
        await asyncio.Event().wait()

    monkeypatch.setattr(chat, "run_agent", blocking_agent)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            group = await _create_group(client)
            sent = await client.post(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
                json={
                    "parts": [{"type": "text", "text": "开始后停止"}],
                    "mentions": group["role_ids"],
                },
            )
            await asyncio.wait_for(first_started.wait(), timeout=2)
            snapshot = await client.get(
                f"/api/conversations/{group['conversation_id']}/messages",
                headers=group["headers"],
            )
            assert snapshot.json()["active_generation_ids"] == sent.json()["generation_ids"]
            stopped = await client.post(
                f"/api/conversations/{group['conversation_id']}/stop",
                headers=group["headers"],
            )
            assert stopped.status_code == 202, stopped.text
            assert stopped.json()["stopped"] is True
            assert stopped.json()["generation_ids"] == sent.json()["generation_ids"]
            role_messages = await _wait_for_role_messages(
                client, group["headers"], group["conversation_id"], 1,
                statuses={"stopped"},
            )
            await asyncio.sleep(0.1)
            async with SessionLocal() as session:
                generations = (await session.scalars(select(Generation).where(
                    Generation.conversation_id == group["conversation_id"],
                ).order_by(Generation.id))).all()
                jobs = (await session.scalars(select(QueueJob).where(
                    QueueJob.conversation_id == group["conversation_id"],
                ).order_by(QueueJob.id))).all()
                executions = (await session.scalars(select(AgentExecution).where(
                    AgentExecution.conversation_id == group["conversation_id"],
                ).order_by(AgentExecution.generation_id))).all()
                assistant_messages = (await session.scalars(select(Message).where(
                    Message.conversation_id == group["conversation_id"],
                    Message.sender_type == "role",
                ))).all()
    assert role_messages[0]["status"] == "stopped"
    assert [generation.status for generation in generations] == ["stopped", "stopped"]
    assert [job.status for job in jobs] == ["cancelled", "cancelled"]
    assert [execution.status for execution in executions] == ["stopped", "stopped"]
    assert all(execution.error_code is None for execution in executions)
    assert all(execution.ended_at is not None for execution in executions)
    assert len(assistant_messages) == 1
    await engine.dispose()
