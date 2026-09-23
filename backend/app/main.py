from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter
import uuid

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ROOT_DIR, settings
from .db import close_db, init_db
from .realtime import events
from .realtime.events import EventHub
from .errors import http_error_handler, validation_error_handler
from .config.logging import (
    collect_git_metadata,
    configure_logging,
    current_logging_session,
    log_context,
    process_stop_reason,
)
from .config.log_archive import maintain_logs
from .routers import agent_budget, approvals, artifacts, auth, context_compaction, context_policy, conversation_context, conversations, memory, messages, model_configs, prompt_settings, roles, runtime, workspaces, worlds, workflows
from .runtime.manager import manager as runtime_manager
from .runtime.registry import RuntimeRejected
from .runtime.wrapper import watch_wrapper
from .services import retention
from .services import chat
from .scheduling import conversation_scheduler
from .realtime.websocket import router as ws_router
from .worlds import WorldManager
from .worlds.compatibility import assert_world_compatible

configure_logging(
    settings.log_dir,
    settings.log_level,
    settings.log_max_bytes,
    settings.log_run_kind,
    settings.log_max_seconds,
    world_name=settings.world_name,
)
http_logger = logging.getLogger("roleplex.http")
lifecycle_logger = logging.getLogger("roleplex.lifecycle")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """在应用生命周期内初始化事件广播器、数据库和会话调度器。

    数据库就绪后清理一次超过保留期的已删除会话：回收站没有常驻定时任务，
    启动是唯一的清理时机（理由见 `services/retention.py`）。
    """
    events.hub = EventHub()
    world_manager: WorldManager | None = None
    if settings.world_managed:
        world_manager = WorldManager(settings.worlds_dir)
        world = world_manager.ensure(settings.world_name)
        assert_world_compatible(world.database_path)
        world_manager.acquire(settings.world_name)
    process_status = "success"
    runtime_started = False
    close_wrapper_watch = lambda: None
    try:
        logging_session = current_logging_session()
        source = (
            collect_git_metadata(ROOT_DIR)
            if logging_session is not None and logging_session.run_kind != "unit"
            else {}
        )
        lifecycle_logger.info(
            "process.started",
            extra={
                "pid": os.getpid(),
                "backend_version": "0.1.0",
                **source,
            },
        )
        if settings.log_archive_enabled and settings.log_run_kind == "runtime":
            try:
                await asyncio.to_thread(
                    maintain_logs,
                    settings.log_dir,
                    retention_days=settings.log_retention_days,
                    max_total_bytes=settings.log_max_total_bytes,
                    compresslevel=settings.log_archive_compresslevel,
                )
            except Exception:
                lifecycle_logger.exception("log.retention_failed", extra={"stage": "startup"})
        lifecycle_logger.info(
            "world.starting", extra={"world_managed": settings.world_managed},
        )
        await init_db()
        await runtime_manager.initialize()
        from .workspaces.diffs import initialize_current_pool
        initialize_current_pool()
        runtime_started = True
        await conversation_scheduler.start(chat.run_scheduled_generation)
        from .context.compaction import recover as recover_compactions
        await recover_compactions()
        from .workflows import service as workflow_service
        await workflow_service.initialize()
        await retention.purge_expired_on_startup()
        close_wrapper_watch = watch_wrapper()
        yield
    except Exception:
        process_status = "failed"
        lifecycle_logger.exception("process.failed", extra={"status": "failed"})
        raise
    finally:
        close_wrapper_watch()
        from .workflows import service as workflow_service
        await workflow_service.shutdown()
        shutdown_error = None
        try:
            if runtime_started:
                await runtime_manager.shutdown()
        except Exception as exc:
            process_status, shutdown_error = 'failed', exc
        finally:
            try:
                await conversation_scheduler.shutdown()
                from .workspaces.resource_admission import current as resource_pool
                await resource_pool().close()
                from .workspaces.scan_admission import close_current_pool as close_scan_pool
                await close_scan_pool()
                from .workspaces.write_admission import close_current_pool as close_write_pool
                await close_write_pool()
                from .workspaces.diffs import close_current_pool
                await close_current_pool()
                if runtime_manager.stop_jobs:
                    await asyncio.gather(*list(runtime_manager.stop_jobs.values()), return_exceptions=True)
            except BaseException as exc:
                process_status = 'failed'
                shutdown_error = shutdown_error or exc
            finally:
                events.hub = None
                try:
                    await close_db()
                except BaseException as exc:
                    process_status = 'failed'
                    shutdown_error = shutdown_error or exc
                try:
                    if world_manager is not None:
                        world_manager.release(settings.world_name)
                except BaseException as exc:
                    process_status = 'failed'
                    shutdown_error = shutdown_error or exc
                lifecycle_logger.info('process.stopped', extra={'status': process_status, 'reason': process_stop_reason(),
                    **({'error_code': 'RUNTIME_CLEANUP_UNCONFIRMED'} if shutdown_error else {})})
        if shutdown_error:
            raise shutdown_error


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


async def runtime_error_handler(request, exc: RuntimeRejected):
    """将运行策略拒绝转换为既有错误信封，不记录原始上下文。

    Args:
        request：当前 HTTP 请求。
        exc：固定错误码。
    """
    status = 503 if exc.code == 'WORLD_OPERATION_FAILED' else 404 if exc.code == 'RUNTIME_NOT_FOUND' else 422 if exc.code in {'RUNTIME_SCOPE_INVALID', 'RUNTIME_LIMIT_INVALID', 'RUNTIME_ARGUMENT_INVALID'} else 409
    return await http_error_handler(request, StarletteHTTPException(status_code=status, detail=exc.code))


app.add_exception_handler(RuntimeRejected, runtime_error_handler)
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
        try:
            response = await call_next(request)
        except Exception:
            http_logger.exception(
                "http.failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "client": client,
                    "status": "failed",
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        content_length = response.headers.get("content-length")
        http_logger.info(
            "http.completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "route_template": getattr(route, "path", None),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "client": client,
                "response_bytes": int(content_length) if content_length and content_length.isdigit() else None,
            },
        )
        return response
app.include_router(auth.router)
app.include_router(model_configs.router)
app.include_router(roles.router)
app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(artifacts.router)
app.include_router(worlds.router)
app.include_router(workspaces.router)
app.include_router(approvals.router)
app.include_router(runtime.router)
app.include_router(agent_budget.router)
app.include_router(prompt_settings.router)
app.include_router(context_policy.router)
app.include_router(conversation_context.router)
app.include_router(memory.router)
app.include_router(context_compaction.router)
app.include_router(workflows.router)
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict[str, str | bool]:
    """报告进程可用性和当前事件 epoch。"""
    return {
        "status": "ok",
        "stream_epoch": events.current_epoch(),
        "world_name": settings.world_name,
        "world_managed": settings.world_managed,
    }
