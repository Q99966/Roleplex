from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .db import close_db, init_db
from .events import EventHub
from . import events
from .errors import http_error_handler, validation_error_handler
from .routers import artifacts, auth, conversations, messages, model_configs, roles
from .services import retention
from .ws import router as ws_router

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """在应用生命周期内初始化进程事件广播器和数据库资源。

    数据库就绪后清理一次超过保留期的已删除会话：回收站没有常驻定时任务，
    启动是唯一的清理时机（理由见 `services/retention.py`）。
    """
    events.hub = EventHub()
    await init_db()
    await retention.purge_expired_on_startup()
    yield
    events.hub = None
    await close_db()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(model_configs.router)
app.include_router(roles.router)
app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(artifacts.router)
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """报告进程可用性和当前事件 epoch。"""
    return {"status": "ok", "stream_epoch": events.current_epoch()}
