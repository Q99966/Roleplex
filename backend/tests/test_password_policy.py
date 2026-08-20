"""密码策略、强制重置与改密的集成测试。

覆盖三条安全要求：
1. 注册时不合规密码被拒，且返回稳定错误码而不是笼统的参数校验错；
2. 弱口令账号可以登录（分发出去的世界必须先能进得来），但除改密和查看
   本人资料外的接口一律被拒，WebSocket 首帧同样被拒；
3. 改密后旧 Token 立即失效，新 Token 不再带待改密标记。

弱口令账号直接写库创建，因为注册接口已经拒绝弱密码——这正是分发包和
历史遗留账号的真实形态。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from accounts import TEST_PASSWORD, ensure_owner_sync

# 分发包中的初始弱口令形态：长度不足且缺少数字与符号。
WEAK_PASSWORD = "owner"

# 合规的替换口令，仅用于测试库，无实际价值。
STRONG_PASSWORD = "Roleplex-Reset-9876"


@pytest.fixture(scope="module")
def client():
    """提供已进入 lifespan 的测试客户端，使数据库和事件广播器完成初始化。"""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _create_weak_password_user(username: str) -> None:
    """直接写库创建一个使用弱口令的账号。

    用同步 sqlite3 而不是应用的 async engine：TestClient 在自己的事件循环里
    持有该 engine，从测试线程另起事件循环复用它会导致连接归属错乱。这里只做
    一次短写入，WAL 下与应用连接并存是安全的。

    Args:
        username：账号名，用例之间必须互不相同，避免共享本轮数据库时冲突。
    """
    import os
    import sqlite3
    from datetime import datetime, timezone

    from app.security import hash_password

    # conftest 已把 DATABASE_URL 指向本轮测试库，这里取出其中的文件路径。
    database_path = os.environ["DATABASE_URL"].split("///", 1)[1]
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute(
            "INSERT INTO users (username, password_hash, nickname, avatar, is_owner, token_version, created_at)"
            " VALUES (?, ?, ?, NULL, 0, 0, ?)",
            (username, hash_password(WEAK_PASSWORD), f"弱口令 {username}", datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def test_policy_rejects_short_missing_category_and_overlong_passwords():
    """策略模块本身覆盖长度、字符类别和 bcrypt 字节上限。"""
    from app import password_policy

    assert password_policy.is_compliant("Roleplex-Test-1234")
    # 缺符号、缺数字、缺字母、长度不足各自都必须被判为不合规。
    assert not password_policy.is_compliant("Roleplex12345")
    assert not password_policy.is_compliant("Roleplex-Test")
    assert not password_policy.is_compliant("1234567890-!@#")
    assert not password_policy.is_compliant("Ab1-cdef")
    # 超过 72 字节的密码会被 bcrypt 静默截断，必须在策略层拒绝。
    assert not password_policy.is_compliant("A1-" + "x" * 80)
    # 长度按字符计、上限按字节计：中文密码字符数达标也可能因为编码后过长被拒。
    overlong_cjk = "密码" * 12 + "Ab1-"
    assert len(overlong_cjk) >= password_policy.MIN_LENGTH
    assert len(overlong_cjk.encode("utf-8")) > password_policy.MAX_BYTES
    assert not password_policy.is_compliant(overlong_cjk)


def test_register_rejects_weak_password_with_stable_error_code(client: TestClient):
    """注册弱密码返回 422 与稳定错误码，并逐条说明原因。"""
    response = client.post("/api/auth/register", json={
        "username": "weak_register_user", "password": "short", "nickname": "弱口令注册",
    })
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "PASSWORD_POLICY_VIOLATION"
    assert body["details"], "错误详情应逐条说明未通过的要求"


def test_weak_password_login_is_gated_until_reset(client: TestClient):
    """弱口令账号能登录，但除改密和本人资料外的接口与 WebSocket 全部被拒。"""
    username = "weak_gated_user"
    _create_weak_password_user(username)

    login = client.post("/api/auth/login", json={"username": username, "password": WEAK_PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["password_reset_required"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 放行的两个接口之一：重置页需要展示当前账号身份。
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    # 默认拒绝：业务接口与登出都必须被挡下。
    for method, path in (("get", "/api/conversations"), ("get", "/api/roles"), ("post", "/api/auth/logout")):
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
        assert response.json()["error"]["code"] == "PASSWORD_RESET_REQUIRED"

    # WebSocket 首帧认证同样拒绝，否则弱口令账号仍能订阅事件流。
    with client.websocket_connect("/api/ws") as ws:
        ws.send_json({"type": "auth", "token": login.json()["access_token"]})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_change_password_revokes_old_token_and_clears_flag(client: TestClient):
    """改密后旧 Token 失效、标记消失，新 Token 可以正常访问业务接口。"""
    username = "weak_reset_user"
    _create_weak_password_user(username)

    login = client.post("/api/auth/login", json={"username": username, "password": WEAK_PASSWORD})
    assert login.status_code == 200, login.text
    old_token = login.json()["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    # 新密码同样要过策略校验，错误码与注册一致。
    weak_attempt = client.post("/api/auth/password", headers=old_headers, json={
        "current_password": WEAK_PASSWORD, "new_password": "still-weak",
    })
    assert weak_attempt.status_code == 422
    assert weak_attempt.json()["error"]["code"] == "PASSWORD_POLICY_VIOLATION"

    # 旧密码不正确时不得改密。
    wrong_current = client.post("/api/auth/password", headers=old_headers, json={
        "current_password": "not-the-password", "new_password": STRONG_PASSWORD,
    })
    assert wrong_current.status_code == 401
    assert wrong_current.json()["error"]["code"] == "AUTH_INVALID"

    changed = client.post("/api/auth/password", headers=old_headers, json={
        "current_password": WEAK_PASSWORD, "new_password": STRONG_PASSWORD,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["password_reset_required"] is False
    new_headers = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    # 旧 Token 因 token_version 递增而失效。
    revoked = client.get("/api/auth/me", headers=old_headers)
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "AUTH_REVOKED"

    # 新 Token 不再带标记，业务接口恢复可用。
    assert client.get("/api/conversations", headers=new_headers).status_code == 200

    # 改完之后可以用新密码重新登录，且不再要求重置。
    relogin = client.post("/api/auth/login", json={"username": username, "password": STRONG_PASSWORD})
    assert relogin.status_code == 200, relogin.text
    assert relogin.json()["password_reset_required"] is False


def test_compliant_owner_login_does_not_require_reset(client: TestClient):
    """合规口令的既有账号登录后不受影响，保证回归护栏不被策略改动波及。"""
    owner = ensure_owner_sync(client)
    login = client.post("/api/auth/login", json={
        "username": owner["user"]["username"], "password": TEST_PASSWORD,
    })
    assert login.status_code == 200, login.text
    assert login.json()["password_reset_required"] is False
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/conversations", headers=headers).status_code == 200
