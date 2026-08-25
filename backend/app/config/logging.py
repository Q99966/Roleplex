"""Roleplex 配置层的统一日志与跨异步任务关联上下文。

控制台日志用于人工排查，JSONL 文件用于精确检索。业务代码只记录标识符和
状态，不记录凭证、完整提示词或模型输出。
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Any, Iterator, Protocol


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
_usage_token_fields = frozenset({"input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens"})
_standard_record_fields = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_ignored_record_fields = frozenset({"color_message"})
_run_kinds = frozenset({"runtime", "unit", "e2e-fake", "e2e-real"})


class _Clock(Protocol):
    """日志分段使用的时钟接口，测试可注入确定性时间。"""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class _SystemClock:
    """生产环境使用本地墙上时间命名，并用单调时钟判断持续时间。"""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def monotonic(self) -> float:
        return time.monotonic()


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
    # token 用量是观测指标而非凭据；只对白名单字段放行，其他 token 键仍按敏感数据过滤。
    if normalized in _usage_token_fields:
        return False
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
        if key in _standard_record_fields or key in _ignored_record_fields or key.startswith("_") or _is_sensitive(key):
            continue
        fields[key] = _redact_value(value)
    return fields


class JsonFormatter(logging.Formatter):
    """将日志记录格式化为单行 JSON，并自动合并关联上下文。"""

    def __init__(self, run_kind: str | None = None) -> None:
        super().__init__()
        self.run_kind = run_kind

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "run_kind": self.run_kind,
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

    def __init__(self, run_kind: str | None = None) -> None:
        super().__init__()
        self.run_kind = run_kind

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")
        fields = _safe_fields(record)
        if self.run_kind:
            fields = {"run_kind": self.run_kind, **fields}
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


class SessionRotatingFileHandler(BaseRotatingHandler):
    """按运行类别、日期、进程启动时间保存，并按大小或持续时间切片。"""

    def __init__(
        self,
        log_dir: str | Path,
        run_kind: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        max_seconds: int = 3600,
        encoding: str = "utf-8",
        clock: _Clock | None = None,
    ) -> None:
        if run_kind not in _run_kinds:
            raise ValueError(f"未知日志运行类别：{run_kind}")
        if max_bytes <= 0:
            raise ValueError("日志文件大小上限必须为正数")
        if not 0 < max_seconds <= 3600:
            raise ValueError("日志文件时间跨度必须在 1 到 3600 秒之间")
        self.log_dir = Path(log_dir)
        self.run_kind = run_kind
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.clock = clock or _SystemClock()
        self.segment_started_at = self.clock.monotonic()
        wall_started_at = self.clock.now()
        self.segment_date = wall_started_at.date()
        filename = self._reserve_path(wall_started_at)
        super().__init__(str(filename), mode="a", encoding=encoding, delay=False)

    def _reserve_path(self, started_at: datetime) -> Path:
        """原子占用一个分段文件名；同秒冲突时递增后缀，绝不续写旧文件。"""
        day_dir = self.log_dir / started_at.strftime("%Y%m%d") / self.run_kind
        day_dir.mkdir(parents=True, exist_ok=True)
        stem = started_at.strftime("%Y%m%d%H%M%S")
        for index in range(10_000):
            suffix = "" if index == 0 else f"-{index:02d}"
            candidate = day_dir / f"{stem}{suffix}.jsonl"
            try:
                descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
            except FileExistsError:
                continue
            os.close(descriptor)
            return candidate
        raise RuntimeError(f"同一秒内日志分段数量过多：{stem}")

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """写入前检查一小时跨度和文件大小，任一达到上限即切片。"""
        if self.clock.now().date() != self.segment_date:
            return True
        if self.clock.monotonic() - self.segment_started_at >= self.max_seconds:
            return True
        current_size = os.path.getsize(self.baseFilename)
        if current_size == 0:
            return False
        encoded = f"{self.format(record)}{self.terminator}".encode(self.encoding or "utf-8")
        return current_size + len(encoded) > self.max_bytes

    def doRollover(self) -> None:
        """关闭当前文件并以新片段起始时间在对应日期目录中创建文件。"""
        if self.stream:
            self.stream.close()
            self.stream = None
        started_at = self.clock.now()
        self.baseFilename = os.path.abspath(self._reserve_path(started_at))
        self.segment_started_at = self.clock.monotonic()
        self.segment_date = started_at.date()
        if not self.delay:
            self.stream = self._open()


def configure_logging(
    log_dir: str | Path,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    run_kind: str = "runtime",
    max_seconds: int = 3600,
) -> Path:
    """接管应用和 Uvicorn 日志，并返回本次进程的首个 JSONL 文件路径。"""
    session_handler = SessionRotatingFileHandler(
        log_dir,
        run_kind,
        max_bytes=max_bytes,
        max_seconds=max_seconds,
    )
    initial_path = Path(session_handler.baseFilename)
    handlers = {
        "console": {"class": "logging.StreamHandler", "formatter": "readable", "stream": "ext://sys.stdout"},
        "jsonl": {
            "()": lambda: session_handler,
            "formatter": "json",
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
            "formatters": {
                "readable": {"()": ReadableFormatter, "run_kind": run_kind},
                "json": {"()": JsonFormatter, "run_kind": run_kind},
            },
            "handlers": handlers,
            "root": {"handlers": ["console", "jsonl"], "level": level},
            "loggers": uvicorn_loggers,
        }
    )
    return initial_path
