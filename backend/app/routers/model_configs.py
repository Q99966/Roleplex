from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import ModelConfig, User
from ..schemas import ModelConfigCreate, ModelConfigResponse
from ..security.credentials import encrypt_api_key
from ..security.tokens import require_owner

router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])


def key_hint(value: str) -> str:
    """为已存储的厂商 Key 返回不可逆的脱敏结果。"""
    return "••••"


def to_response(config: ModelConfig) -> ModelConfigResponse:
    """转换模型配置，且不暴露加密的 Key 材料。"""
    raw = config.api_key_encrypted
    return ModelConfigResponse(
        id=config.id, name=config.name, provider_type=config.provider_type,
        base_url=config.base_url, api_key_hint=key_hint(raw),
        capability_overrides=config.capability_overrides_json or {}, created_at=config.created_at,
    )


@router.get("", response_model=list[ModelConfigResponse])
async def list_configs(user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """仅列出当前 Owner 拥有的模型厂商配置。"""
    configs = (await session.scalars(select(ModelConfig).where(ModelConfig.created_by == user.id).order_by(ModelConfig.created_at.desc()))).all()
    return [to_response(config) for config in configs]


@router.post("", response_model=ModelConfigResponse, status_code=201)
async def create_config(payload: ModelConfigCreate, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """加密并保存模型厂商配置，不返回明文 Key。"""
    config = ModelConfig(
        created_by=user.id, name=payload.name, provider_type=payload.provider_type,
        base_url=payload.base_url, api_key_encrypted=encrypt_api_key(payload.api_key.get_secret_value()),
        capability_overrides_json=payload.capability_overrides, created_at=datetime.now(timezone.utc),
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return to_response(config)


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: int, user: Annotated[User, Depends(require_owner)], session: Annotated[AsyncSession, Depends(get_session)]):
    """删除 Owner 拥有的配置；若仍被角色引用则拒绝删除。"""
    config = await session.scalar(select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.created_by == user.id))
    if not config:
        raise HTTPException(status_code=404, detail="MODEL_CONFIG_NOT_FOUND")
    await session.delete(config)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="MODEL_CONFIG_IN_USE") from exc
