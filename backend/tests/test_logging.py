"""结构化日志、请求关联与认证审计测试。"""
from __future__ import annotations

import json
import logging
import os
import stat
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


def test_json_formatter_assigns_identity_order_category_and_omits_empty_context():
    """每条事件有稳定身份/顺序，关联字段无值时省略且敏感键被剔除。"""
    from app.config.logging import EventMetadataFilter, JsonFormatter, log_context

    metadata = EventMetadataFilter(
        run_kind="runtime", world_name="default", process_instance_id="a8137c2f"
    )
    formatter = JsonFormatter()
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
                "cache_write_tokens": 20,
                "cache_hit_ratio": 2 / 3,
                "estimated_context_tokens": 1800,
                "input_budget_tokens": 4096,
                "safety_margin_tokens": 128,
                "color_message": "internal-colored-template",
                "metadata": {
                    "safe": "visible",
                    "authorization": "Bearer nested-secret",
                    "api_key": "nested-api-key",
                },
            },
        )
        metadata.filter(record)
        payload = json.loads(formatter.format(record))

    assert payload["schema_version"] == 2
    assert payload["timestamp"].endswith("+08:00")
    assert payload["event"] == "test.event"
    assert payload["category"] == "app"
    assert payload["run_kind"] == "runtime"
    assert payload["world_name"] == "default"
    assert payload["process_instance_id"] == "a8137c2f"
    assert payload["process_seq"] == 1
    assert payload["event_id"].startswith("evt_")
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
    assert payload["cache_write_tokens"] == 20
    assert payload["cache_hit_ratio"] == 2 / 3
    assert payload["estimated_context_tokens"] == 1800
    assert payload["input_budget_tokens"] == 4096
    assert payload["safety_margin_tokens"] == 128
    assert payload["metadata"] == {"safe": "visible"}
    assert "user_id" not in payload
    assert "generation_id" not in payload
    assert "password" not in payload
    assert "never-log-this" not in formatter.format(record)
    assert "credential-token-must-disappear" not in formatter.format(record)
    assert "internal-colored-template" not in formatter.format(record)
    assert "nested-secret" not in formatter.format(record)
    assert "nested-api-key" not in formatter.format(record)


def _json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_runtime_routes_categories_and_error_copy_keeps_same_event_identity(tmp_path: Path):
    """runtime 按类别写四个文件，错误副本与 agent 原始事实身份完全一致。"""
    from app.config.logging import configure_logging, shutdown_logging

    clock = _Clock()
    configure_logging(
        tmp_path, run_kind="runtime", world_name="default", clock=clock,
        process_instance_id="a8137c2f",
    )
    logging.getLogger("roleplex.auth").info("auth.login_failed", extra={"username": "missing"})
    logging.getLogger("roleplex.chat").info("provider.call_completed", extra={"duration_ms": 12})
    logging.getLogger("roleplex.http").info("http.completed", extra={"status_code": 200})
    logging.getLogger("roleplex.chat").error(
        "provider.call_failed", extra={"error_code": "PROVIDER_TIMEOUT"}
    )
    shutdown_logging()

    day = tmp_path / "runtime" / "2026-08-24"
    assert {path.name for path in day.glob("*.jsonl")} == {
        "app.jsonl", "agent.jsonl", "access.jsonl", "errors.jsonl",
    }
    assert [item["event"] for item in _json_lines(day / "app.jsonl")] == ["auth.login_failed"]
    assert [item["event"] for item in _json_lines(day / "access.jsonl")] == ["http.completed"]
    agent_error = _json_lines(day / "agent.jsonl")[-1]
    copied_error = _json_lines(day / "errors.jsonl")[-1]
    assert agent_error == copied_error
    assert copied_error["category"] == "agent"
    assert copied_error["event_id"] == agent_error["event_id"]


def test_persistent_logs_exclude_third_party_free_text(tmp_path: Path):
    """Alembic/Uvicorn 等自由文本只留终端，机器日志只收项目稳定事件。"""
    from app.config.logging import configure_logging, shutdown_logging

    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock())
    logging.getLogger("alembic.runtime.migration").info("Running migration with free text")
    logging.getLogger("uvicorn.error").info("Application startup complete")
    logging.getLogger("roleplex.lifecycle").info("process.started")
    shutdown_logging()

    app_events = _json_lines(tmp_path / "runtime/2026-08-24/app.jsonl")
    assert [item["event"] for item in app_events] == ["process.started"]


@pytest.mark.skipif(os.name == "nt", reason="Windows 使用当前用户 ACL，不断言 POSIX mode")
def test_new_runtime_log_paths_use_private_posix_permissions(tmp_path: Path):
    """新建日志目录为 0750，机器日志文件为 0640。"""
    from app.config.logging import configure_logging, shutdown_logging

    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock())
    logging.getLogger("roleplex.lifecycle").info("process.started")
    shutdown_logging()

    day = tmp_path / "runtime/2026-08-24"
    assert stat.S_IMODE(day.stat().st_mode) == 0o750
    assert stat.S_IMODE((day / "app.jsonl").stat().st_mode) == 0o640


def test_runtime_restart_appends_same_active_file(tmp_path: Path):
    """同日后端重启继续追加 active 文件，不创建进程级目录。"""
    from app.config.logging import configure_logging, shutdown_logging

    clock = _Clock()
    configure_logging(tmp_path, run_kind="runtime", world_name="alpha", clock=clock)
    logging.getLogger("roleplex.auth").info("first.process")
    shutdown_logging()
    configure_logging(tmp_path, run_kind="runtime", world_name="beta", clock=clock)
    logging.getLogger("roleplex.auth").info("second.process")
    shutdown_logging()

    path = tmp_path / "runtime" / "2026-08-24" / "app.jsonl"
    assert [item["event"] for item in _json_lines(path)] == ["first.process", "second.process"]
    assert len({item["process_instance_id"] for item in _json_lines(path)}) == 2


def test_runtime_rotation_uses_ascending_immutable_segments(tmp_path: Path):
    """大小或一小时轮转后，旧片段按 001 递增且 active 保持基础名。"""
    from app.config.logging import DailyCategoryFileHandler, JsonFormatter

    clock = _Clock()
    handler = DailyCategoryFileHandler(tmp_path, "app", max_bytes=600, max_seconds=3600, clock=clock)
    handler.setFormatter(JsonFormatter())
    handler.emit(_record("x" * 300))
    clock.advance(3600)
    handler.emit(_record("after.hour"))
    handler.close()

    day = tmp_path / "2026-08-24"
    assert (day / "app.001.jsonl").exists()
    assert (day / "app.jsonl").exists()
    assert "after.hour" not in (day / "app.001.jsonl").read_text(encoding="utf-8")
    assert "after.hour" in (day / "app.jsonl").read_text(encoding="utf-8")


def test_unit_logging_does_not_persist_application_events(tmp_path: Path):
    """pytest 应用日志只进终端，持久化由 session summary/failure 插件负责。"""
    from app.config.logging import configure_logging, shutdown_logging

    clock = _Clock()
    configure_logging(tmp_path, run_kind="unit", world_name="default", clock=clock)
    logging.getLogger("roleplex.auth").error("unit.failure")
    shutdown_logging()
    assert not list(tmp_path.rglob("*.jsonl"))


def test_e2e_logging_uses_shared_run_directory(tmp_path: Path):
    """E2E 后端按 fake/real 和 8 位 run ID 写入同一轮目录。"""
    from app.config.logging import configure_logging, shutdown_logging

    clock = _Clock()
    configure_logging(
        tmp_path, run_kind="e2e-fake", world_name="alpha", clock=clock,
        run_id="a8137c2f", run_started_at=clock.now(),
    )
    logging.getLogger("roleplex.chat").info("generation.completed")
    shutdown_logging()
    run_dir = tmp_path / "tests" / "e2e" / "fake" / "2026-08-24" / "12-34-56_a8137c2f"
    assert (run_dir / "events.jsonl").exists()
    assert _json_lines(run_dir / "events.jsonl")[0]["run_id"] == "a8137c2f"


def test_jsonl_reader_tolerates_only_a_broken_final_line(tmp_path: Path):
    """崩溃截断只允许发生在最后一行，中间损坏必须阻断消费。"""
    from app.config.logging import LogCorruptionError, read_jsonl

    final_broken = tmp_path / "tail.jsonl"
    final_broken.write_text('{"event":"ok"}\n{"event":"broken', encoding="utf-8")
    result = read_jsonl(final_broken)
    assert result.records == [{"event": "ok"}]
    assert result.truncated_tail is True

    middle_broken = tmp_path / "middle.jsonl"
    middle_broken.write_text('{"event":"ok"}\nnot-json\n{"event":"later"}\n', encoding="utf-8")
    with pytest.raises(LogCorruptionError, match="第 2 行"):
        read_jsonl(middle_broken)


def test_runtime_restart_repairs_broken_active_tail_and_records_recovery(tmp_path: Path):
    """继续追加 active 文件前截断崩溃尾行，并留下恢复字节数事件。"""
    from app.config.logging import configure_logging, shutdown_logging

    day = tmp_path / "runtime/2026-08-24"
    day.mkdir(parents=True)
    (day / "app.jsonl").write_bytes(b'{"event":"before"}\n{"event":"broken')
    configure_logging(tmp_path, run_kind="runtime", world_name="default", clock=_Clock())
    shutdown_logging()

    lines = _json_lines(day / "app.jsonl")
    assert lines[0] == {"event": "before"}
    assert lines[-1]["event"] == "log.tail_recovered"
    assert lines[-1]["recovered_bytes"] > 0


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
