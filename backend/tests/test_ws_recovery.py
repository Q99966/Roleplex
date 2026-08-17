"""WebSocket 事件恢复集成测试。

覆盖三条可靠性要求：首帧认证失败必须直接关闭连接、重连后 backlog 不丢不重、
stream epoch 变化时回落到完整快照。测试使用同步 TestClient，因为 httpx
本身不支持 WebSocket。
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from accounts import OWNER_NICKNAME, OWNER_PASSWORD, OWNER_USERNAME

# 单次读取的帧数上限，协议异常时用例应失败而不是挂死。
_MAX_FRAMES = 400


@pytest.fixture(scope="module")
def client():
    """提供已进入 lifespan 的测试客户端，使事件广播器和数据库完成初始化。"""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _owner_token(client: TestClient) -> str:
    """返回 Owner 的访问 Token；账号已存在时改为登录，保证复用同一个 Owner。"""
    register = client.post("/api/auth/register", json={
        "username": OWNER_USERNAME, "password": OWNER_PASSWORD, "nickname": OWNER_NICKNAME,
    })
    if register.status_code == 201:
        return register.json()["access_token"]
    assert register.status_code == 409, register.text
    login = client.post("/api/auth/login", json={
        "username": OWNER_USERNAME, "password": OWNER_PASSWORD,
    })
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _single_chat(client: TestClient, token: str, title: str) -> int:
    """创建一个带角色成员的单聊会话，返回会话 ID。"""
    headers = {"Authorization": f"Bearer {token}"}
    config = client.post("/api/model-configs", headers=headers, json={
        "name": f"ws-{title}", "provider_type": "openai_compatible", "api_key": "sk-test-placeholder",
    })
    assert config.status_code == 201, config.text
    role = client.post("/api/roles", headers=headers, json={
        "name": f"ws-助手-{title}", "system_prompt": "你是测试助手",
        "model_config_id": config.json()["id"], "model_name": "fake-model",
    })
    assert role.status_code == 201, role.text
    conversation = client.post("/api/conversations", headers=headers, json={
        "type": "single", "title": title, "role_ids": [role.json()["id"]],
    })
    assert conversation.status_code == 201, conversation.text
    return conversation.json()["id"]


def _authenticate(ws, token: str) -> str:
    """完成首帧认证并返回服务端当前的 stream epoch。"""
    ws.send_json({"type": "auth", "token": token})
    frame = ws.receive_json()
    assert frame["type"] == "auth_ok", frame
    return frame["stream_epoch"]


def _read_until(ws, terminal_type: str) -> list[dict]:
    """读取事件帧直到出现指定类型，返回包含该帧的全部帧。"""
    frames: list[dict] = []
    for _ in range(_MAX_FRAMES):
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == terminal_type:
            return frames
    raise AssertionError(f"未在 {_MAX_FRAMES} 帧内收到 {terminal_type}")


def _send_message(client: TestClient, token: str, conversation_id: int, client_message_id: str) -> None:
    """发送一条用户消息并触发 fake 生成。"""
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"parts": [{"type": "text", "text": "你好"}], "client_message_id": client_message_id},
    )
    assert response.status_code == 202, response.text


def _wait_until_done(client: TestClient, token: str, conversation_id: int) -> int:
    """轮询等待角色回复完成，返回会话当前事件序号。"""
    for _ in range(100):
        time.sleep(0.1)
        history = client.get(
            f"/api/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = history.json()
        if any(m["sender_type"] == "role" and m["status"] == "done" for m in body["items"]):
            return body["event_seq"]
    raise AssertionError("fake 生成没有在预期时间内完成")


@pytest.mark.parametrize("first_frame", [
    {"type": "auth", "token": "not-a-real-token"},
    {"type": "subscribe", "conversation_id": 1},
])
def test_first_frame_must_carry_valid_token(client: TestClient, first_frame: dict):
    """首帧不是有效认证帧时连接必须被策略性关闭，不进入订阅流程。"""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json(first_frame)
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_subscribe_requires_conversation_membership(client: TestClient):
    """非成员订阅会话时按不存在处理，不泄露会话是否存在。"""
    owner_token = _owner_token(client)
    conversation_id = _single_chat(client, owner_token, "成员校验")

    guest = client.post("/api/auth/register", json={
        "username": "ws_guest", "password": "password123", "nickname": "Guest",
    })
    assert guest.status_code == 201, guest.text
    assert guest.json()["user"]["is_owner"] is False

    with client.websocket_connect("/api/ws") as ws:
        _authenticate(ws, guest.json()["access_token"])
        ws.send_json({"type": "subscribe", "conversation_id": conversation_id})
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert frame["payload"]["code"] == "CONVERSATION_NOT_FOUND"


def test_reconnect_backlog_is_gapless(client: TestClient):
    """断线重连按游标回放：实时流与 backlog 都连续，且不重复已应用的事件。"""
    token = _owner_token(client)
    conversation_id = _single_chat(client, token, "断线恢复")

    with client.websocket_connect("/api/ws") as ws:
        epoch = _authenticate(ws, token)
        ws.send_json({
            "type": "subscribe", "conversation_id": conversation_id,
            "after_event_seq": 0, "stream_epoch": epoch,
        })
        assert ws.receive_json() == {
            "type": "subscribed", "stream_epoch": epoch,
            "conversation_id": conversation_id, "event_seq": 0,
        }
        _send_message(client, token, conversation_id, "ws-live")
        live = _read_until(ws, "message_done")

    latest = live[-1]["event_seq"]
    assert [frame["event_seq"] for frame in live] == list(range(1, latest + 1))
    assert live[0]["payload"]["message"]["sender_type"] == "user"

    # 假设客户端只应用到中间某个事件就断线，重连后必须补齐其后的全部事件。
    cut = latest // 2
    with client.websocket_connect("/api/ws") as ws:
        assert _authenticate(ws, token) == epoch
        ws.send_json({
            "type": "subscribe", "conversation_id": conversation_id,
            "after_event_seq": cut, "stream_epoch": epoch,
        })
        head = ws.receive_json()
        assert head["type"] == "subscribed" and head["event_seq"] == latest
        replay = [ws.receive_json() for _ in range(latest - cut)]

    assert [frame["event_seq"] for frame in replay] == list(range(cut + 1, latest + 1))
    assert replay[-1]["type"] == "message_done"
    assert replay[-1]["payload"]["message"]["status"] == "done"


def test_stale_epoch_falls_back_to_snapshot(client: TestClient):
    """客户端携带过期 epoch 时必须收到完整快照，而不是按序号增量回放。"""
    token = _owner_token(client)
    conversation_id = _single_chat(client, token, "快照回落")
    _send_message(client, token, conversation_id, "ws-snapshot")
    latest = _wait_until_done(client, token, conversation_id)

    with client.websocket_connect("/api/ws") as ws:
        epoch = _authenticate(ws, token)
        ws.send_json({
            "type": "subscribe", "conversation_id": conversation_id,
            "after_event_seq": 0, "stream_epoch": "epoch-from-a-previous-process",
        })
        frame = ws.receive_json()

    assert frame["type"] == "snapshot"
    assert frame["stream_epoch"] == epoch
    snapshot = frame["payload"]
    assert snapshot["event_seq"] == latest
    assert [m["sender_type"] for m in snapshot["messages"]] == ["user", "role"]
    assert snapshot["active_generation_id"] is None
