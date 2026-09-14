"""保留调用级执行证据与停止原因，不生成回合总结或模型历史文本。"""
from __future__ import annotations

from ..schemas import StopReason

READ_ONLY = frozenset({'workspace_list', 'workspace_read', 'workspace_run_command',
                       'workspace_service_status', 'workspace_service_logs'})
STOP_REASONS = frozenset({'user_cancelled', 'graph_budget', 'provider_failed', 'protocol_error', 'interrupted', 'context_rejected'})


def tool_evidence(name: str, status: str, private_output: dict | None) -> dict:
    """仅从白名单提交凭据取事实，工具成功/取消不能替代副作用证据。

    Args:
        name：宿主已绑定的工具名。
        status：执行层公开状态。
        private_output：有界原生凭据，只提取布尔值和数量，不回传原文。
    """
    effect, count = 'unknown', 0
    if name in READ_ONLY:
        effect = 'not_applicable'
    elif name in {'workspace_write', 'workspace_edit'} and isinstance(private_output, dict):
        if private_output.get('format') == 'write-batch-v1':
            items = private_output.get('batch', {}).get('items', [])[:8]
            count = sum(item.get('applied') is True for item in items)
            if items and all(item.get('applied') is True for item in items):
                effect = 'applied'
            elif items and all(item.get('applied') is False for item in items):
                effect = 'not_applied'
        elif private_output.get('format') == 'write-v1':
            write = private_output.get('write') or {}
            if private_output.get('commit_confirmed') is True or any(file.get('applied') is True for file in write.get('files', [])[:1]):
                effect, count = 'applied', 1
            elif write.get('availability') == 'not_executed':
                effect = 'not_applied'
        if private_output.get('diagnostic', {}).get('executed') is False and count == 0:
            effect = 'not_applied'
    return {'effect_state': effect, 'confirmed_applied_items': count}



def message_stop_reason(message) -> StopReason | None:
    """读取服务器终态原因，旧摘要只用于兼容，不作为新的事实来源。

    Args:
        message：已鉴权读取的持久消息；用户内容不能提供服务器元数据。
    """
    if message.sender_type not in {'role', 'orchestrator'} or message.status in {'done', 'generating', 'pending'}:
        return None
    metadata = message.meta_json or {}
    reason = metadata.get('stop_reason')
    if isinstance(reason, str) and reason in STOP_REASONS:
        return reason
    if metadata.get('execution_summary_version') == 1:
        for part in message.parts_json or []:
            if part.get('type') == 'execution_summary' and part.get('version') == 1:
                reason = part.get('stop_reason')
                return reason if isinstance(reason, str) and reason in STOP_REASONS else None
    return None
