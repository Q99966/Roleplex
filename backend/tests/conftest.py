from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# 每轮测试使用全新数据库文件，避免 Owner 单例和事件序号跨用例污染。
_TEST_DB = Path(tempfile.gettempdir()) / f"roleplex-test-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def cleanup_database():
    """测试结束后删除临时数据库文件及其 WAL 附属文件。"""
    yield
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(_TEST_DB) + suffix)
        if candidate.exists():
            candidate.unlink()
