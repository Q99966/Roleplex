"""Owner 世界列表与受控重启切换接口。"""
from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..config.logging import set_process_stop_reason
from ..models import User
from ..security.tokens import require_owner
from ..worlds import WorldManager

logger = logging.getLogger("roleplex.worlds")
router = APIRouter(prefix="/api/worlds", tags=["worlds"])
manager = WorldManager(settings.worlds_dir)


class WorldSwitchRequest(BaseModel):
    """世界切换请求。"""

    name: str = Field(min_length=1, max_length=64)


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
        "items": [
            {
                "name": world.name,
                "current": world.name == settings.world_name,
                "created_at": world.created_at,
            }
            for world in manager.list_worlds()
        ],
    }


@router.post("/switch", status_code=202)
async def switch_world(
    payload: WorldSwitchRequest,
    background_tasks: BackgroundTasks,
    _owner: Annotated[User, Depends(require_owner)],
) -> dict:
    """把目标写入包装器控制文件，并在响应后优雅终止当前进程。"""
    if not switching_supported():
        raise HTTPException(status_code=409, detail="WORLD_SWITCH_REQUIRES_WRAPPER")
    if payload.name == settings.world_name:
        raise HTTPException(status_code=409, detail="WORLD_ALREADY_ACTIVE")
    try:
        manager.request_switch(payload.name, settings.world_control_file)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="WORLD_NOT_FOUND") from None
    logger.info("world.switch_requested", extra={"source_world": settings.world_name, "target_world": payload.name})
    set_process_stop_reason("world_switch")
    background_tasks.add_task(schedule_shutdown)
    return {"target": payload.name, "restarting": True}
