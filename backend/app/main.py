from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter
import uuid

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .db import close_db, init_db
from .realtime import events
from .realtime.events import EventHub
from .errors import http_error_handler, validation_error_handler
from .config.logging import configure_logging, log_context
from .routers import artifacts, auth, conversations, messages, model_configs, roles
from .services import retention
from .realtime.websocket import router as ws_router

configure_logging(
    settings.log_dir,
    settings.log_level,
    settings.log_max_bytes,
    settings.log_run_kind,
    settings.log_max_seconds,
)
logger = logging.getLogger("roleplex.http")


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
    max_age=600,
)


@app.middleware("http")
async def request_logging(request, call_next):
    """为 HTTP 请求建立关联上下文，并记录状态码与端到端耗时。"""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started = perf_counter()
    client = f"{request.client.host}:{request.client.port}" if request.client else None
    with log_context(request_id=request_id):
        logger.info("http.request_started", extra={"method": request.method, "path": request.url.path, "client": client})
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http.request_failed",
                extra={"method": request.method, "path": request.url.path, "duration_ms": round((perf_counter() - started) * 1000, 2)},
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http.request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response
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
