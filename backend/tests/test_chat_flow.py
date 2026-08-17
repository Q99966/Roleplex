from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import OWNER_NICKNAME, OWNER_PASSWORD, OWNER_USERNAME


async def _bootstrap(client: AsyncClient) -> dict:
    """注册 Owner、创建模型配置、角色和单聊会话，返回测试所需上下文。"""
    register = await client.post("/api/auth/register", json={
        "username": OWNER_USERNAME, "password": OWNER_PASSWORD, "nickname": OWNER_NICKNAME,
    })
    assert register.status_code == 201, register.text
    token = register.json()["access_token"]
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
    """发送消息后 fake 生成应完成，并留下可恢复的事件序列。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.services import event_store

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
                "username": "guest_chat", "password": "password123", "nickname": "Guest",
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
