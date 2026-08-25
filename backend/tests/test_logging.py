"""结构化日志、请求关联与认证审计测试。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


class _Clock:
    """为日志轮转测试提供可控的墙上时间和单调时钟。"""

    def __init__(self) -> None:
        self.current = datetime(2026, 8, 24, 12, 34, 56, tzinfo=timezone(timedelta(hours=8)))
        self.elapsed = 0.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)
        self.elapsed += seconds


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("roleplex.test.rotation", logging.INFO, __file__, 1, message, (), None)


def test_json_formatter_includes_context_and_safe_extra_fields():
    """JSONL 日志保留链路与业务字段，同时剔除敏感键。"""
    from app.config.logging import JsonFormatter, log_context

    formatter = JsonFormatter(run_kind="e2e-real")
    logger = logging.getLogger("roleplex.test.logging")
    with log_context(
        request_id="req-test-1",
        conversation_id=7,
        chain_id="chain-test-1",
        execution_id="run-test-1",
    ):
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            "test.event",
            (),
            None,
            extra={
                "duration_ms": 12.5,
                "message_id": 9,
                "password": "never-log-this",
                "token": "credential-token-must-disappear",
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "cache_hit_tokens": 80,
                "color_message": "internal-colored-template",
                "metadata": {
                    "safe": "visible",
                    "authorization": "Bearer nested-secret",
                    "api_key": "nested-api-key",
                },
            },
        )
        payload = json.loads(formatter.format(record))

    assert payload["event"] == "test.event"
    assert payload["run_kind"] == "e2e-real"
    assert payload["request_id"] == "req-test-1"
    assert payload["conversation_id"] == 7
    assert payload["chain_id"] == "chain-test-1"
    assert payload["execution_id"] == "run-test-1"
    assert payload["duration_ms"] == 12.5
    assert payload["message_id"] == 9
    assert payload["input_tokens"] == 120
    assert payload["output_tokens"] == 30
    assert payload["total_tokens"] == 150
    assert payload["cache_hit_tokens"] == 80
    assert payload["metadata"] == {"safe": "visible"}
    assert "password" not in payload
    assert "never-log-this" not in formatter.format(record)
    assert "credential-token-must-disappear" not in formatter.format(record)
    assert "internal-colored-template" not in formatter.format(record)
    assert "nested-secret" not in formatter.format(record)
    assert "nested-api-key" not in formatter.format(record)


def test_each_start_creates_a_timestamped_file_in_run_kind_folder(tmp_path: Path):
    """每次进程启动都新建文件，即使两个进程恰好在同一秒启动也不续写旧文件。"""
    from app.config.logging import JsonFormatter, SessionRotatingFileHandler

    clock = _Clock()
    first = SessionRotatingFileHandler(tmp_path, "unit", clock=clock)
    first.setFormatter(JsonFormatter())
    first.emit(_record("first.start"))
    first_path = Path(first.baseFilename)
    first.close()

    second = SessionRotatingFileHandler(tmp_path, "unit", clock=clock)
    second.setFormatter(JsonFormatter())
    second.emit(_record("second.start"))
    second_path = Path(second.baseFilename)
    second.close()

    assert first_path.relative_to(tmp_path).as_posix() == "20260824/unit/20260824123456.jsonl"
    assert second_path.relative_to(tmp_path).as_posix() == "20260824/unit/20260824123456-01.jsonl"
    assert "first.start" in first_path.read_text(encoding="utf-8")
    assert "second.start" not in first_path.read_text(encoding="utf-8")

    real = SessionRotatingFileHandler(tmp_path, "e2e-real", clock=clock)
    real.close()
    assert Path(real.baseFilename).relative_to(tmp_path).as_posix() == "20260824/e2e-real/20260824123456.jsonl"


def test_log_segment_rotates_on_hour_or_size_limit(tmp_path: Path):
    """时间和大小任一条件先达到，都必须在写入下一条记录前切换文件。"""
    from app.config.logging import JsonFormatter, SessionRotatingFileHandler

    time_clock = _Clock()
    time_handler = SessionRotatingFileHandler(tmp_path / "time", "e2e-fake", max_seconds=3600, clock=time_clock)
    time_handler.setFormatter(JsonFormatter())
    time_handler.emit(_record("before.hour"))
    time_clock.advance(3600)
    time_handler.emit(_record("after.hour"))
    time_handler.close()

    time_files = sorted((tmp_path / "time" / "20260824" / "e2e-fake").glob("*.jsonl"))
    assert [path.name for path in time_files] == ["20260824123456.jsonl", "20260824133456.jsonl"]
    assert "after.hour" not in time_files[0].read_text(encoding="utf-8")
    assert "after.hour" in time_files[1].read_text(encoding="utf-8")

    size_clock = _Clock()
    size_handler = SessionRotatingFileHandler(tmp_path / "size", "runtime", max_bytes=512, clock=size_clock)
    size_handler.setFormatter(JsonFormatter())
    size_handler.emit(_record("x" * 300))
    size_clock.advance(1)
    size_handler.emit(_record("y" * 300))
    size_handler.close()

    size_files = sorted((tmp_path / "size" / "20260824" / "runtime").glob("*.jsonl"))
    assert [path.name for path in size_files] == ["20260824123456.jsonl", "20260824123457.jsonl"]


def test_log_segment_moves_to_new_day_folder_at_midnight(tmp_path: Path):
    """跨过本地午夜时立即切片，次日记录不能留在前一天目录。"""
    from app.config.logging import JsonFormatter, SessionRotatingFileHandler

    clock = _Clock()
    clock.current = datetime(2026, 8, 24, 23, 59, 30, tzinfo=timezone(timedelta(hours=8)))
    handler = SessionRotatingFileHandler(tmp_path, "runtime", clock=clock)
    handler.setFormatter(JsonFormatter())
    handler.emit(_record("before.midnight"))
    clock.advance(60)
    handler.emit(_record("after.midnight"))
    handler.close()

    assert (tmp_path / "20260824" / "runtime" / "20260824235930.jsonl").exists()
    next_day = tmp_path / "20260825" / "runtime" / "20260825000030.jsonl"
    assert next_day.exists()
    assert "after.midnight" in next_day.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_failed_login_uses_one_request_id_and_records_username():
    """登录失败响应与审计日志共享 request ID，并记录账号但不记录口令。"""
    from app.main import app

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        """仅供本用例收集认证日志记录。"""

        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    auth_logger = logging.getLogger("roleplex.auth")
    auth_logger.addHandler(handler)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/auth/login",
                    headers={"X-Request-ID": "req-login-failed"},
                    json={"username": "missing_user", "password": "Secret-Do-Not-Log-1"},
                )
    finally:
        auth_logger.removeHandler(handler)

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "req-login-failed"
    assert response.json()["error"]["request_id"] == "req-login-failed"

    failed = next(record for record in records if record.getMessage() == "auth.login_failed")
    assert failed.username == "missing_user"
    assert failed.request_id == "req-login-failed"
    assert "Secret-Do-Not-Log-1" not in repr(failed.__dict__)
