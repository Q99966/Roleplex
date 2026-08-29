"""上下文硬预算与安全优先的本地 token 估算。"""
from __future__ import annotations

from math import ceil
from typing import Sequence

from langchain_core.messages import BaseMessage

from .domain import TokenEstimate

ESTIMATOR_KIND = "conservative_utf8_v1"
ESTIMATOR_VERSION = 1
_MESSAGE_WRAPPER_TOKENS = 8


def message_content_text(message: BaseMessage) -> str:
    """把 LangChain 消息内容规范化为估算文本。

    Args:
        message：已完成角色视角投影的模型消息。
    """
    content = message.content
    if isinstance(content, str):
        return content
    return str(content)


def estimate_text_tokens(text: str, *, structural_tokens: int = 0) -> int:
    """按 UTF-8 字节上界估算未知 tokenizer 的输入用量。

    Args:
        text：最终规范化文本。
        structural_tokens：消息或请求包装的固定结构开销。
    """
    return len(text.encode("utf-8")) + structural_tokens


def estimate_messages_tokens(messages: Sequence[BaseMessage]) -> int:
    """估算一组规范化消息及其包装开销。

    Args:
        messages：按最终发送顺序排列的模型消息。
    """
    return sum(
        estimate_text_tokens(message_content_text(message), structural_tokens=_MESSAGE_WRAPPER_TOKENS)
        for message in messages
    )


def token_estimate(estimated_tokens: int) -> TokenEstimate:
    """为预算估算增加固定比例和最小安全余量。

    Args:
        estimated_tokens：UTF-8 上界与结构开销之和。
    """
    margin = max(128, ceil(estimated_tokens * 0.02))
    return TokenEstimate(
        estimated_tokens=estimated_tokens,
        estimator_kind=ESTIMATOR_KIND,
        estimator_version=ESTIMATOR_VERSION,
        is_provider_exact=False,
        safety_margin_tokens=margin,
    )
