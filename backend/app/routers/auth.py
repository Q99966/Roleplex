from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..security import passwords as password_policy
from ..db import get_session
from ..config.logging import current_request_id, set_log_context
from ..models import InstanceSettings, User
from ..security.passwords import PasswordPolicyError, hash_password, verify_password
from ..schemas import ChangePasswordRequest, LoginRequest, RegisterRequest, TokenResponse, UserResponse
from ..security.tokens import (
    create_access_token,
    get_current_user,
    get_current_user_pending_password_reset,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger("roleplex.auth")


def _reject_weak_password(password: str) -> None:
    """按统一策略校验新密码，不合规时抛出稳定错误码。

    注册和改密共用本函数，保证两条路径的规则与错误码完全一致。

    Args:
        password：用户提交的新密码明文，仅在内存中使用，不写日志。

    Raises:
        HTTPException：422 `PASSWORD_POLICY_VIOLATION`，`details` 逐条说明未通过的要求。
    """
    try:
        password_policy.validate(password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PASSWORD_POLICY_VIOLATION", "details": exc.reasons},
        ) from exc


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, session: Annotated[AsyncSession, Depends(get_session)]):
    """创建账号，并通过原子操作将首个注册者设为 Owner。"""
    password = payload.password.get_secret_value()
    _reject_weak_password(password)

    existing = await session.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=409, detail="USERNAME_TAKEN")

    user = User(
        username=payload.username,
        password_hash=hash_password(password),
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
    """认证用户，并签发绑定其 Token 版本的访问 Token。

    弱密码不拒绝登录：分发出去的世界使用初始弱口令，必须先能登进来才有机会改密。
    但签发的 Token 会带待改密标记，除改密和查看本人资料外的接口一律被拒绝，
    同时响应体显式回传该状态供前端跳转。
    """
    user = await session.scalar(select(User).where(User.username == payload.username))
    password = payload.password.get_secret_value()
    if not user or not verify_password(password, user.password_hash):
        logger.info(
            "auth.login_failed",
            extra={
                "username": payload.username,
                "reason": "invalid_credentials",
                "status": "rejected",
                "request_id": current_request_id(),
            },
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_INVALID")
    reset_required = not password_policy.is_compliant(password)
    set_log_context(user_id=user.id)
    logger.info(
        "auth.login_succeeded",
        extra={
            "username": user.username,
            "user_id": user.id,
            "password_reset_required": reset_required,
            "status": "success",
        },
    )
    return TokenResponse(
        access_token=create_access_token(user, password_reset_required=reset_required),
        user=UserResponse.model_validate(user),
        password_reset_required=reset_required,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user_pending_password_reset)]):
    """返回当前认证用户的公开资料。

    待改密用户显式放行：重置页需要展示当前账号身份，若在此拦截会导致
    前端刷新后无法确定登录状态。
    """
    return UserResponse.model_validate(user)


@router.post("/password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    user: Annotated[User, Depends(get_current_user_pending_password_reset)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """校验旧密码后设置新密码，撤销全部旧 Token 并返回新 Token。

    待改密用户显式放行，否则弱口令账号无法完成重置。改密后递增
    `token_version`，使包含待改密标记的旧 Token 立即失效；返回的新 Token
    不带该标记，前端可以直接进入工作台。

    Args:
        payload：旧密码与新密码。
        user：当前认证用户，允许带待改密标记。
        session：请求级数据库会话。

    Raises:
        HTTPException：401 `AUTH_INVALID` 旧密码不正确；
            422 `PASSWORD_POLICY_VIOLATION` 新密码不合规；
            409 `PASSWORD_UNCHANGED` 新密码与旧密码相同。
    """
    current_password = payload.current_password.get_secret_value()
    new_password = payload.new_password.get_secret_value()
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="AUTH_INVALID")
    _reject_weak_password(new_password)
    if new_password == current_password:
        # 允许旧密码本身合规的用户改密，但不允许"改成同一个"绕过强制重置。
        raise HTTPException(status_code=409, detail="PASSWORD_UNCHANGED")

    user.password_hash = hash_password(new_password)
    user.token_version += 1
    await session.commit()
    await session.refresh(user)
    return TokenResponse(access_token=create_access_token(user), user=UserResponse.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_session)]):
    """递增用户 Token 版本，使当前已签发的 Token 全部失效。"""
    await session.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    await session.commit()
