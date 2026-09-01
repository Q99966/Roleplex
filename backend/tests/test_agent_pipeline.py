"""消息生成链路的工具权限、审计与取消测试。

通过真实的 HTTP 接口触发生成，只把模型与工具替换为脚本化实现，因此验证的是产品链路
本身：工具危险级别在执行层生效、每次调用都留下审计记录、停止生成按 stopped 收尾。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.tools import tool
from sqlalchemy import select

from accounts import TEST_PASSWORD, ensure_owner_async, guest_username


@tool
def read_artifact(artifact_id: int) -> str:
    """读取产物内容，属于 safe 白名单工具。"""
    return f"artifact-{artifact_id}-内容"


@tool
def wipe_disk(path: str) -> str:
    """危险示例工具：只要被真正执行就说明拦截失效。"""
    return "已清空"


async def _owner_headers(client: AsyncClient) -> tuple[dict[str, str], int]:
    """返回 Owner 的认证头与用户 ID。"""
    body = await ensure_owner_async(client)
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def _single_chat(client: AsyncClient, headers: dict[str, str], title: str) -> int:
    """创建带角色成员的单聊会话，返回会话 ID。"""
    config = await client.post("/api/model-configs", headers=headers, json={
        "name": f"cfg-{title}", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text
    role = await client.post("/api/roles", headers=headers, json={
        "name": f"角色-{title}", "system_prompt": "你是测试助手",
        "model_config_id": config.json()["id"], "model_name": "fake-model",
    })
    assert role.status_code == 201, role.text
    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": title, "role_ids": [role.json()["id"]],
    })
    assert conversation.status_code == 201, conversation.text
    return conversation.json()["id"]


def _scripted_inputs(tool_name: str, tool_obj, *, delay: float = 0.0):
    """构造替换 build_agent_inputs 的脚本化实现：先调一次工具，再产出文本。"""
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.agent.tools import guard_tools

    async def build(session, role, prompt, *, allow_dangerous: bool):
        model = ScriptedChatModel(
            turns=[
                ScriptedTurn(tool_calls=[{"name": tool_name, "args": {"artifact_id": 1} if tool_name == "read_artifact" else {"path": "C:/"}, "id": "call_1"}]),
                ScriptedTurn(text="本轮已结束"),
            ],
            delay=delay,
        )
        return model, guard_tools([tool_obj], allow_dangerous=allow_dangerous)

    return build


async def _wait_for_done(client: AsyncClient, headers: dict[str, str], conversation_id: int) -> list[dict]:
    """轮询等待角色回复进入终态，返回消息列表。"""
    for _ in range(100):
        await asyncio.sleep(0.05)
        history = await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
        items = history.json()["items"]
        if any(m["sender_type"] == "role" and m["status"] in {"done", "error", "stopped"} for m in items):
            return items
    raise AssertionError("生成没有在预期时间内进入终态")


@pytest.mark.anyio
async def test_owner_tool_call_is_audited(monkeypatch):
    """Owner 触发的 safe 工具应正常执行并留下审计记录。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import EventLog, Message, ToolCall
    from app.services import chat

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers, owner_id = await _owner_headers(client)
            conversation_id = await _single_chat(client, headers, "审计")
            monkeypatch.setattr(chat, "build_agent_inputs", _scripted_inputs("read_artifact", read_artifact))

            send = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers, json={"parts": [{"type": "text", "text": "读一下产物"}]},
            )
            assert send.status_code == 202, send.text
            await _wait_for_done(client, headers, conversation_id)

            async with SessionLocal() as session:
                calls = (await session.scalars(
                    select(ToolCall).where(ToolCall.conversation_id == conversation_id)
                )).all()
                assistant = await session.scalar(select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.sender_type == "role",
                ))
                part_events = (await session.scalars(select(EventLog).where(
                    EventLog.conversation_id == conversation_id,
                    EventLog.event_type == "message_part_update",
                ).order_by(EventLog.event_seq))).all()

    assert len(calls) == 1
    assert calls[0].tool_name == "read_artifact"
    assert calls[0].status == "ok"
    assert calls[0].triggered_by_user_id == owner_id
    assert calls[0].role_id is not None and calls[0].message_id is not None
    assert "artifact_id" in calls[0].args_summary
    assert assistant is not None
    tool_part = next(part for part in assistant.parts_json if part["type"] == "tool_call")
    assert tool_part["tool_name"] == "read_artifact"
    assert tool_part["status"] == "success"
    assert len(part_events) == 2
    await engine.dispose()


@pytest.mark.anyio
async def test_guest_dangerous_tool_is_rejected_at_execution_layer(monkeypatch):
    """Guest 触发的 dangerous 工具必须被执行层拒绝，本轮仍要正常收尾。"""
    from app.main import app
    from app.db import SessionLocal, engine, now_utc
    from app.models import ConversationMember, ToolCall
    from app.services import chat

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers, _ = await _owner_headers(client)
            conversation_id = await _single_chat(client, headers, "拦截")

            guest = await client.post("/api/auth/register", json={
                "username": guest_username("tools"), "password": TEST_PASSWORD, "nickname": "Guest",
            })
            assert guest.status_code == 201, guest.text
            assert guest.json()["user"]["is_owner"] is False
            guest_id = guest.json()["user"]["id"]
            guest_headers = {"Authorization": f"Bearer {guest.json()['access_token']}"}

            # 邀请流程属于后续里程碑，这里直接写入成员关系以构造"Guest 已在会话内"的场景。
            async with SessionLocal() as session:
                session.add(ConversationMember(
                    conversation_id=conversation_id, member_type="user", member_id=guest_id,
                    pinned=False, archived=False, joined_at=now_utc(),
                ))
                await session.commit()

            monkeypatch.setattr(chat, "build_agent_inputs", _scripted_inputs("wipe_disk", wipe_disk))
            send = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=guest_headers, json={"parts": [{"type": "text", "text": "清空磁盘"}]},
            )
            assert send.status_code == 202, send.text
            items = await _wait_for_done(client, guest_headers, conversation_id)

            async with SessionLocal() as session:
                calls = (await session.scalars(
                    select(ToolCall).where(ToolCall.conversation_id == conversation_id)
                )).all()

    assert [call.status for call in calls] == ["rejected"]
    assert calls[0].triggered_by_user_id == guest_id
    assistant = [m for m in items if m["sender_type"] == "role"][0]
    assert assistant["status"] == "done", "被拒绝的工具不应让整轮生成失败"
    await engine.dispose()


@pytest.mark.anyio
async def test_owner_dangerous_tool_is_allowed(monkeypatch):
    """Owner 是本机可信主体，dangerous 工具对其放行。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import ToolCall
    from app.services import chat

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers, _ = await _owner_headers(client)
            conversation_id = await _single_chat(client, headers, "放行")
            monkeypatch.setattr(chat, "build_agent_inputs", _scripted_inputs("wipe_disk", wipe_disk))

            send = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers, json={"parts": [{"type": "text", "text": "执行维护脚本"}]},
            )
            assert send.status_code == 202, send.text
            await _wait_for_done(client, headers, conversation_id)

            async with SessionLocal() as session:
                calls = (await session.scalars(
                    select(ToolCall).where(ToolCall.conversation_id == conversation_id)
                )).all()

    assert [call.status for call in calls] == ["ok"]
    await engine.dispose()


@pytest.mark.anyio
async def test_stop_cancels_generation_and_persists_stopped(monkeypatch):
    """停止生成应取消任务并按 stopped 落库，而不是当作错误。"""
    from app.main import app
    from app.db import SessionLocal, engine
    from app.models import Generation
    from app.services import chat

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers, _ = await _owner_headers(client)
            conversation_id = await _single_chat(client, headers, "停止")
            # 分片间隔放大，保证停止请求落在生成中途。
            monkeypatch.setattr(chat, "build_agent_inputs", _scripted_inputs("read_artifact", read_artifact, delay=0.3))

            send = await client.post(
                f"/api/conversations/{conversation_id}/messages",
                headers=headers, json={"parts": [{"type": "text", "text": "写一段长文本"}]},
            )
            assert send.status_code == 202, send.text
            await asyncio.sleep(0.4)

            stop = await client.post(f"/api/conversations/{conversation_id}/stop", headers=headers)
            assert stop.status_code == 202, stop.text
            assert stop.json()["stopped"] is True
            items = await _wait_for_done(client, headers, conversation_id)

            async with SessionLocal() as session:
                generation = await session.get(Generation, stop.json()["generation_id"])
                status = generation.status
                stop_requested_at = generation.stop_requested_at
                ended_at = generation.ended_at

    assistant = [m for m in items if m["sender_type"] == "role"][0]
    assert assistant["status"] == "stopped"
    assert status == "stopped"
    # 取消传播耗时的观测口径：请求停止到落库终态之间的间隔。
    assert stop_requested_at is not None and ended_at is not None
    await engine.dispose()
