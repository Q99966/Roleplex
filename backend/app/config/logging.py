"""Roleplex 日志 v2 核心：事件身份、分类、上下文和不可变轮转。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Any, Iterator, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo


LOG_SCHEMA_VERSION = 2
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_context: ContextVar[dict[str, Any]] = ContextVar("roleplex_log_context", default={})
_context_fields = frozenset({
    "request_id", "user_id", "conversation_id", "message_id", "generation_id",
    "chain_id", "execution_id", "parent_execution_id", "role_id", "tool_call_id",
    "ws_connection_id",
})
_sensitive_fragments = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "cookie")
_usage_token_fields = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_write_tokens",
    # ContextBuilder 的本地预算估算不是凭据，也不冒充 Provider usage；字段名必须显式登记才可落盘。
    "estimated_context_tokens", "input_budget_tokens", "safety_margin_tokens",
})
_standard_record_fields = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_ignored_record_fields = frozenset({"color_message", "websocket"})
_run_kinds = frozenset({"runtime", "unit", "e2e-fake", "e2e-real"})
_agent_event_prefixes = ("agent.", "generation.", "provider.", "tool.", "context.")
_active_file_handlers: list[logging.Handler] = []
_process_stop_reason = "normal"
_known_secret_values: tuple[str, ...] = ()
_sensitive_assignment = re.compile(
    r"(?i)(password|passwd|token|authorization|api[_-]?key|cookie)(\s*[=:]\s*)([^\s,;]+)"
)


class _Clock(Protocol):
    """日志时间接口，测试可注入确定性墙上时间。"""

    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(BEIJING_TZ)


def ensure_log_directory(path: str | Path) -> Path:
    """创建日志目录；POSIX 上仅收紧本次新建目录，不改写用户已有目录权限。

    Args:
        path：需要存在的日志目录。
    """
    target = Path(path)
    missing: list[Path] = []
    cursor = target
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o750, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o750)
    return target


def restrict_log_file(path: str | Path) -> None:
    """POSIX 上把新日志/归档收紧为 0640；Windows 继续依赖当前用户 ACL。

    Args:
        path：已经创建的日志或归档文件。
    """
    if os.name != "nt":
        Path(path).chmod(0o640)


def current_log_context() -> dict[str, Any]:
    """返回当前异步执行上下文的浅拷贝。"""
    return dict(_context.get())


def current_request_id() -> str | None:
    """返回当前 HTTP/WS 链路的关联 ID。"""
    value = _context.get().get("request_id")
    return str(value) if value is not None else None


def set_log_context(**fields: Any) -> None:
    """向当前上下文补充非空关联字段。

    Args:
        **fields：当前链路允许传播的关联字段。
    """
    merged = current_log_context()
    merged.update({key: value for key, value in fields.items() if value is not None})
    _context.set(merged)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """在一个同步或异步调用范围内绑定日志关联字段。

    Args:
        **fields：仅在上下文管理器范围内生效的关联字段。
    """
    merged = current_log_context()
    merged.update({key: value for key, value in fields.items() if value is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def _is_sensitive(key: str) -> bool:
    """判断字段名是否属于禁止持久化的凭据类别。

    Args:
        key：待检查的日志字段名。
    """
    normalized = key.lower().replace("-", "_")
    if normalized in _usage_token_fields:
        return False
    return any(fragment in normalized for fragment in _sensitive_fragments)


def _redact_value(value: Any) -> Any:
    """递归移除嵌套结构中的敏感键；业务层仍须坚持允许字段优先。

    Args:
        value：可能包含嵌套结构或凭据文本的字段值。
    """
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items() if not _is_sensitive(str(key))}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        text = _sensitive_assignment.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", value)
        for secret in _known_secret_values:
            text = text.replace(secret, "<redacted>")
        return text
    return value


def _safe_fields(record: logging.LogRecord) -> dict[str, Any]:
    """合并上下文与附加字段，并在格式化前完成递归脱敏。

    Args:
        record：当前 Python 日志记录。
    """
    fields = _redact_value(current_log_context())
    for key, value in record.__dict__.items():
        if key in _standard_record_fields or key in _ignored_record_fields or key.startswith("_") or _is_sensitive(key):
            continue
        if value is not None:
            fields[key] = _redact_value(value)
    return fields


def _category_for(record: logging.LogRecord) -> str:
    """按稳定 logger/event 约定选择 app、agent 或 access。

    Args:
        record：已进入统一日志入口的记录。
    """
    event = record.getMessage()
    if record.name in {"roleplex.http", "uvicorn.access"} or event.startswith("http."):
        return "access"
    if record.name.startswith(("roleplex.agent", "roleplex.chat")) or event.startswith(_agent_event_prefixes):
        return "agent"
    return "app"


class EventMetadataFilter(logging.Filter):
    """在所有 handler 之前给事件分配一次身份、进程序号和进程上下文。"""

    def __init__(
        self,
        *,
        run_kind: str,
        world_name: str,
        process_instance_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__()
        self.run_kind = run_kind
        self.world_name = world_name
        self.process_instance_id = process_instance_id or secrets.token_hex(4)
        self.run_id = run_id
        self._sequence = 0
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "process_seq"):
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
            if not hasattr(record, "event_id"):
                record.event_id = f"evt_{uuid4().hex}"
            record.process_seq = sequence
            record.process_instance_id = self.process_instance_id
            record.run_kind = self.run_kind
            record.world_name = self.world_name
            record.category = _category_for(record)
            if self.run_id:
                record.run_id = self.run_id
        return True


class JsonFormatter(logging.Formatter):
    """把 LogRecord 转换为日志 v2 单行 JSON。"""

    _mandatory = (
        "event_id", "category", "run_kind", "process_instance_id", "process_seq", "world_name",
    )

    def format(self, record: logging.LogRecord) -> str:
        fields = _safe_fields(record)
        payload: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": datetime.fromtimestamp(record.created, BEIJING_TZ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": _redact_value(record.getMessage()),
        }
        for key in self._mandatory:
            value = fields.pop(key, getattr(record, key, None))
            if value is not None:
                payload[key] = value
        run_id = fields.pop("run_id", getattr(record, "run_id", None))
        if run_id is not None:
            payload["run_id"] = run_id
        for key in _context_fields:
            value = fields.pop(key, None)
            if value is not None:
                payload[key] = value
        payload.update(fields)
        if record.exc_info:
            error = record.exc_info[1]
            if error:
                payload.setdefault("error_type", type(error).__name__)
                payload.setdefault("exception_message", _redact_value(str(error)))
            payload["traceback"] = _redact_value(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


class ReadableFormatter(logging.Formatter):
    """生成带本地时间和安全附加字段的终端日志。"""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, BEIJING_TZ).isoformat(timespec="milliseconds")
        fields = _safe_fields(record)
        suffix = " ".join(
            f"{key}={str(value).replace(chr(13), r'\r').replace(chr(10), r'\n')}"
            for key, value in fields.items()
            if key not in {"event_id", "process_seq"} and value is not None
        )
        line = f"[{timestamp}] {record.levelname} {record.name} {_redact_value(record.getMessage())}"
        if suffix:
            line += f" {suffix}"
        if record.exc_info:
            line += f"\n{_redact_value(self.formatException(record.exc_info))}"
        return line


def _first_timestamp(path: Path) -> datetime | None:
    """读取 active 片段首行时间，用于跨进程恢复一小时边界。

    Args:
        path：当前 active JSONL 路径。
    """
    try:
        with path.open("r", encoding="utf-8") as stream:
            line = stream.readline()
        value = json.loads(line).get("timestamp")
        return datetime.fromisoformat(value) if value else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _repair_jsonl_tail(path: Path) -> int:
    """截断 active JSONL 的不完整末行并返回移除字节数。

    Args:
        path：允许修补的 active JSONL，关闭片段不得传入。
    """
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    if not lines:
        return 0
    last = lines[-1]
    try:
        json.loads(last.decode("utf-8").rstrip("\r\n"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        kept = b"".join(lines[:-1])
        removed = len(data) - len(kept)
        with path.open("r+b") as stream:
            stream.truncate(len(kept))
        return removed
    if not data.endswith(b"\n"):
        with path.open("ab") as stream:
            stream.write(b"\n")
    return 0


class _AscendingRotatingFileHandler(BaseRotatingHandler):
    """基础名为 active，关闭片段按 001、002 递增且永不改写。"""

    def __init__(
        self,
        root: str | Path,
        stem: str,
        *,
        max_bytes: int,
        max_seconds: int,
        clock: _Clock | None = None,
        dated: bool,
    ) -> None:
        self.root = Path(root)
        self.stem = stem
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.clock = clock or _SystemClock()
        self.dated = dated
        now = self.clock.now()
        self.current_date = now.date()
        path = self._active_path(now)
        ensure_log_directory(path.parent)
        self.recovered_bytes = _repair_jsonl_tail(path)
        self.segment_started_at = _first_timestamp(path) or now
        if path.exists() and path.stat().st_size and self._expired(now, path):
            self._rotate_path(path)
            self.segment_started_at = now
        super().__init__(str(path), mode="a", encoding="utf-8", delay=False)
        restrict_log_file(path)

    def _directory(self, now: datetime) -> Path:
        return self.root / now.strftime("%Y-%m-%d") if self.dated else self.root

    def _active_path(self, now: datetime) -> Path:
        return self._directory(now) / f"{self.stem}.jsonl"

    def _next_closed_path(self, active: Path) -> Path:
        existing = []
        pattern = re.compile(rf"^{re.escape(self.stem)}\.(\d{{3}})\.jsonl$")
        for candidate in active.parent.glob(f"{self.stem}.*.jsonl"):
            match = pattern.match(candidate.name)
            if match:
                existing.append(int(match.group(1)))
        return active.parent / f"{self.stem}.{(max(existing, default=0) + 1):03d}.jsonl"

    def _rotate_path(self, active: Path) -> None:
        if active.is_file() and active.stat().st_size:
            active.replace(self._next_closed_path(active))

    def _expired(self, now: datetime, active: Path) -> bool:
        return (
            (now - self.segment_started_at).total_seconds() >= self.max_seconds
            or active.stat().st_size >= self.max_bytes
        )

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        now = self.clock.now()
        if self.dated and now.date() != self.current_date:
            return True
        if (now - self.segment_started_at).total_seconds() >= self.max_seconds:
            return True
        current_size = os.path.getsize(self.baseFilename)
        encoded = f"{self.format(record)}{self.terminator}".encode(self.encoding or "utf-8")
        return bool(current_size and current_size + len(encoded) > self.max_bytes)

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        old_active = Path(self.baseFilename)
        self._rotate_path(old_active)
        now = self.clock.now()
        new_active = self._active_path(now)
        ensure_log_directory(new_active.parent)
        self.baseFilename = os.path.abspath(new_active)
        self.current_date = now.date()
        self.segment_started_at = now
        self.stream = self._open()
        restrict_log_file(new_active)


class DailyCategoryFileHandler(_AscendingRotatingFileHandler):
    """runtime 按本地日期和类别维护 active/递增关闭片段。"""

    def __init__(
        self,
        root: str | Path,
        category: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        max_seconds: int = 3600,
        clock: _Clock | None = None,
    ) -> None:
        super().__init__(
            root, category, max_bytes=max_bytes, max_seconds=max_seconds, clock=clock, dated=True,
        )


class RunFileHandler(_AscendingRotatingFileHandler):
    """单轮 E2E 在固定 run 目录内维护事件或错误文件。"""

    def __init__(self, directory: Path, stem: str, *, max_bytes: int, max_seconds: int, clock: _Clock) -> None:
        super().__init__(
            directory, stem, max_bytes=max_bytes, max_seconds=max_seconds, clock=clock, dated=False,
        )


class _CategoryFilter(logging.Filter):
    def __init__(self, category: str, *, exclude_uvicorn_access: bool = False) -> None:
        super().__init__()
        self.category = category
        self.exclude_uvicorn_access = exclude_uvicorn_access

    def filter(self, record: logging.LogRecord) -> bool:
        if self.exclude_uvicorn_access and record.name == "uvicorn.access":
            return False
        return getattr(record, "category", None) == self.category


class _ErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR or bool(record.exc_info)


class _ProjectEventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("roleplex")


class _ExcludeUvicornAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "uvicorn.access"


def _parse_run_started(value: str | datetime | None, clock: _Clock) -> datetime:
    """解析共享测试开始时间，缺失时使用注入时钟。

    Args:
        value：环境变量文本、已解析时间或空值。
        clock：缺省时间来源。
    """
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(value)
    return clock.now()


def _validate_run_id(value: str | None) -> str:
    """生成或校验固定八位小写十六进制 run ID。

    Args:
        value：外部传入的可选 run ID。
    """
    candidate = value or secrets.token_hex(4)
    if not re.fullmatch(r"[0-9a-f]{8}", candidate):
        raise ValueError("LOG_RUN_ID 必须是 8 位小写十六进制")
    return candidate


def _attach(handler: logging.Handler, metadata: EventMetadataFilter, *filters: logging.Filter) -> None:
    """按“先分配事件身份，再做筛选”的固定顺序安装 filters。

    Args:
        handler：目标终端或持久化 handler。
        metadata：本进程共享的事件元数据过滤器。
        *filters：元数据之后执行的分类/错误过滤器。
    """
    handler.addFilter(metadata)
    for item in filters:
        handler.addFilter(item)


@dataclass(frozen=True)
class LoggingSession:
    """当前日志进程的身份和持久化位置。"""

    process_instance_id: str
    run_kind: str
    run_id: str | None
    directory: Path | None


@dataclass(frozen=True)
class JsonlReadResult:
    """JSONL 读取结果；尾部截断是可报告事实，不等于中间损坏。"""

    records: list[dict[str, Any]]
    truncated_tail: bool
    discarded_bytes: int


class LogCorruptionError(ValueError):
    """JSONL 中间行损坏，消费者不得静默跳过。"""


def read_jsonl(path: str | Path) -> JsonlReadResult:
    """读取 JSONL，仅容忍最后一行不完整并报告丢弃字节数。

    Args:
        path：待读取的 JSONL 文件。

    Raises:
        LogCorruptionError：非末行存在损坏记录。
    """
    source = Path(path)
    lines = source.read_bytes().splitlines(keepends=True)
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            records.append(json.loads(line.decode("utf-8").rstrip("\r\n")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if index == len(lines):
                return JsonlReadResult(records, True, len(line))
            raise LogCorruptionError(f"日志文件 {source} 第 {index} 行损坏") from exc
    return JsonlReadResult(records, False, 0)


_session: LoggingSession | None = None


def current_logging_session() -> LoggingSession | None:
    return _session


def set_process_stop_reason(reason: str) -> None:
    """记录进程正常退出原因，供生命周期终态使用。

    Args:
        reason：已登记的退出原因，如正常停止或世界切换。
    """
    global _process_stop_reason
    _process_stop_reason = reason


def process_stop_reason() -> str:
    return _process_stop_reason


def shutdown_logging() -> None:
    """关闭并移除本模块安装的 handlers，测试重配时避免文件句柄泄漏。"""
    global _active_file_handlers
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    _active_file_handlers = []


def flush_persistent_logs() -> bool:
    """刷新全部持久化 handler；没有文件或任一刷新失败时返回 false。"""
    if not _active_file_handlers:
        return False
    try:
        for handler in _active_file_handlers:
            handler.flush()
        return True
    except Exception:
        return False


def configure_logging(
    log_dir: str | Path,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    run_kind: str = "runtime",
    max_seconds: int = 3600,
    *,
    world_name: str = "default",
    run_id: str | None = None,
    run_started_at: str | datetime | None = None,
    process_instance_id: str | None = None,
    clock: _Clock | None = None,
) -> LoggingSession:
    """按日志 v2 为 runtime/unit/E2E 配置终端与结构化 handlers。

    Args:
        log_dir：日志树根目录。
        level：Python 日志级别。
        max_bytes：单个活跃片段的最大字节数。
        run_kind：runtime、unit 或 E2E 分类。
        max_seconds：单个活跃片段允许的最长时间跨度。
        world_name：当前世界展示名。
        run_id：E2E 共享运行标识；runtime/unit 不使用。
        run_started_at：测试轮次开始时间，用于固定 E2E 目录。
        process_instance_id：可选进程标识，主要供确定性测试注入。
        clock：可选墙上时钟，主要供轮转测试注入。
    """
    global _session, _active_file_handlers, _known_secret_values, _process_stop_reason
    if run_kind not in _run_kinds:
        raise ValueError(f"未知日志运行类别：{run_kind}")
    shutdown_logging()
    _process_stop_reason = "normal"
    _known_secret_values = tuple(
        value for key, value in os.environ.items()
        if len(value) >= 4 and any(part in key.lower() for part in ("password", "token", "secret", "key"))
    )
    clock = clock or _SystemClock()
    process_id = process_instance_id or secrets.token_hex(4)
    effective_run_id = None
    started = _parse_run_started(run_started_at or os.environ.get("LOG_RUN_STARTED_AT"), clock)
    if run_kind.startswith("e2e-"):
        effective_run_id = _validate_run_id(run_id or os.environ.get("LOG_RUN_ID"))
    metadata = EventMetadataFilter(
        run_kind=run_kind,
        world_name=world_name,
        process_instance_id=process_id,
        run_id=effective_run_id,
    )
    json_formatter = JsonFormatter()
    console = logging.StreamHandler()
    console.setFormatter(ReadableFormatter())
    console.setLevel(level)
    _attach(console, metadata, _ExcludeUvicornAccessFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [console]
    handlers: list[logging.Handler] = []
    directory: Path | None = None

    if run_kind == "runtime":
        runtime_root = Path(log_dir) / "runtime"
        directory = runtime_root / started.strftime("%Y-%m-%d")
        for category in ("app", "agent", "access"):
            handler = DailyCategoryFileHandler(
                runtime_root, category, max_bytes=max_bytes, max_seconds=max_seconds, clock=clock,
            )
            handler.setFormatter(json_formatter)
            _attach(
                handler,
                metadata,
                _ProjectEventFilter(),
                _CategoryFilter(category, exclude_uvicorn_access=category == "access"),
            )
            handlers.append(handler)
        errors = DailyCategoryFileHandler(
            runtime_root, "errors", max_bytes=max_bytes, max_seconds=max_seconds, clock=clock,
        )
        errors.setFormatter(json_formatter)
        _attach(errors, metadata, _ProjectEventFilter(), _ErrorFilter())
        handlers.append(errors)
    elif run_kind.startswith("e2e-"):
        mode = "fake" if run_kind == "e2e-fake" else "real"
        directory = (
            Path(log_dir) / "tests" / "e2e" / mode / started.strftime("%Y-%m-%d")
            / f"{started.strftime('%H-%M-%S')}_{effective_run_id}"
        )
        for stem, extra_filter in (("events", _ExcludeUvicornAccessFilter()), ("errors", _ErrorFilter())):
            handler = RunFileHandler(directory, stem, max_bytes=max_bytes, max_seconds=max_seconds, clock=clock)
            handler.setFormatter(json_formatter)
            _attach(handler, metadata, _ProjectEventFilter(), extra_filter)
            handlers.append(handler)

    for handler in handlers:
        handler.setLevel(level)
        root.addHandler(handler)
    _active_file_handlers = handlers

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(level)
        logger.propagate = True
    # 第三方 HTTP 客户端 INFO 可能包含完整 URL/query；provider 元数据由项目事件单独记录。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _session = LoggingSession(process_id, run_kind, effective_run_id, directory)
    for handler in handlers:
        recovered = getattr(handler, "recovered_bytes", 0)
        if recovered:
            logging.getLogger("roleplex.logging").warning(
                "log.tail_recovered",
                extra={
                    "recovered_bytes": recovered,
                    "log_file": str(Path(handler.baseFilename).relative_to(Path(log_dir))),
                },
            )
    return _session


def collect_git_metadata(repository_root: str | Path) -> dict[str, Any]:
    """收集进程/测试级 Git 元数据；缺少 Git 时安全降级。

    Args:
        repository_root：用于读取 HEAD 和工作区状态的仓库根目录。
    """
    root = Path(repository_root)
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL,
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "-z"], cwd=root, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return {"git_available": False}
    dirty = bool(status)
    result: dict[str, Any] = {"git_available": True, "git_head": head, "git_dirty": dirty}
    if not dirty:
        return result
    digest = hashlib.sha256()
    digest.update(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=root))
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
    ).split(b"\0")
    for raw in sorted(item for item in untracked if item):
        digest.update(b"\0path\0" + raw + b"\0")
        path = root / os.fsdecode(raw)
        if path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    result["working_tree_hash"] = digest.hexdigest()[:8]
    return result
