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
from .routers import auth, conversations, model_configs, roles

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize process-local event state and database resources for the app lifetime."""
    events.hub = EventHub(settings.event_buffer_size)
    await init_db()
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


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Report process readiness and the current in-memory event epoch."""
    return {"status": "ok", "stream_epoch": events.hub.stream_epoch if events.hub else "starting"}
