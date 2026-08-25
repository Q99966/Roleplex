from __future__ import annotations

import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import TEST_PASSWORD, ensure_owner_async, guest_username


async def _bootstrap(client: AsyncClient) -> dict:
    """确保 Owner 存在，并创建模型配置、角色和单聊会话，返回测试所需上下文。"""
    token = (await ensure_owner_async(client))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    config = await client.post("/api/model-configs", headers=headers, json={
        "name": "fake", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text

    role = await client.post("/api/roles", headers=headers, json={
        "name": "助手", "system_prompt": "你是测试助手", "model_config_id": config.json()["id"],
        "model_name": "fake-model",
    })
    assert role.status_code == 201, role.text

    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": "单聊测试", "role_ids": [role.json()["id"]],
    })
    assert conversation.status_code == 201, conversation.text
    return {"headers": headers, "conversation_id": conversation.json()["id"], "role_id": role.json()["id"]}


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

    completed = next(record for record in records if record.getMessage() == "generation.completed")
    assert completed.provider_call_count == 1
    assert completed.total_tokens is None
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
