from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """将请求校验失败映射为稳定的公开错误信封。"""
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "请求参数无效", "details": exc.errors()}})


async def http_error_handler(_request: Request, exc: Any) -> JSONResponse:
    """将 HTTP 失败映射为机器可读错误码，同时不暴露资源是否存在。"""
    detail = getattr(exc, "detail", "REQUEST_FAILED")
    code = detail if isinstance(detail, str) else "REQUEST_FAILED"
    status_code = getattr(exc, "status_code", 500)
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": code, "request_id": uuid.uuid4().hex}})
