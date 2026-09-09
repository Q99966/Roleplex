"""Agent 领域事件。

这是业务层唯一可见的 Agent 事件词汇表。LangGraph、LangChain 或任何模型厂商 SDK
的私有事件名只允许出现在防腐层（`app/agent/loop.py`）内部，调度、数据库、
WebSocket 和前端都只消费本模块定义的事件，从而在框架升级或替换时不受影响。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    """模型输出的一段文本增量。"""

    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    """一次工具调用开始执行。

    `call_id` 由防腐层从框架执行标识派生，仅用于把开始和结束事件配对，
    不对客户端承诺稳定含义。
    """

    call_id: str
    tool_name: str
    args_summary: str


@dataclass(frozen=True)
class ToolCallFinished:
    """一次工具调用结束。

    `status` 取值：`ok`（正常返回）、`rejected`（危险级别在执行层被拒绝）、
    `error`（工具自身执行失败）、`cancelled`（调度层收口被取消的 W1b 命令）。
    被拒绝和取消同样是正常结束，不属于异常路径。
    """

    call_id: str
    tool_name: str
    status: str
    duration_ms: int
    output_summary: str
    command_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageDone:
    """本轮生成正常结束，携带最终文本与可选用量统计。"""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCallStarted:
    """一次模型厂商 API 调用已经开始。"""

    call_index: int


@dataclass(frozen=True)
class ProviderCallCompleted:
    """一次模型厂商 API 调用完成后的归一化性能与用量摘要。"""

    call_index: int
    ttft_ms: int | None
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cache_hit_tokens: int | None
    cache_write_tokens: int | None
    cache_hit_ratio: float | None
    total_tokens_derived: bool | None = None


@dataclass(frozen=True)
class ProviderError:
    """本轮生成失败，`code` 是稳定错误码，业务层据此决定展示与降级。"""

    code: str
    message: str


# 业务层按此联合类型消费事件；新增事件类型时必须同步更新内部协议文档。
AgentEvent = TextDelta | ToolCallStarted | ToolCallFinished | ProviderCallStarted | ProviderCallCompleted | MessageDone | ProviderError
