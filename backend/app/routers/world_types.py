"""Owner 当前世界的类型设置与初始化；类型实现仅来自受信注册表。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import User
from ..security import require_owner
from ..world_types import registry, service

router = APIRouter(tags=['world-types'])
Owner = Annotated[User, Depends(require_owner)]


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0, le=2**31 - 1)


class Configuration(Revision):
    configuration: dict


@router.get('/api/world-types')
async def types(response: Response, user: Owner):
    """列出受信装配的类型和配置 schema，不启动类型活动。"""
    response.headers['Cache-Control'] = 'no-store'
    return {'items': registry.catalog()}


@router.get('/api/world-type')
async def current(response: Response, user: Owner):
    """只读当前物理 World 的配置、初始化凭据与已授权概览。"""
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return await service.view(session, user.id)


async def result(uid):
    async with SessionLocal() as session:
        return await service.view(session, uid)


@router.put('/api/world-type/config')
async def configure(payload: Configuration, response: Response, user: Owner):
    """CAS 保存设置并尝试幂等初始化；初始化失败仍返回已保存的配置状态。"""
    response.headers['Cache-Control'] = 'no-store'
    try:
        await service.save_configuration(user.id, payload.expected_revision, payload.configuration)
        return await result(user.id)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_TYPE_STORAGE_UNAVAILABLE') from None


@router.post('/api/world-type/initialize')
async def initialize(payload: Revision, response: Response, user: Owner):
    """显式重试当前配置的初始化，不重复创建已完成资源。"""
    response.headers['Cache-Control'] = 'no-store'
    try:
        await service.initialize(payload.expected_revision)
        return await result(user.id)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_TYPE_STORAGE_UNAVAILABLE') from None
