"""上下文硬预算与安全优先的本地 token 估算。"""
from __future__ import annotations

from math import ceil
import json
from dataclasses import asdict
from typing import Sequence

from langchain_core.messages import BaseMessage

from .domain import TokenEstimate

ESTIMATOR_KIND = "conservative_utf8_v2"
ESTIMATOR_VERSION = 2
_MESSAGE_WRAPPER_TOKENS = 8


def message_content_text(message: BaseMessage) -> str:
    """把 LangChain 消息内容规范化为估算文本。

    Args:
        message：已完成角色视角投影的模型消息。
    """
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


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
    total = 0
    for message in messages:
        total += estimate_text_tokens(message_content_text(message), structural_tokens=_MESSAGE_WRAPPER_TOKENS)
        # 工具轮不只有正文：模型的调用参数、名称和结果关联 ID 同样占据输入。
        extra = {key: getattr(message, key) for key in ['tool_calls', 'name', 'tool_call_id'] if getattr(message, key, None)}
        if extra:
            total += estimate_text_tokens(json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    return total


def estimate_tools_tokens(definitions: Sequence[dict]) -> int:
    """只计算实际发送的工具 Schema；权限配置等服务端元数据不冒充模型输入。"""
    return estimate_text_tokens(json.dumps(list(definitions), ensure_ascii=False, sort_keys=True,
        separators=(',', ':')), structural_tokens=8) if definitions else 0


def estimate_request(messages: Sequence[BaseMessage], definitions: Sequence[dict]) -> dict:
    """返回每次真实组装的无正文数字摘要，厂商 usage 另行记录。"""
    message_tokens, tool_tokens = estimate_messages_tokens(messages), estimate_tools_tokens(definitions)
    return {**asdict(token_estimate(message_tokens + tool_tokens)), 'message_tokens': message_tokens,
        'tool_schema_tokens': tool_tokens, 'message_count': len(messages),
        'tool_message_count': sum(message.type == 'tool' for message in messages)}


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
