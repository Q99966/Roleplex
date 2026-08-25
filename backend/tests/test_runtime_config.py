"""运行模式配置测试。"""
from __future__ import annotations

from pathlib import Path


def test_normal_runtime_uses_real_provider_by_default(monkeypatch):
    """正常启动默认调用真实 provider，测试套件再显式切换到 fake。"""
    from app.config import Settings

    monkeypatch.delenv("AGENT_USE_FAKE_PROVIDER", raising=False)
    runtime = Settings(_env_file=None)

    assert runtime.agent_use_fake_provider is False


def test_runtime_directories_stay_at_repository_root_after_config_packaging():
    """配置模块分包后，默认数据库和日志目录仍必须位于仓库根目录。"""
    from app.config import DATA_DIR, LOG_DIR, ROOT_DIR

    repository_root = Path(__file__).resolve().parents[2]
    assert ROOT_DIR == repository_root
    assert DATA_DIR == repository_root / "data"
    assert LOG_DIR == repository_root / "logs"
