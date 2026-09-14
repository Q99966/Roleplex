"""测试账号常量与 Owner 获取辅助函数。

实例 Owner 是单例：整轮测试共用同一份数据库，第一个注册者认领 Owner，
之后注册的账号只能是 Guest。因此需要 Owner 权限的用例必须复用同一账号，
并且不能假设自己是第一个注册者——用例执行顺序会变，注册失败要回退到登录。

账号名带上本轮时间戳，与数据库文件名一致，方便测试结束后登录进去核对数据。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from typing import Any

# 时间戳由 conftest 在导入时写入环境变量；单独运行本模块时回退为固定值。
TEST_STAMP = os.environ.get("ROLEPLEX_TEST_STAMP", "local")
OWNER_USERNAME = f"test{TEST_STAMP}"
OWNER_NICKNAME = f"测试 Owner {TEST_STAMP}"
# 固定占位口令，符合密码策略（长度、字母、数字、符号）以免测试账号被强制改密。
# 仅用于本地测试库，不具备任何实际价值，禁止用于真实环境。
TEST_PASSWORD = "Roleplex-Test-1234"
OWNER_PASSWORD = TEST_PASSWORD


@contextmanager
def stable_auth_clock():
    """隔离测试共用单调推进的 UTC，避免宿主墙钟回退使新 JWT 偶发尚未生效。

    只替换测试作用域内的签发/校验时钟，不增加 leeway，不改变签名、有效期或撤销检查。
    """
    import jwt.api_jwt
    from app.security import tokens
    wall, monotonic = datetime.now(timezone.utc), time.monotonic()

    class ClockMeta(type):
        """保留 PyJWT 对普通 datetime 声明的编码兼容。"""
        def __instancecheck__(cls, instance):
            """Args:
                instance：PyJWT 待编码的日期声明。
            """
            return isinstance(instance, datetime)

    class AuthClock(datetime, metaclass=ClockMeta):
        """两个认证边界使用同一 UTC 起点与单调时间差。"""
        @classmethod
        def now(cls, tz=None):
            """Args:
                tz：签发/校验要求的时区，None 保持标准库本地时间语义。
            """
            current = wall + timedelta(seconds=time.monotonic() - monotonic)
            return cls.fromtimestamp(current.timestamp(), tz)

    with patch.object(tokens, 'datetime', AuthClock), patch.object(jwt.api_jwt, 'datetime', AuthClock):
        yield


def guest_username(tag: str) -> str:
    """返回本轮专用的 Guest 账号名。

    Args:
        tag：用例维度的短标识，用于区分同一轮里的多个 Guest。
    """
    return f"{OWNER_USERNAME}_{tag}"


_REGISTER_BODY = {"username": OWNER_USERNAME, "password": OWNER_PASSWORD, "nickname": OWNER_NICKNAME}
_LOGIN_BODY = {"username": OWNER_USERNAME, "password": OWNER_PASSWORD}


def _unwrap(register: Any, login_factory) -> dict:
    """校验注册结果；账号已存在时改为登录并返回令牌响应体。"""
    if register.status_code == 201:
        return register.json()
    assert register.status_code == 409, register.text
    login = login_factory()
    assert login.status_code == 200, login.text
    return login.json()


async def ensure_owner_async(client) -> dict:
    """用异步客户端拿到 Owner 的令牌响应体（含 access_token 与 user）。"""
    register = await client.post("/api/auth/register", json=_REGISTER_BODY)
    if register.status_code == 201:
        return register.json()
    assert register.status_code == 409, register.text
    login = await client.post("/api/auth/login", json=_LOGIN_BODY)
    assert login.status_code == 200, login.text
    return login.json()


def ensure_owner_sync(client) -> dict:
    """用同步客户端拿到 Owner 的令牌响应体（含 access_token 与 user）。"""
    register = client.post("/api/auth/register", json=_REGISTER_BODY)
    return _unwrap(register, lambda: client.post("/api/auth/login", json=_LOGIN_BODY))
