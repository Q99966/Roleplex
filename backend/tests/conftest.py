from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_ROOT.parent / "data"
sys.path.insert(0, str(BACKEND_ROOT))

# 保留最近若干轮的测试数据库供人工排查，更早的在下一轮开始时清理。
_KEEP_RUNS = 5

# 每轮测试使用带时间戳的独立数据库：Owner 是实例级单例，复用同一个库会让后注册的账号
# 变成 Guest；时间戳同时用于测试账号名，方便按轮次对照数据库内容。
_STAMP = os.environ.setdefault("ROLEPLEX_TEST_STAMP", datetime.now().strftime("%Y%m%d%H%M%S"))
_TEST_DB = DATA_DIR / f"roleplex-test-{_STAMP}.db"


def _prune_old_databases() -> None:
    """删除超出保留轮次的旧测试数据库，忽略仍被占用的文件。"""
    runs = sorted({path.name.split(".db")[0] for path in DATA_DIR.glob("roleplex-test-*.db*")})
    for name in runs[:-_KEEP_RUNS] if len(runs) > _KEEP_RUNS else []:
        for suffix in ("", "-wal", "-shm"):
            candidate = DATA_DIR / f"{name}.db{suffix}"
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                # 被其他进程占用时留给下一轮清理，不能让清理失败影响测试。
                continue


DATA_DIR.mkdir(parents=True, exist_ok=True)
_prune_old_databases()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
# 强制使用确定性 fake provider：环境变量优先于本地 .env，
# 因此即使开发机上配了真实厂商开关，普通回归也不会联网或产生费用。
os.environ["AGENT_USE_FAKE_PROVIDER"] = "true"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def report_database_location():
    """在测试结束后打印本轮数据库位置，便于登录查看测试产生的数据。"""
    yield
    print(f"\n本轮测试数据库：{_TEST_DB}")
