"""运行模式配置测试。"""
from __future__ import annotations


def test_normal_runtime_uses_real_provider_by_default(monkeypatch):
    """正常启动默认调用真实 provider，测试套件再显式切换到 fake。"""
    from app.config import Settings

    monkeypatch.delenv("AGENT_USE_FAKE_PROVIDER", raising=False)
    runtime = Settings(_env_file=None)

    assert runtime.agent_use_fake_provider is False
