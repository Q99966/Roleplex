from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """对用户密码进行哈希，且不保留明文。

    Args:
        password：仅在认证输入阶段接收的明文密码。

    Returns:
        适合存入数据库的 bcrypt 编码密码哈希。
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """使用已存储的 bcrypt 哈希校验候选密码。"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _fernet() -> Fernet:
    """构造用于模型厂商 API Key 的认证加密器。"""
    secret = settings.resolved_api_key_secret().encode()
    # Fernet 需要 32 字节的 URL 安全 Base64 密钥，这里根据实例密钥确定性派生。
    import base64
    import hashlib
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt_api_key(value: str) -> str:
    """在厂商 Key 写入数据库前进行加密。"""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_api_key(value: str) -> str:
    """仅为已授权的内部集成解密厂商 Key。"""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("无法解密模型厂商 API Key") from exc


# Token 中标记"该用户必须先改密"的声明名；旧 Token 没有该字段时按不需要改密处理。
PASSWORD_RESET_CLAIM = "pwr"


def create_access_token(user: User, *, password_reset_required: bool = False) -> str:
    """签发带有效期并绑定用户当前 Token 版本的 JWT。

    Args:
        user：本次登录或改密后的用户记录。
        password_reset_required：登录时口令不符合当前策略。标记写进 Token，
            使后续每个请求都能在不查库的情况下被拦截；同时登录响应体会显式
            返回该状态，前端据此跳转，不需要解析 JWT。

    Returns:
        编码后的访问 Token。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(days=settings.access_token_days),
    }
    if password_reset_required:
        payload[PASSWORD_RESET_CLAIM] = True
    return jwt.encode(payload, settings.resolved_jwt_secret(), algorithm=settings.jwt_algorithm)


def token_requires_password_reset(payload: dict) -> bool:
    """判断已解码的 Token 是否被标记为必须先改密。"""
    return payload.get(PASSWORD_RESET_CLAIM) is True


async def _resolve_token_user(
    credentials: HTTPAuthorizationCredentials | None,
    session: AsyncSession,
) -> tuple[User, bool]:
    """校验 Bearer 凭据并返回用户及其待改密状态。

    Args:
        credentials：由 FastAPI 提取的 Bearer 凭据。
        session：用于查询 Token 版本的请求级数据库会话。

    Returns:
        `(用户记录, 是否被标记为必须先改密)`。

    Raises:
        HTTPException：凭据缺失、签名无效、用户不存在或 Token 版本已被撤销。
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_REQUIRED")
    try:
        payload = jwt.decode(credentials.credentials, settings.resolved_jwt_secret(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="AUTH_INVALID") from exc
    user = await session.get(User, user_id)
    if not user or user.token_version != payload.get("ver"):
        raise HTTPException(status_code=401, detail="AUTH_REVOKED")
    return user, token_requires_password_reset(payload)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """解析当前认证用户，并拒绝缺失、无效、已撤销或待改密的 Token。

    这是默认依赖，采用默认拒绝：带待改密标记的 Token 在这里被挡下，
    因此新增路由自动受保护。只有"改密"和"查看本人资料"两个接口显式改用
    `get_current_user_pending_password_reset` 放行，否则用户无从完成改密。

    Args:
        credentials：由 FastAPI 提取的 Bearer 凭据。
        session：用于查询 Token 版本的请求级数据库会话。

    Returns:
        当前认证用户记录。

    Raises:
        HTTPException：403 `PASSWORD_RESET_REQUIRED` 表示必须先重置密码。
    """
    user, reset_required = await _resolve_token_user(credentials, session)
    if reset_required:
        raise HTTPException(status_code=403, detail="PASSWORD_RESET_REQUIRED")
    return user


async def get_current_user_pending_password_reset(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """解析当前认证用户，且允许其 Token 带待改密标记。

    仅供改密和查看本人资料使用：这两个接口是弱密码用户唯一还需要走的路径。
    其余接口一律使用 `get_current_user`。
    """
    user, _ = await _resolve_token_user(credentials, session)
    return user


async def require_owner(user: Annotated[User, Depends(get_current_user)]) -> User:
    """要求调用者是实例 Owner，才能访问配置和管理路由。"""
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="OWNER_REQUIRED")
    return user
