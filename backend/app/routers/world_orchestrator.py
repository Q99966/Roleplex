"""当前 World Owner 的角色任命与协调入口。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from ..db import SessionLocal
from ..models import User, WorldTask, WorldMemory, WorldMemoryVersion
from ..security import require_owner
from ..world_orchestrator import service
from ..schemas import RoleCreate

router = APIRouter(prefix='/api/world-orchestrator', tags=['world-orchestrator'])
Owner = Annotated[User, Depends(require_owner)]


class Appointment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    role_id: int | None = Field(gt=0)
    expected_revision: int = Field(ge=0, le=2**31 - 1)


@router.get('')
async def get(response: Response, user: Owner):
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return await service.view(session, user.id)


@router.put('')
async def put(payload: Appointment, response: Response, user: Owner):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await service.appoint(user.id, payload.role_id, payload.expected_revision)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_ORCHESTRATOR_STORAGE_UNAVAILABLE') from None


@router.get('/tasks')
async def list_tasks(response: Response, user: Owner):
    from ..world_orchestrator import tasks
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        rows = (await session.scalars(select(WorldTask).where(WorldTask.owner_id == user.id).order_by(WorldTask.created_at.desc()).limit(50))).all()
        return {'items': [await tasks.view(session, row) for row in rows]}


class ManagerProfile(RoleCreate):
    expected_revision: int = Field(ge=0, strict=True)


async def change_manager(uid, **kwargs):
    """存储故障返回固定码，配置与 SQL 参数不会进入公开错误。"""
    try:
        return await service.change(uid, **kwargs)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_ORCHESTRATOR_STORAGE_UNAVAILABLE') from None


@router.put('/config')
async def configure(payload: ManagerProfile, response: Response, user: Owner):
    """修改固定岗位的模型/人设，CAS 使用 profile.revision；保留身份与历史。"""
    response.headers['Cache-Control'] = 'no-store'
    return await change_manager(user.id, profile=payload)


@router.post('/import-role')
async def import_role(payload: Appointment, response: Response, user: Owner):
    """从同 World 普通角色复制配置，保留专用管理者名称和身份。"""
    response.headers['Cache-Control'] = 'no-store'
    if payload.role_id is None: raise HTTPException(422, 'WORLD_ORCHESTRATOR_ROLE_UNAVAILABLE')
    return await change_manager(user.id, source_role_id=payload.role_id, expected_revision=payload.expected_revision)


class Enabled(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool
    expected_revision: int = Field(ge=0)


@router.post('/enabled')
async def enable(payload: Enabled, response: Response, user: Owner):
    """启停职责时撤销旧版本的派发，重新启用不会恢复旧任务。"""
    response.headers['Cache-Control'] = 'no-store'
    return await change_manager(user.id, enabled=payload.enabled, expected_revision=payload.expected_revision)


class Control(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0)


@router.post('/tasks/{task_id}/stop')
async def stop(task_id: str, payload: Control, user: Owner):
    from ..world_orchestrator import tasks
    return await tasks.stop(task_id, user.id, payload.expected_revision)


@router.post('/tasks/{task_id}/children/{child_id}/stop')
async def stop_child(task_id: str, child_id: str, payload: Control, user: Owner):
    from ..world_orchestrator import tasks
    return await tasks.stop_child(task_id, child_id, user.id, payload.expected_revision)


from ..world_orchestrator import memory


@router.get('/memories')
async def memories(user: Owner, response: Response, query: str = Query(default='', max_length=200)):
    response.headers['Cache-Control'] = 'no-store'
    return await memory.search(user.id, query, include_disabled=True)


@router.get('/memories/{memory_id}')
async def read_memory(memory_id: str, revision: int, user: Owner, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await memory.read(user.id, memory_id, revision, admin=True)


@router.post('/memories', status_code=201)
async def save_memory(payload: memory.Save, user: Owner):
    try:
        return await memory.save(user.id, payload)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_ORCHESTRATOR_STORAGE_UNAVAILABLE') from None


@router.put('/memories/{memory_id}')
async def edit_memory(memory_id: str, payload: memory.Edit, user: Owner):
    try:
        return await memory.edit(user.id, memory_id, payload)
    except SQLAlchemyError:
        raise HTTPException(503, 'WORLD_ORCHESTRATOR_STORAGE_UNAVAILABLE') from None


@router.get('/memories/{memory_id}/versions')
async def memory_versions(memory_id: str, user: Owner, response: Response):
    from types import SimpleNamespace
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        current = await session.get(WorldMemory, memory_id)
        if not current or current.owner_id != user.id:
            raise HTTPException(404, 'WORLD_MEMORY_NOT_FOUND')
        versions = (await session.scalars(select(WorldMemoryVersion).where(WorldMemoryVersion.memory_id == memory_id)
            .order_by(WorldMemoryVersion.revision.desc()).limit(50))).all()
        return {'items': [await memory.view(session, SimpleNamespace(**v.snapshot_json, updated_at=v.created_at), full=True, admin=True) for v in versions]}
