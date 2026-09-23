"""ContextBuilder 的内部请求、结果、预算和错误契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage


if TYPE_CHECKING:
    from ..agent.capabilities import ExecutionCapabilities

# v10 区分授权人与通信主体，节点由独立输入引用构建，公开广播不冒充用户要求。
CONTEXT_SCHEMA_VERSION = 10


@dataclass(frozen=True)
class ContextBuildRequest:
    """一次上下文构建的稳定输入身份。"""

    role_id: int
    conversation_id: int
    current_message_id: int | None
    triggered_by_user_id: int | None
    execution_kind: str = "single"
    execution_id: str | None = None
    draft_text: str | None = None


@dataclass(frozen=True)
class TokenEstimate:
    """与 Provider usage 明确分离的本地预算估算。"""

    estimated_tokens: int
    estimator_kind: str
    estimator_version: int
    is_provider_exact: bool
    safety_margin_tokens: int


@dataclass(frozen=True)
class ContextBudget:
    """本轮有效窗口、输出预留和历史裁剪结果。"""

    effective_context_window: int
    output_reserved_tokens: int
    input_budget_tokens: int
    estimate: TokenEstimate
    included_message_count: int
    truncated_message_count: int


@dataclass(frozen=True)
class ContextFingerprints:
    """不含 Prompt 原文的稳定层内容指纹。"""

    runtime_prefix_hash: str
    role_prefix_hash: str
    conversation_prefix_hash: str
    tool_policy_hash: str
    interruption_hash: str | None = None
    platform_prefix_hash: str | None = None
    world_prefix_hash: str | None = None


@dataclass(frozen=True)
class ContextBuildResult:
    """可直接交给 Agent 防腐层的规范化上下文。"""

    system_prompt: str
    history: tuple[BaseMessage, ...]
    current_message: str
    budget: ContextBudget
    fingerprints: ContextFingerprints
    context_schema_version: int = CONTEXT_SCHEMA_VERSION
    prompt_snapshot: dict = field(default_factory=dict)
    capabilities: ExecutionCapabilities | None = None
    material_snapshot: dict = field(default_factory=dict)
    request_estimate: dict = field(default_factory=dict)


class ContextBuildError(ValueError):
    """上下文输入或持久状态不满足构建契约。"""


class ContextBudgetExceeded(ContextBuildError):
    """不可裁剪的最小上下文已经超过角色有效窗口。"""

    error_code = "CONTEXT_BUDGET_EXCEEDED"

    def __init__(
        self,
        *,
        estimated_tokens: int,
        safety_margin_tokens: int,
        input_budget_tokens: int,
        estimator_kind: str,
    ) -> None:
        """保存安全预算数字，不携带 system 或用户消息原文。

        Args:
            estimated_tokens：不可裁剪最小输入的估算 token。
            safety_margin_tokens：估算器为请求包装和口径偏差保留的余量。
            input_budget_tokens：扣除输出预留后的可用输入预算。
            estimator_kind：本轮估算器公共标识。
        """
        super().__init__(self.error_code)
        self.estimated_tokens = estimated_tokens
        self.safety_margin_tokens = safety_margin_tokens
        self.input_budget_tokens = input_budget_tokens
        self.estimator_kind = estimator_kind
