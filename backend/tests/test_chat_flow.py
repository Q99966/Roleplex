from __future__ import annotations

import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from accounts import TEST_PASSWORD, ensure_owner_async, guest_username


async def _bootstrap(
    client: AsyncClient,
    suffix: str = "",
    *,
    context_window_tokens: int = 200_000,
    system_prompt: str = "你是测试助手",
) -> dict:
    """确保 Owner 存在，并创建模型配置、角色和单聊会话，返回测试所需上下文。"""
    token = (await ensure_owner_async(client))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    config = await client.post("/api/model-configs", headers=headers, json={
        "name": f"fake{suffix}", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text

    role = await client.post("/api/roles", headers=headers, json={
        "name": f"助手{suffix}", "system_prompt": system_prompt, "model_config_id": config.json()["id"],
        "model_name": "fake-model", "context_window_tokens": context_window_tokens,
    })
    assert role.status_code == 201, role.text
    assert role.json()["context_window_tokens"] == context_window_tokens
    assert role.json()["context_window_ceiling_tokens"] == 2_000_000
    assert role.json()["effective_context_window_tokens"] == context_window_tokens

    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": f"单聊测试{suffix}", "role_ids": [role.json()["id"]],
    })
    assert conversation.status_code == 201, conversation.text
    return {"headers": headers, "conversation_id": conversation.json()["id"], "role_id": role.json()["id"]}


async def _wait_for_role_status(
    client: AsyncClient,
    headers: dict[str, str],
    conversation_id: int,
    status: str,
) -> list[dict]:
    """轮询直到会话出现指定角色消息终态。

    Args:
        client：测试 HTTP 客户端。
        headers：Owner 认证头。
        conversation_id：目标会话。
        status：等待的角色消息状态。
    """
    for _ in range(100):
        await asyncio.sleep(0.05)
        history = await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
        items = history.json()["items"]
        if any(message["sender_type"] == "role" and message["status"] == status for message in items):
            return items
    pytest.fail(f"角色消息没有在预期时间内进入 {status} 状态")


@pytest.mark.anyio
async def test_single_chat_streams_and_persists():
    """发送消息后 fake 生成应完成，并留下事件序列及明确为空的用量日志。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.realtime import store as event_store

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    chat_logger = logging.getLogger("roleplex.chat")
    chat_logger.addHandler(handler)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ctx = await _bootstrap(client)
            conversation_id = ctx["conversation_id"]

            send = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=ctx["headers"],
                json={"parts": [{"type": "text", "text": "你好"}], "client_message_id": "client-1"},
            )
            assert send.status_code == 202, send.text
            assert send.json()["generation_id"] is not None

            # 重复提交同一 client_message_id 必须幂等，不创建第二条用户消息。
            duplicate = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=ctx["headers"],
                json={"parts": [{"type": "text", "text": "你好"}], "client_message_id": "client-1"},
            )
            assert duplicate.json()["duplicate"] is True

            for _ in range(100):
                await asyncio.sleep(0.1)
                history = await client.get(f"/api/conversations/{conversation_id}/messages", headers=ctx["headers"])
                items = history.json()["items"]
                if any(m["sender_type"] == "role" and m["status"] == "done" for m in items):
                    break
            else:
                pytest.fail("fake 生成没有在预期时间内完成")

            assistant = [m for m in items if m["sender_type"] == "role"][0]
            assert assistant["parts_json"][0]["text"].startswith("已收到你的消息：你好")
            assert assistant["sender_id"] == ctx["role_id"]

            async with SessionLocal() as session:
                backlog = await event_store.read_backlog(session, conversation_id, 0)
            types = [event.type for event in backlog]
            seqs = [event.event_seq for event in backlog]
            assert types[0] == "message_created"
            assert "message_delta" in types
            assert types[-1] == "message_done"
            assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))

            deltas = [event for event in backlog if event.type == "message_delta"]
            assert [event.delta_seq for event in deltas] == list(range(1, len(deltas) + 1))

    chat_logger.removeHandler(handler)
    provider_call = next(record for record in records if record.getMessage() == "provider.call_completed")
    assert provider_call.provider_call_index == 1
    assert provider_call.ttft_ms >= 0
    assert provider_call.duration_ms >= provider_call.ttft_ms
    assert provider_call.input_tokens is None
    assert provider_call.output_tokens is None
    assert provider_call.total_tokens is None
    assert provider_call.cache_hit_tokens is None
    assert provider_call.cache_write_tokens is None
    assert provider_call.cache_hit_ratio is None
    assert provider_call.base_url == "fake://local"
    assert provider_call.base_url_source == "fake"

    loaded = next(record for record in records if record.getMessage() == "context.loaded")
    assert loaded.context_schema_version == 1
    assert len(loaded.runtime_prefix_hash) == 64
    assert len(loaded.role_prefix_hash) == 64
    assert len(loaded.conversation_prefix_hash) == 64
    assert len(loaded.tool_policy_hash) == 64
    assert loaded.context_message_count == 0
    assert loaded.context_truncated_message_count == 0
    assert loaded.estimated_context_tokens > 0
    assert loaded.estimator_kind == "conservative_utf8_v1"
    assert not hasattr(loaded, "checkpoint_hash")

    for field in (
        "context_schema_version", "runtime_prefix_hash", "role_prefix_hash",
        "conversation_prefix_hash", "tool_policy_hash", "context_message_count",
        "context_truncated_message_count", "estimated_context_tokens",
    ):
        assert getattr(provider_call, field) == getattr(loaded, field)
    provider_started = next(record for record in records if record.getMessage() == "provider.call_started")
    assert provider_started.tool_policy_hash == loaded.tool_policy_hash
    assert not hasattr(provider_started, "checkpoint_hash")
    assert not hasattr(loaded, "system_prompt")
    assert not hasattr(loaded, "current_message")

    completed = next(record for record in records if record.getMessage() == "generation.completed")
    assert completed.provider_call_count == 1
    assert completed.total_tokens is None
    assert completed.runtime_prefix_hash == loaded.runtime_prefix_hash
    await engine.dispose()


@pytest.mark.anyio
async def test_unauthorized_and_foreign_conversation_are_hidden():
    """未认证请求返回 401，非成员访问会话按不存在处理。"""
    from app.main import app
    from app.db import engine

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            anonymous = await client.get("/api/conversations/1/messages")
            assert anonymous.status_code == 401
            assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"

            guest = await client.post("/api/auth/register", json={
                "username": guest_username("chat"), "password": TEST_PASSWORD, "nickname": "Guest",
            })
            assert guest.status_code == 201
            assert guest.json()["user"]["is_owner"] is False
            guest_headers = {"Authorization": f"Bearer {guest.json()['access_token']}"}

            hidden = await client.get("/api/conversations/1/messages", headers=guest_headers)
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

            denied = await client.get("/api/model-configs", headers=guest_headers)
            assert denied.status_code == 403

    await engine.dispose()


@pytest.mark.anyio
async def test_second_turn_receives_first_terminal_turn_as_history(monkeypatch: pytest.MonkeyPatch):
    """第二轮必须看到第一轮用户消息和角色终态，且当前消息不能重复进入 history。"""
    from app.agent.domain import MessageDone, TextDelta
    from app.main import app
    from app.db import engine
    from app.services import chat

    observed_histories: list[list] = []
    observed_system_prompts: list[str] = []
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        """收集两轮生成的上下文诊断事件。"""

        def emit(self, record: logging.LogRecord) -> None:
            """保存一条 chat logger 记录。

            Args:
                record：生成链路写出的日志记录。
            """
            records.append(record)

    async def history_aware_agent(**kwargs):
        """记录业务层传入的 history，并产出确定性终态。

        Args:
            **kwargs：生成服务传给 Agent 防腐层的本轮输入。
        """
        history = list(kwargs["history"])
        observed_histories.append(history)
        observed_system_prompts.append(kwargs["system_prompt"])
        reply = f"已处理：{kwargs['prompt']}"
        yield TextDelta(text=reply)
        yield MessageDone(text=reply)

    monkeypatch.setattr(chat, "run_agent", history_aware_agent)
    handler = Capture()
    chat.logger.addHandler(handler)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                ctx = await _bootstrap(client, "历史")
                first = await client.post(
                    f"/api/conversations/{ctx['conversation_id']}/messages",
                    headers=ctx["headers"],
                    json={"parts": [{"type": "text", "text": "第一轮问题"}], "client_message_id": "history-1"},
                )
                assert first.status_code == 202, first.text
                await _wait_for_role_status(client, ctx["headers"], ctx["conversation_id"], "done")

                second = await client.post(
                    f"/api/conversations/{ctx['conversation_id']}/messages",
                    headers=ctx["headers"],
                    json={"parts": [{"type": "text", "text": "第二轮问题"}], "client_message_id": "history-2"},
                )
                assert second.status_code == 202, second.text
                await _wait_for_role_status(client, ctx["headers"], ctx["conversation_id"], "done")
    finally:
        chat.logger.removeHandler(handler)

    assert observed_histories[0] == []
    assert observed_system_prompts[0] == observed_system_prompts[1]
    second_history = observed_histories[1]
    assert any(isinstance(message, HumanMessage) and "第一轮问题" in str(message.content) for message in second_history)
    assert any(isinstance(message, AIMessage) and "已处理：第一轮问题" in str(message.content) for message in second_history)
    assert all("第二轮问题" not in str(message.content) for message in second_history)
    loaded = [record for record in records if record.getMessage() == "context.loaded"]
    assert len(loaded) == 2
    for field in (
        "context_schema_version", "runtime_prefix_hash", "role_prefix_hash",
        "conversation_prefix_hash", "tool_policy_hash",
    ):
        assert getattr(loaded[0], field) == getattr(loaded[1], field)
    assert [record.context_message_count for record in loaded] == [0, 2]
    await engine.dispose()


@pytest.mark.anyio
async def test_untrimmable_context_budget_fails_before_provider(monkeypatch: pytest.MonkeyPatch):
    """必要上下文超过角色窗口时必须拒绝，不能截断当前消息或调用 Provider。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import Generation
    from app.services import chat

    provider_called = False
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        """收集预算终态记录，不依赖 pytest root logger handler。"""

        def emit(self, record: logging.LogRecord) -> None:
            """保存一条 chat logger 记录。

            Args:
                record：生成链路写出的日志记录。
            """
            records.append(record)

    async def forbidden_agent(**_kwargs):
        """标记任何越过预算防线的 Agent 调用。

        Args:
            **_kwargs：本用例不应收到的 Agent 输入。
        """
        nonlocal provider_called
        provider_called = True
        if False:
            yield None

    monkeypatch.setattr(chat, "run_agent", forbidden_agent)
    handler = Capture()
    chat.logger.addHandler(handler)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                ctx = await _bootstrap(
                    client,
                    "预算",
                    context_window_tokens=4096,
                    system_prompt="必须遵守的角色约束" * 1200,
                )
                sent = await client.post(
                    f"/api/conversations/{ctx['conversation_id']}/messages",
                    headers=ctx["headers"],
                    json={"parts": [{"type": "text", "text": "不能被静默截断的当前消息"}]},
                )
                assert sent.status_code == 202, sent.text
                await _wait_for_role_status(client, ctx["headers"], ctx["conversation_id"], "error")
                async with SessionLocal() as session:
                    generation = await session.get(Generation, sent.json()["generation_id"])
                    assert generation is not None
                    assert generation.error_code == "CONTEXT_BUDGET_EXCEEDED"
    finally:
        chat.logger.removeHandler(handler)

    assert provider_called is False
    failed = next(
        record
        for record in records
        if record.name == "roleplex.chat"
        and record.getMessage() == "generation.failed"
        and getattr(record, "error_code", None) == "CONTEXT_BUDGET_EXCEEDED"
    )
    assert failed.estimated_context_tokens + failed.safety_margin_tokens > failed.input_budget_tokens
    assert failed.estimator_kind == "conservative_utf8_v1"
    await engine.dispose()
