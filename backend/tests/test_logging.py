"""结构化日志、请求关联与认证审计测试。"""
from __future__ import annotations

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient


def test_json_formatter_includes_context_and_safe_extra_fields():
    """JSONL 日志保留链路与业务字段，同时剔除敏感键。"""
    from app.logging_config import JsonFormatter, log_context

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
                "metadata": {
                    "safe": "visible",
                    "authorization": "Bearer nested-secret",
                    "api_key": "nested-api-key",
                },
            },
        )
        payload = json.loads(formatter.format(record))

    assert payload["event"] == "test.event"
    assert payload["request_id"] == "req-test-1"
    assert payload["conversation_id"] == 7
    assert payload["chain_id"] == "chain-test-1"
    assert payload["execution_id"] == "run-test-1"
    assert payload["duration_ms"] == 12.5
    assert payload["message_id"] == 9
    assert payload["metadata"] == {"safe": "visible"}
    assert "password" not in payload
    assert "never-log-this" not in formatter.format(record)
    assert "nested-secret" not in formatter.format(record)
    assert "nested-api-key" not in formatter.format(record)


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
