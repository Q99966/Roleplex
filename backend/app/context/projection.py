"""数据库消息到稳定角色视角模型消息的投影。"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from ..models import Message

_STOPPED_MARKER = "[该回复已由用户停止]"
_SAFE_PART_TYPE = re.compile(r"[^A-Za-z0-9_-]+")


def parts_text(parts: list[dict[str, Any]]) -> str:
    """把可扩展消息 parts 转换为确定性文本，未知类型安全降级。

    Args:
        parts：消息持久化的 parts_json。
    """
    fragments: list[str] = []
    if any(part.get('type') == 'text' and part.get('part_id') for part in parts or []):
        # 展示分段不能改变 C1 的模型正文；沿用原先完整正文 + 工具占位的投影语义。
        parts = [{'type': 'text', 'text': ''.join(part.get('text', '') for part in parts if part.get('type') == 'text')},
                 *[part for part in parts if part.get('type') != 'text']]
    for part in parts or []:
        part_type = str(part.get("type", "unknown"))
        if part_type == "text":
            fragments.append(str(part.get("text", "")))
        elif part_type == "code":
            language = str(part.get("language", ""))
            fragments.append(f"```{language}\n{part.get('code', '')}\n```")
        else:
            safe_type = _SAFE_PART_TYPE.sub("_", part_type).strip("_")[:64] or "unknown"
            fragments.append(f"[不支持的消息内容:{safe_type}]")
    text = "\n".join(fragment for fragment in fragments if fragment)
    # 判空与正文分开：裁剪会破坏首行代码缩进及用户刻意保留的外围空行。
    return text if text.strip() else ""


def project_message(message: Message, *, target_role_id: int) -> BaseMessage | None:
    """按目标角色把一条终态消息投影为 assistant 或带稳定身份的 user 消息。

    Args:
        message：按 ID 读取的数据库消息。
        target_role_id：本轮执行角色，用于识别其自身历史回复。
    """
    if message.status not in {"done", "stopped"}:
        return None
    text = parts_text(message.parts_json or [])
    if not text:
        return None
    if message.status == "stopped":
        text = f"{text}\n{_STOPPED_MARKER}"
    if message.sender_type == "role" and message.sender_id == target_role_id:
        return AIMessage(content=text)
    identity = f"[{message.sender_type}:{message.sender_id}]" if message.sender_id is not None else f"[{message.sender_type}]"
    return HumanMessage(content=f"{identity} {text}")
