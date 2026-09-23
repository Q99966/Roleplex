"""Owner 世界列表与受控重启切换接口。"""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
import asyncio
import tempfile
from pathlib import Path
from contextvars import copy_context
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..config.logging import set_process_stop_reason
from ..models import User
from ..security.tokens import require_owner
from ..worlds import WorldManager, WorldActiveError
from ..worlds.compatibility import assert_world_compatible, WorldRequiresNewerRoleplex, WorldTypeUnavailable
from ..runtime.manager import manager as runtime_manager, settled
from ..runtime.registry import RuntimeRejected

logger = logging.getLogger("roleplex.worlds")
router = APIRouter(prefix="/api/worlds", tags=["worlds"])
manager = WorldManager(settings.worlds_dir)


class WorldSwitchRequest(BaseModel):
    """世界切换请求。"""

    name: str = Field(min_length=1, max_length=64)


class WorldCreateRequest(BaseModel):
    """创建独立空世界；名称的路径约束由 WorldManager 统一校验。"""

    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    world_type: str = Field(default='general', pattern=r'^[a-z][a-z0-9_]{0,63}$')
    type_version: int | None = Field(default=None, strict=True, ge=1, le=2**31 - 1)


class WorldBackupRequest(BaseModel):
    """导出包括密钥的完整世界前，必须明确确认停止其托管实例。"""
    model_config = ConfigDict(extra='forbid')
    confirm_cleanup: bool = Field(default=False, strict=True)


class WorldExportResponse(FileResponse):
    """含密钥的临时导出必须在成功、断线或发送异常后都清理。"""
    def __init__(self, temporary, archive):
        """Args:
            temporary：宿主独占的临时目录对象。
            archive：已生成的归档路径。
        """
        self.temporary = temporary
        super().__init__(archive, media_type='application/zip', filename=archive.name, headers={'Cache-Control': 'no-store'})

    async def __call__(self, scope, receive, send):
        """Args:
            scope：ASGI 请求上下文。
            receive：请求消息通道。
            send：响应通道，可能因下载中断抛错。
        """
        try:
            await super().__call__(scope, receive, send)
        finally:
            await settled(asyncio.create_task(asyncio.to_thread(self.temporary.cleanup), context=copy_context()))


@router.post('/backup')
async def backup_world(payload: WorldBackupRequest, owner: Annotated[User, Depends(require_owner)]):
    """协调回收后导出当前 World；正文包含密钥，禁止缓存及普通日志采集。

    Args:
        payload：Owner 对本次停止与导出的确认。
        owner：当前 World Owner，不能凭名称导出其他 World。
    """
    if not settings.world_managed:
        raise HTTPException(409, 'WORLD_OPERATION_REQUIRES_MANAGED')
    if not payload.confirm_cleanup:
        raise HTTPException(409, 'RUNTIME_CLEANUP_CONFIRM_REQUIRED')
    temporary = tempfile.TemporaryDirectory(prefix='roleplex-world-export-')
    try:
        async with runtime_manager.world_operation_lock:
            if runtime_manager.switch_target is not None:
                raise HTTPException(409, 'WORLD_OPERATION_IN_PROGRESS')
            async with runtime_manager.cleanup_scope('world', 0, 'world_backup', owner.id):
                # 线程复制不能在请求取消后与临时目录删除或新服务启动交错。
                try:
                    archive = await settled(asyncio.create_task(asyncio.to_thread(
                        manager.backup_owned, settings.world_name, temporary.name), context=copy_context()))
                except (OSError, WorldActiveError):
                    raise RuntimeRejected('WORLD_OPERATION_FAILED') from None
        return WorldExportResponse(temporary, archive)
    except BaseException as exc:
        temporary.cleanup()
        if isinstance(exc, (OSError, WorldActiveError)):
            raise HTTPException(503, 'WORLD_OPERATION_FAILED') from None
        raise


def switching_supported() -> bool:
    """只有世界托管模式且包装器提供控制文件时允许切换。"""
    return settings.world_managed and bool(settings.world_control_file)


def schedule_shutdown() -> None:
    """在响应发出后向当前后端进程发送正常终止信号。"""
    # SIGINT 会进入 Uvicorn 已安装的优雅关闭处理；Windows 的 SIGTERM 会直接 TerminateProcess。
    timer = threading.Timer(0.25, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.daemon = True
    timer.start()


@router.get("")
async def list_worlds(_owner: Annotated[User, Depends(require_owner)]) -> dict:
    """列出可切换世界，并标记当前世界与包装器能力。"""
    return {
        "current": settings.world_name,
        "switching_supported": switching_supported(),
        "creation_supported": settings.world_managed,
        "items": [
            {
                "name": world.name,
                "current": world.name == settings.world_name,
                "created_at": world.created_at,
                "world_type": world.world_type, "type_version": world.type_version,
                "available": world.unavailable_reason is None, "unavailable_reason": world.unavailable_reason,
            }
            for world in manager.list_worlds()
        ],
    }


@router.post("", status_code=201)
async def create_world(
    payload: WorldCreateRequest,
    _owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """当前 Owner 创建空世界，不复制账号、不切换或停止当前任务。

    同名目录由排他 mkdir 拒绝；请求取消后等待磁盘操作完成，避免与切换交错。
    数据库表由新世界首次启动时的 Alembic 迁移创建。
    """
    if not settings.world_managed:
        raise HTTPException(409, 'WORLD_OPERATION_REQUIRES_MANAGED')
    async with runtime_manager.world_operation_lock:
        if runtime_manager.switch_target is not None:
            raise HTTPException(409, 'WORLD_OPERATION_IN_PROGRESS')
        try:
            world = await settled(asyncio.create_task(
                asyncio.to_thread(manager.create, payload.name, world_type=payload.world_type, type_version=payload.type_version), context=copy_context()))
        except FileExistsError:
            raise HTTPException(409, 'WORLD_ALREADY_EXISTS') from None
        except WorldTypeUnavailable:
            raise HTTPException(422, 'WORLD_TYPE_UNAVAILABLE') from None
        except ValueError:
            raise HTTPException(422, 'WORLD_NAME_INVALID') from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, 'WORLD_OPERATION_FAILED') from None
    return {"name": world.name, "current": False, "created_at": world.created_at,
        "world_type": world.world_type, "type_version": world.type_version, "available": True, "unavailable_reason": None}


@router.post("/switch", status_code=202)
async def switch_world(
    payload: WorldSwitchRequest,
    _owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """把目标写入包装器控制文件，并在响应后优雅终止当前进程。"""
    if not switching_supported():
        raise HTTPException(status_code=409, detail="WORLD_SWITCH_REQUIRES_WRAPPER")
    if payload.name == settings.world_name:
        raise HTTPException(status_code=409, detail="WORLD_ALREADY_ACTIVE")
    try:
        target = manager.get(payload.name)
        assert_world_compatible(target.database_path)
        if manager.is_active(payload.name):
            raise HTTPException(409, 'WORLD_ACTIVE')
    except WorldTypeUnavailable:
        raise HTTPException(409, 'WORLD_TYPE_UNAVAILABLE') from None
    except WorldRequiresNewerRoleplex:
        raise HTTPException(409, 'WORLD_REQUIRES_NEWER_ROLEPLEX') from None
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="WORLD_NOT_FOUND") from None
    async with runtime_manager.world_operation_lock:
        if runtime_manager.switch_target is not None:
            raise HTTPException(409, 'WORLD_OPERATION_IN_PROGRESS')
        async def perform_switch():
            """一次受保护交接：响应取消不能留下已写目标但永不退出的后端。"""
            written = False
            try:
                async with runtime_manager.cleanup_scope('world', 0, 'world_switch', _owner.id):
                    if manager.is_active(payload.name):
                        raise HTTPException(409, 'WORLD_ACTIVE')
                    assert_world_compatible(target.database_path)
                    try:
                        manager.request_switch(payload.name, settings.world_control_file)
                    except OSError:
                        raise RuntimeRejected('WORLD_OPERATION_FAILED') from None
                    written = True
                    runtime_manager.switch_target = payload.name
                    logger.info('world.switch_requested', extra={'source_world': settings.world_name, 'target_world': payload.name})
            except BaseException as exc:
                if written:
                    control = Path(settings.world_control_file)
                    # 只撤销本任务刚写的目标；不删除未知或被其他来源替换的文件。
                    if control.read_text(encoding='utf-8') == payload.name:
                        control.unlink()
                        runtime_manager.switch_target = None
                if isinstance(exc, OSError):
                    raise HTTPException(503, 'WORLD_OPERATION_FAILED') from None
                if isinstance(exc, WorldRequiresNewerRoleplex):
                    raise HTTPException(409, 'WORLD_REQUIRES_NEWER_ROLEPLEX') from None
                raise
            set_process_stop_reason('world_switch')
            schedule_shutdown()
        await settled(asyncio.create_task(perform_switch(), context=copy_context()))
    return {"target": payload.name, "restarting": True}
