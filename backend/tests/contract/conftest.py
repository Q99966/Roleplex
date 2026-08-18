"""真实模型厂商契约测试的配置。

这一层默认不参与普通回归：没有配置凭据时整层跳过，配置后才会真正联网并消耗额度。
凭据只从环境变量读取，也可以写在 `backend/.env`（已被 .gitignore 忽略），
任何情况下都不得把 Key 写进仓库、测试固件或日志。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    """把 `backend/.env` 中的键补进环境变量。

    只补齐当前缺失的键，因此显式设置的环境变量始终优先；不引入额外依赖，
    也不打印文件内容。
    """
    env_path = BACKEND_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def pytest_collection_modifyitems(config, items) -> None:
    """给本目录下的用例统一打上 contract 标记。

    凭据可能已经写在本地 `.env` 里，只靠"有没有配凭据"来决定是否运行，会让普通回归
    在开发者机器上悄悄产生费用；因此运行这一层必须显式选择该标记。

    该钩子会收到本轮收集到的全部用例，因此必须按路径过滤，只标记本目录下的用例。
    """
    contract_dir = Path(__file__).resolve().parent
    for item in items:
        if contract_dir in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.contract)


@dataclass(frozen=True)
class VendorSpec:
    """一组厂商契约测试所需的配置来源。"""

    id: str
    provider_type: str
    key_env: str
    model_env: str
    base_url_env: str | None = None

    @property
    def api_key(self) -> str:
        return os.environ.get(self.key_env, "")

    @property
    def model_name(self) -> str:
        return os.environ.get(self.model_env, "")

    @property
    def base_url(self) -> str | None:
        return os.environ.get(self.base_url_env, "") or None if self.base_url_env else None

    def configured(self) -> bool:
        """是否已提供可用凭据与模型名。"""
        return bool(self.api_key and self.model_name)

    def build_model(self, *, api_key: str | None = None, params: dict | None = None):
        """经由产品的 provider 工厂构造模型，确保契约测试覆盖参数过滤与解密边界。

        Args:
            api_key：覆盖使用的 Key，传入无效值可验证错误映射。
            params：覆盖采样参数。
        """
        from app.models import ModelConfig, Role
        from app.security import encrypt_api_key
        from app.agent import providers

        model_config = ModelConfig(
            provider_type=self.provider_type,
            base_url=self.base_url,
            api_key_encrypted=encrypt_api_key(api_key or self.api_key),
            capability_overrides_json={},
        )
        role = Role(
            name=f"contract-{self.id}",
            system_prompt="你是契约测试助手，请简短回答。",
            model_name=self.model_name,
            params_json=params if params is not None else {"temperature": 0.0, "max_tokens": 256},
        )
        return providers.build_chat_model(role, model_config)


VENDORS = (
    VendorSpec(
        id="anthropic", provider_type="anthropic",
        key_env="ROLEPLEX_CONTRACT_ANTHROPIC_KEY", model_env="ROLEPLEX_CONTRACT_ANTHROPIC_MODEL",
    ),
    VendorSpec(
        id="openai_compatible", provider_type="openai_compatible",
        key_env="ROLEPLEX_CONTRACT_OPENAI_KEY", model_env="ROLEPLEX_CONTRACT_OPENAI_MODEL",
        base_url_env="ROLEPLEX_CONTRACT_OPENAI_BASE_URL",
    ),
)


@pytest.fixture(params=[vendor.id for vendor in VENDORS])
def vendor(request) -> VendorSpec:
    """逐个提供已配置好的厂商；未配置的厂商跳过而不是失败。"""
    spec = next(item for item in VENDORS if item.id == request.param)
    if not spec.configured():
        pytest.skip(f"未配置 {spec.key_env} / {spec.model_env}，跳过 {spec.id} 契约测试")
    return spec
