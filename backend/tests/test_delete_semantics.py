"""删除语义集成测试：角色墓碑与会话回收站。

覆盖三条要求：
1. 角色删除后保留身份、清空配置，历史消息仍能查到"谁说的"，且同名角色可以立刻重建；
2. 会话删除进回收站后从列表消失、消息链路按不存在处理，保留期内可完整恢复；
3. 过期清理只删除超过保留期的会话，未到期的不受影响。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from accounts import ensure_owner_async


async def _bootstrap(client: AsyncClient, tag: str) -> dict:
    """建立 Owner、模型配置、角色和单聊会话，返回后续断言需要的上下文。

    Args:
        client：已进入 lifespan 的异步测试客户端。
        tag：用例维度的短标识，避免同一轮数据库中角色名与会话名冲突。
    """
    token = (await ensure_owner_async(client))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    config = await client.post("/api/model-configs", headers=headers, json={
        "name": f"cfg-{tag}", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text

    role = await client.post("/api/roles", headers=headers, json={
        "name": f"助手-{tag}", "system_prompt": "你是测试助手",
        "model_config_id": config.json()["id"], "model_name": "fake-model",
        "description": "会被墓碑清除", "tags": ["测试"],
    })
    assert role.status_code == 201, role.text

    conversation = await client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": f"会话-{tag}", "role_ids": [role.json()["id"]],
    })
    assert conversation.status_code == 201, conversation.text
    return {
        "headers": headers,
        "config_id": config.json()["id"],
        "role_id": role.json()["id"],
        "role_name": role.json()["name"],
        "conversation_id": conversation.json()["id"],
    }


@pytest.mark.anyio
async def test_role_delete_keeps_identity_and_frees_the_name():
    """删除角色留下墓碑：身份保留、配置清空，同名角色可以立刻重建。"""
    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx = await _bootstrap(client, "tombstone")
            headers, role_id = ctx["headers"], ctx["role_id"]

            assert (await client.delete(f"/api/roles/{role_id}", headers=headers)).status_code == 204

            roles = {role["id"]: role for role in (await client.get("/api/roles", headers=headers)).json()}
            tombstone = roles[role_id]
            # 身份信息保留，历史消息才能显示原名称。
            assert tombstone["name"] == ctx["role_name"]
            assert tombstone["deleted_at"] is not None
            # 可用配置全部清空，且不再是启用状态。
            assert tombstone["active"] is False
            assert tombstone["model_config_id"] is None
            assert tombstone["system_prompt"] == ""
            assert tombstone["model_name"] == ""
            assert tombstone["skills"] == [] and tombstone["mcp_servers"] == [] and tombstone["builtin_tools"] == []

            # 墓碑不可再编辑或重复删除。
            assert (await client.delete(f"/api/roles/{role_id}", headers=headers)).status_code == 404

            # 重名约束只作用于未删除角色，所以同名角色可以立刻重建。
            rebuilt = await client.post("/api/roles", headers=headers, json={
                "name": ctx["role_name"], "system_prompt": "重建后的同名角色",
                "model_config_id": ctx["config_id"], "model_name": "fake-model",
            })
            assert rebuilt.status_code == 201, rebuilt.text
            assert rebuilt.json()["id"] != role_id


@pytest.mark.anyio
async def test_deleted_role_disappears_from_conversation_members():
    """墓碑不再作为孤儿项出现在会话成员里，但会话本身仍然可用。"""
    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx = await _bootstrap(client, "orphan")
            headers, conversation_id = ctx["headers"], ctx["conversation_id"]

            before = (await client.get("/api/conversations", headers=headers)).json()
            assert ctx["role_id"] in next(c for c in before if c["id"] == conversation_id)["role_ids"]

            assert (await client.delete(f"/api/roles/{ctx['role_id']}", headers=headers)).status_code == 204

            after = (await client.get("/api/conversations", headers=headers)).json()
            entry = next(c for c in after if c["id"] == conversation_id)
            assert entry["role_ids"] == []


@pytest.mark.anyio
async def test_conversation_delete_goes_to_recycle_bin_and_restores():
    """会话删除进回收站：列表消失、消息按不存在处理，恢复后完全回到原状。"""
    from app.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx = await _bootstrap(client, "recycle")
            headers, conversation_id = ctx["headers"], ctx["conversation_id"]

            sent = await client.post(
                f"/api/conversations/{conversation_id}/messages", headers=headers,
                json={"parts": [{"type": "text", "text": "回收站测试"}], "client_message_id": "recycle-1"},
            )
            assert sent.status_code == 202, sent.text

            assert (await client.delete(f"/api/conversations/{conversation_id}", headers=headers)).status_code == 204

            listed = [c["id"] for c in (await client.get("/api/conversations", headers=headers)).json()]
            assert conversation_id not in listed
            recycled = (await client.get("/api/conversations/deleted", headers=headers)).json()
            entry = next(c for c in recycled if c["id"] == conversation_id)
            assert entry["deleted_at"] is not None

            # 回收站中的会话对消息链路等同于不存在。
            assert (await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)).status_code == 404
            blocked = await client.post(
                f"/api/conversations/{conversation_id}/messages", headers=headers,
                json={"parts": [{"type": "text", "text": "不应写入"}], "client_message_id": "recycle-2"},
            )
            assert blocked.status_code == 404

            # 重复删除是幂等的，不报错。
            assert (await client.delete(f"/api/conversations/{conversation_id}", headers=headers)).status_code == 204

            restored = await client.post(f"/api/conversations/{conversation_id}/restore", headers=headers)
            assert restored.status_code == 200, restored.text
            assert restored.json()["deleted_at"] is None

            # 恢复后消息历史原样还在：保留期内数据从未被物理删除。
            history = await client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
            assert history.status_code == 200
            assert any(item["parts_json"][0].get("text") == "回收站测试" for item in history.json()["items"])
            assert conversation_id in [c["id"] for c in (await client.get("/api/conversations", headers=headers)).json()]


@pytest.mark.anyio
async def test_purge_only_removes_conversations_past_retention():
    """启动清理只物理删除超过保留期的会话，未到期的留在回收站。"""
    from app.main import app
    from app.db import SessionLocal
    from app.services.retention import RETENTION_DAYS, purge_expired_conversations

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            expired = await _bootstrap(client, "purge-old")
            kept = await _bootstrap(client, "purge-new")
            headers = expired["headers"]

            for ctx in (expired, kept):
                assert (await client.delete(f"/api/conversations/{ctx['conversation_id']}", headers=headers)).status_code == 204

            # 用"未来的现在"代替改数据：把时钟推过保留期，只有先删的那条过期。
            async with SessionLocal() as session:
                result = await purge_expired_conversations(
                    session, now=datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS, seconds=5),
                )
            assert result["conversations"] >= 2

            recycled = [c["id"] for c in (await client.get("/api/conversations/deleted", headers=headers)).json()]
            assert expired["conversation_id"] not in recycled
            assert kept["conversation_id"] not in recycled

            # 已被清理的会话无法恢复，也不再出现在任何列表里。
            gone = await client.post(f"/api/conversations/{expired['conversation_id']}/restore", headers=headers)
            assert gone.status_code == 404
            assert gone.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


@pytest.mark.anyio
async def test_purge_keeps_conversations_within_retention():
    """保留期内的已删除会话不会被清理，仍然可以恢复。"""
    from app.main import app
    from app.db import SessionLocal
    from app.services.retention import purge_expired_conversations

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ctx = await _bootstrap(client, "retention-window")
            headers, conversation_id = ctx["headers"], ctx["conversation_id"]
            assert (await client.delete(f"/api/conversations/{conversation_id}", headers=headers)).status_code == 204

            async with SessionLocal() as session:
                result = await purge_expired_conversations(session)
            assert result["conversations"] == 0

            restored = await client.post(f"/api/conversations/{conversation_id}/restore", headers=headers)
            assert restored.status_code == 200, restored.text
