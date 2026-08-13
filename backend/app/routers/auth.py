from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import InstanceSettings, User
from ..schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from ..security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, session: Annotated[AsyncSession, Depends(get_session)]):
    """创建账号，并通过原子操作将首个注册者设为 Owner。"""
    existing = await session.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=409, detail="USERNAME_TAKEN")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
        nickname=payload.nickname,
        is_owner=False,
        token_version=0,
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.flush()

    # 原子认领预创建的单例记录，只有一个并发注册者可以成为 Owner。
    claim = await session.execute(
        update(InstanceSettings)
        .where(InstanceSettings.id == 1, InstanceSettings.owner_user_id.is_(None))
        .values(owner_user_id=user.id)
    )
    user.is_owner = claim.rowcount == 1
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="REGISTRATION_CONFLICT") from exc
    return TokenResponse(access_token=create_access_token(user), user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: Annotated[AsyncSession, Depends(get_session)]):
    """认证用户，并签发绑定其 Token 版本的访问 Token。"""
    user = await session.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password.get_secret_value(), user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_INVALID")
    return TokenResponse(access_token=create_access_token(user), user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]):
    """返回当前认证用户的公开资料。"""
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=204)
async def logout(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """递增用户 Token 版本，使当前已签发的 Token 全部失效。"""
    await session.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    await session.commit()
