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


def create_access_token(user: User) -> str:
    """签发带有效期并绑定用户当前 Token 版本的 JWT。"""
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "ver": user.token_version, "iat": now, "exp": now + timedelta(days=settings.access_token_days)}
    return jwt.encode(payload, settings.resolved_jwt_secret(), algorithm=settings.jwt_algorithm)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """解析当前认证用户，并拒绝缺失、无效或已撤销的 Token。

    Args:
        credentials：由 FastAPI 提取的 Bearer 凭据。
        session：用于查询 Token 版本的请求级数据库会话。

    Returns:
        当前认证用户记录。
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
    return user


async def require_owner(user: Annotated[User, Depends(get_current_user)]) -> User:
    """要求调用者是实例 Owner，才能访问配置和管理路由。"""
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="OWNER_REQUIRED")
    return user
