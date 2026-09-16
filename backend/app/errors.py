from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config.logging import current_request_id


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """将请求校验失败映射为稳定错误信封，只返回字段位置与原因。

    不回显原始 input/异常 ctx：坐标指数溢出等非有限值不能让 422 序列化变成 500。
    """
    details = [{key: error[key] for key in ('type', 'loc', 'msg') if key in error} for error in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "请求参数无效", "details": details, "request_id": current_request_id()}},
    )


async def http_error_handler(_request: Request, exc: Any) -> JSONResponse:
    """将 HTTP 失败映射为机器可读错误码，同时不暴露资源是否存在。

    `detail` 为字符串时直接作为稳定错误码；为字典时按
    `{"code": ..., "details": [...]}` 解析，供密码策略等需要逐条回显原因的
    场景使用，错误信封结构保持不变。
    """
    detail = getattr(exc, "detail", "REQUEST_FAILED")
    status_code = getattr(exc, "status_code", 500)
    details: Any = None
    if isinstance(detail, str):
        code = detail
    elif isinstance(detail, dict) and isinstance(detail.get("code"), str):
        code = detail["code"]
        details = detail.get("details")
    else:
        code = "REQUEST_FAILED"
    body: dict[str, Any] = {"code": code, "message": code, "request_id": current_request_id()}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body})
