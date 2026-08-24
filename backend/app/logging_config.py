"""Roleplex 的统一日志配置与跨异步任务关联上下文。

控制台日志用于人工排查，JSONL 文件用于精确检索。业务代码只记录标识符和
状态，不记录凭证、完整提示词或模型输出。
"""
from __future__ import annotations

import json
import logging
import logging.config
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_context: ContextVar[dict[str, Any]] = ContextVar("roleplex_log_context", default={})
_context_fields = (
    "request_id",
    "user_id",
    "conversation_id",
    "message_id",
    "generation_id",
    "chain_id",
    "execution_id",
    "parent_execution_id",
    "role_id",
    "tool_call_id",
    "ws_connection_id",
)
_sensitive_fragments = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey")
_standard_record_fields = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def current_log_context() -> dict[str, Any]:
    """返回当前异步执行上下文的浅拷贝。"""
    return dict(_context.get())


def current_request_id() -> str | None:
    """返回当前 HTTP/WS 链路的关联 ID。"""
    value = _context.get().get("request_id")
    return str(value) if value is not None else None


def set_log_context(**fields: Any) -> None:
    """向当前上下文补充非空关联字段。"""
    merged = current_log_context()
    merged.update({key: value for key, value in fields.items() if value is not None})
    _context.set(merged)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """在一个同步或异步调用范围内绑定日志关联字段。"""
    merged = current_log_context()
    merged.update({key: value for key, value in fields.items() if value is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in _sensitive_fragments)


def _redact_value(value: Any) -> Any:
    """递归移除嵌套结构中的敏感键，避免元数据字典绕过顶层过滤。"""
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items() if not _is_sensitive(str(key))}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _safe_fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = _redact_value(current_log_context())
    for key, value in record.__dict__.items():
        if key in _standard_record_fields or key.startswith("_") or _is_sensitive(key):
            continue
        fields[key] = _redact_value(value)
    return fields


class JsonFormatter(logging.Formatter):
    """将日志记录格式化为单行 JSON，并自动合并关联上下文。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        context = _safe_fields(record)
        for key in _context_fields:
            payload[key] = context.pop(key, None)
        payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ReadableFormatter(logging.Formatter):
    """生成带时间、事件名和关键字段的单行终端日志。"""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        fields = _safe_fields(record)
        # 控制字符在 JSONL 中天然转义；终端也要显式转义，避免用户名等字段伪造新日志行。
        suffix = " ".join(
            f"{key}={str(value).replace(chr(13), r'\r').replace(chr(10), r'\n')}"
            for key, value in fields.items()
            if value is not None
        )
        line = f"[{timestamp}] {record.levelname} {record.name} {record.getMessage()}"
        if suffix:
            line += f" {suffix}"
        if record.exc_info:
            line += f"\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(log_dir: str | Path, level: str = "INFO", max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5) -> Path:
    """接管应用和 Uvicorn 日志，并返回当前 JSONL 文件路径。"""
    target_dir = Path(log_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / "roleplex.jsonl"
    handlers = {
        "console": {"class": "logging.StreamHandler", "formatter": "readable", "stream": "ext://sys.stdout"},
        "jsonl": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": str(log_path),
            "encoding": "utf-8",
            "maxBytes": max_bytes,
            "backupCount": backup_count,
        },
    }
    uvicorn_loggers = {
        name: {"handlers": ["console", "jsonl"], "level": level, "propagate": False}
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"readable": {"()": ReadableFormatter}, "json": {"()": JsonFormatter}},
            "handlers": handlers,
            "root": {"handlers": ["console", "jsonl"], "level": level},
            "loggers": uvicorn_loggers,
        }
    )
    return log_path
