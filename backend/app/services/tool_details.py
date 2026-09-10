"""工具 Owner 业务详情的加密、原子更新和保留边界。"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentExecution, Generation, Message, ToolApprovalRequest, ToolExecutionDetail, User
from ..schemas import ShellDetailView


def _cipher() -> Fernet:
    """按工具详情用途隔离派生 World 密钥，不复用 API Key 密文格式。"""
    material = b'roleplex-tool-details-v1\0' + settings.resolved_api_key_secret().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _encrypt(message_id: int, call_id: str, capture: dict) -> str:
    """将内容与调用身份一起加密。

    Args:
        message_id：所属消息。
        call_id：所属调用。
        capture：已经限长的采集结果。
    """
    body = json.dumps({'message_id': message_id, 'call_id': call_id, 'capture': capture}, ensure_ascii=False)
    return _cipher().encrypt(body.encode()).decode()


def _decrypt(row: ToolExecutionDetail, value: str | None) -> dict | None:
    """解密后复核身份，拒绝密文跨调用替换。

    Args:
        row：已通过归属授权的记录。
        value：输入或输出密文。
    """
    if value is None:
        return None
    body = json.loads(_cipher().decrypt(value.encode()))
    if body['message_id'] != row.message_id or body['call_id'] != row.call_id:
        raise ValueError('TOOL_DETAILS_UNAVAILABLE')
    return body['capture']


async def update_detail(
    session: AsyncSession, *, message_id: int, call_id: str, tool_name: str, status: str,
    execution_id: str | None, user_id: int | None, private_input: dict | None, private_output: dict | None,
) -> bool:
    """在消息所有者事务中保存详情，调用方统一提交。

    Args:
        session：消息更新的短事务。
        message_id：所属消息。
        call_id：本轮工具调用身份。
        tool_name：实际工具名。
        status：公开工具状态。
        execution_id：持久 execution 身份。
        user_id：触发者，仅 Owner 可生成私有详情。
        private_input：显式白名单采集的有界输入，开始事件提供。
        private_output：有界结果，结束事件提供。
    """
    row = await session.scalar(select(ToolExecutionDetail).where(
        ToolExecutionDetail.message_id == message_id, ToolExecutionDetail.call_id == call_id,
    ))
    now = datetime.now(timezone.utc)
    # Shell 的输入只保留在审批密文中，不能因为没有输入副本而跳过输出记录。
    if row is None and (private_input is not None or tool_name == 'workspace_run_shell') and execution_id and user_id:
        user = await session.get(User, user_id)
        if user is None or not user.is_owner:
            return False
        row = ToolExecutionDetail(message_id=message_id, call_id=call_id, execution_id=execution_id,
            tool_name=tool_name, status=status, started_at=now, expires_at=now + timedelta(days=7),
            input_encrypted=_encrypt(message_id, call_id, private_input) if private_input is not None else None)
        session.add(row)
    if row is None:
        return False
    row.status = status
    if status != 'running':
        row.ended_at = now
    if private_output is not None:
        row.output_encrypted = _encrypt(message_id, call_id, private_output)
    return True


def detail_payload(row: ToolExecutionDetail) -> dict:
    """仅供已授权 Owner 响应使用，不进入共享事件。

    Args:
        row：已校验消息归属的私有记录。
    """
    result = {'tool_name': row.tool_name, 'status': row.status, 'started_at': _utc_string(row.started_at),
              'ended_at': _utc_string(row.ended_at), 'expires_at': _utc_string(row.expires_at), 'input': None, 'output': None}
    expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    if expires <= datetime.now(timezone.utc):
        return {**result, 'availability': 'expired'}
    try:
        return {**result, 'availability': 'available', 'input': _decrypt(row, row.input_encrypted),
                'output': _decrypt(row, row.output_encrypted)}
    except (InvalidToken, ValueError, KeyError, TypeError):
        return {**result, 'availability': 'unavailable'}


def _utc_string(value: datetime | None) -> str | None:
    """补回 SQLite 丢失的 UTC 标记，避免浏览器按本地时间错误解释。

    Args:
        value：应用统一以 UTC 写入的数据库时间。
    """
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


async def shell_detail_payload(session: AsyncSession, message: Message, call_id: str,
                               row: ToolExecutionDetail | None, status: str) -> dict:
    """关联原审批读取脚本，兼容没有详情行的旧 Shell 调用，绝不重放。

    Args:
        session：已完成 Owner/会话/消息归属检查的只读会话。
        message：所属消息，用实际 generation 绑定 execution。
        call_id：该消息中实际存在的 Shell 调用。
        row：既有私有详情；旧调用可能为空。
        status：共享工具 part 已观察到的状态，不据此推算执行耗时。
    """
    approval = await session.scalar(select(ToolApprovalRequest).join(AgentExecution,
        AgentExecution.execution_id == ToolApprovalRequest.execution_id).join(Generation,
        Generation.id == AgentExecution.generation_id).where(
        Generation.assistant_message_id == message.id, Generation.conversation_id == message.conversation_id,
        AgentExecution.conversation_id == message.conversation_id, ToolApprovalRequest.tool_call_id == call_id,
        ToolApprovalRequest.tool_name == 'workspace_run_shell'))
    if row is None and approval is None:
        return {'availability': 'not_recorded', 'input': None, 'output': None, 'shell': None}
    base = detail_payload(row) if row is not None else {
        'availability': 'available', 'tool_name': 'workspace_run_shell', 'status': status,
        'started_at': _utc_string(approval.requested_at), 'ended_at': None,
        'expires_at': _utc_string(approval.requested_at + timedelta(days=7)), 'input': None, 'output': None}
    output = base.get('output')
    base.update(input=None, output=None, shell=None)
    if base['availability'] in {'expired', 'unavailable'}:
        return base
    # 不因旧调用没有详情行而绕过七天展示期，也不通过读取延长保留期。
    if approval and datetime.fromisoformat(_utc_string(approval.requested_at)) + timedelta(days=7) <= datetime.now(timezone.utc):
        return {**base, 'availability': 'expired'}
    script, wait_ms = None, None
    if approval:
        from ..workspaces.approvals import decrypt_request
        from ..workspaces.commands import WorkspaceCommandError
        try:
            request = decrypt_request(approval)
            if not isinstance(request['script'], str) or len(request['script'].encode('utf-8')) > 65536:
                raise ValueError('TOOL_DETAILS_UNAVAILABLE')
            script = {'text': request['script'], 'bytes': len(request['script'].encode('utf-8')), 'truncated': False}
        except (WorkspaceCommandError, ValueError, KeyError, TypeError):
            return {**base, 'availability': 'unavailable'}
        if approval.resolved_at:
            wait_ms = max(0, int((datetime.fromisoformat(_utc_string(approval.resolved_at))
                - datetime.fromisoformat(_utc_string(approval.requested_at))).total_seconds() * 1000))
    if output is not None and (not isinstance(output, dict) or output.get('format') != 'shell-v1'):
        return {**base, 'availability': 'unavailable'}
    captured = output or {}
    availability = ('recorded' if captured.get('stdout') is not None and captured.get('stderr') is not None
        else 'not_executed' if captured.get('execution_status') == 'not_executed' or (approval and approval.status in {'rejected', 'expired'})
        else 'pending' if status == 'running' else 'not_recorded')
    try:
        value = ShellDetailView(script=script, approval_status=approval.status if approval else None,
            approval_wait_ms=wait_ms, execution_duration_ms=captured.get('execution_duration_ms'),
            stdout=captured.get('stdout'), stderr=captured.get('stderr'), output_availability=availability,
            execution_status=captured.get('execution_status'), exit_code=captured.get('exit_code'))
    except ValueError:
        return {**base, 'availability': 'unavailable'}
    return {**base, 'shell': value.model_dump()}


async def recover_details(session: AsyncSession) -> None:
    """启动时清除过期密文并标记中断，不补造结束时间或结果。

    Args:
        session：启动恢复事务，由调用者提交。
    """
    await session.execute(update(ToolExecutionDetail).where(ToolExecutionDetail.expires_at <= datetime.now(timezone.utc))
                          .values(input_encrypted=None, output_encrypted=None))
    await session.execute(update(ToolExecutionDetail).where(ToolExecutionDetail.status == 'running').values(status='interrupted'))
