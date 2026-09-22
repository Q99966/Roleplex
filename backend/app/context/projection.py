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
        if part_type == "execution_summary":
            continue
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


def stable_message_text(message: Message) -> str:
    """提取共享的稳定正文；失败片段与工具证据仍由原消息及中断事实保留。"""
    from ..services.execution_evidence import message_stop_reason
    if message.status not in {'done', 'stopped'}:
        return ''
    text = parts_text(message.parts_json or [])
    if not text:
        return ''
    if message.status == 'stopped':
        reason = message_stop_reason(message)
        marker = '[该回复因决策上限停止]' if reason == 'decision_budget' else '[该回复因执行步数上限停止]' if reason == 'graph_budget' else (
            _STOPPED_MARKER if reason == 'user_cancelled' else '[该回复已停止]')
        text = f'{text}\n{marker}'
    return text


def project_text(text: str, sender_type: str, sender_id: int | None, target_role_id: int) -> BaseMessage:
    """只在组装时加入相对角色身份，共享材料不因选择角色复制正文。"""
    if sender_type == "role" and sender_id == target_role_id:
        return AIMessage(content=text)
    identity = f"[{sender_type}:{sender_id}]" if sender_id is not None else f"[{sender_type}]"
    return HumanMessage(content=f"{identity} {text}")


def project_message(message: Message, *, target_role_id: int) -> BaseMessage | None:
    """按目标角色投影一条终态来源；供工作流精确上游复用。"""
    text = stable_message_text(message)
    return project_text(text, message.sender_type, message.sender_id, target_role_id) if text else None


def public_execution_facts(message: Message) -> dict | None:
    """只归纳原消息的服务器执行标记；状态计数不冒充文件验收或具体副作用明细。"""
    from ..services.execution_evidence import message_stop_reason
    if message.sender_type not in {'role', 'orchestrator'} or message.status in {'pending', 'generating'}:
        return None
    tools = {}
    for part in message.parts_json or []:
        if part.get('type') != 'tool_call':
            continue
        name = str(part.get('tool_name', 'unknown'))[:128]
        counts = tools.setdefault(name, {'statuses': {}, 'effects': {}, 'confirmed_applied_items': 0})
        status = part.get('status') if part.get('status') in {'running', 'success', 'failed', 'rejected', 'cancelled', 'interrupted', 'not_executed'} else 'unknown'
        effect = part.get('effect_state') if part.get('effect_state') in {'applied', 'not_applied', 'not_applicable'} else 'unknown'
        counts['statuses'][status] = counts['statuses'].get(status, 0) + 1
        counts['effects'][effect] = counts['effects'].get(effect, 0) + 1
        count = part.get('confirmed_applied_items')
        if type(count) is int and count > 0:
            counts['confirmed_applied_items'] += count
    if not tools and message.status == 'done':
        return None
    return {'status': message.status, 'stop_reason': message_stop_reason(message), 'tools': tools,
        'notice': '仅为原消息的执行状态计数，不是任务验收；未知不能当作未执行。'}
