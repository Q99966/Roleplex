"""模型 provider 工厂与能力表。

角色配置在这里转换为具体的 LangChain 模型对象。API Key 只在本模块经解密边界进入
provider 实例，不进入日志、异常信息或事件负载；参数按"厂商 + 模型能力"过滤，
不兼容的参数在运行期剔除而不是让请求在厂商侧报错。
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from ..models import ModelConfig, Role
from ..security.credentials import decrypt_api_key

logger = logging.getLogger("roleplex.agent.provider")

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

# 各厂商可透传的采样参数白名单；不在表内的键一律丢弃，避免把未知参数带到厂商侧。
_ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    PROVIDER_ANTHROPIC: frozenset({"temperature", "top_p", "max_tokens", "timeout", "stop"}),
    PROVIDER_OPENAI_COMPATIBLE: frozenset(
        {"temperature", "top_p", "max_tokens", "timeout", "stop", "frequency_penalty", "presence_penalty"}
    ),
}

# 能力表默认值，可被 model_configs.capability_overrides_json 按厂商覆盖。
# 真实能力差异以契约测试结论为准，不在业务代码里写死全局规则：
# OpenAI 兼容一侧已用 DeepSeek 实测过流式、工具调用、参数兼容与错误映射，沿用下列默认值即可；
# 视觉能力因厂商差异过大，默认按不支持处理，由配置显式覆盖。
_DEFAULT_CAPABILITIES: dict[str, dict[str, Any]] = {
    PROVIDER_ANTHROPIC: {"vision": True, "parallel_tools": True, "supports_temperature": True, "supports_top_p": True},
    PROVIDER_OPENAI_COMPATIBLE: {"vision": False, "parallel_tools": True, "supports_temperature": True, "supports_top_p": True},
}


def capabilities_for(model_config: ModelConfig) -> dict[str, Any]:
    """返回该模型配置的最终能力表。

    Args:
        model_config：角色引用的厂商配置。

    Returns:
        内置默认值与 `capability_overrides_json` 合并后的能力字典；未知厂商按最保守的
        能力集处理，避免把不支持的能力当作可用。
    """
    base = dict(_DEFAULT_CAPABILITIES.get(model_config.provider_type, {"vision": False, "parallel_tools": False}))
    base.update(model_config.capability_overrides_json or {})
    return base


def filter_params(params: dict[str, Any], *, provider_type: str, capabilities: dict[str, Any]) -> dict[str, Any]:
    """按厂商白名单和模型能力过滤采样参数。

    Args:
        params：角色配置中保存的原始参数。
        provider_type：厂商类型。
        capabilities：该模型的最终能力表。

    Returns:
        可以安全透传给 provider 的参数子集。开启思考模式的组合会额外剔除
        temperature/top_p，因为部分模型在该模式下不接受这两个参数。
    """
    allowed = _ALLOWED_PARAMS.get(provider_type, frozenset())
    filtered = {key: value for key, value in (params or {}).items() if key in allowed and value is not None}
    thinking_enabled = bool((params or {}).get("thinking"))
    if thinking_enabled or not capabilities.get("supports_temperature", True):
        filtered.pop("temperature", None)
    if thinking_enabled or not capabilities.get("supports_top_p", True):
        filtered.pop("top_p", None)
    return filtered


def build_chat_model(role: Role, model_config: ModelConfig) -> BaseChatModel:
    """按角色配置构造可直接用于 Agent 循环的模型对象。

    Args:
        role：提供模型名与采样参数的角色。
        model_config：提供厂商类型、接入地址和加密 Key 的配置。

    Returns:
        已配置好的 LangChain 模型实例。

    Raises:
        ValueError：厂商类型未知，或 Key 无法解密。
    """
    capabilities = capabilities_for(model_config)
    params = filter_params(role.params_json or {}, provider_type=model_config.provider_type, capabilities=capabilities)
    api_key = decrypt_api_key(model_config.api_key_encrypted)

    # 只记录可公开的元数据；Key、Authorization 头和完整参数不进日志。
    logger.info(
        "provider.built",
        extra={
            "provider_type": model_config.provider_type,
            "provider_mode": "real",
            "model": role.model_name,
            "role_id": role.id,
            "param_keys": sorted(params),
        },
    )

    if model_config.provider_type == PROVIDER_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=role.model_name,
            api_key=api_key,
            base_url=model_config.base_url or None,
            **params,
        )
    if model_config.provider_type == PROVIDER_OPENAI_COMPATIBLE:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=role.model_name,
            api_key=api_key,
            base_url=model_config.base_url or None,
            **params,
        )
    raise ValueError(f"未知的 provider 类型：{model_config.provider_type}")
