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
from ..models import ToolExecutionDetail, User


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
    if row is None and private_input is not None and execution_id and user_id:
        user = await session.get(User, user_id)
        if user is None or not user.is_owner:
            return False
        row = ToolExecutionDetail(message_id=message_id, call_id=call_id, execution_id=execution_id,
            tool_name=tool_name, status=status, started_at=now, expires_at=now + timedelta(days=7),
            input_encrypted=_encrypt(message_id, call_id, private_input))
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


async def recover_details(session: AsyncSession) -> None:
    """启动时清除过期密文并标记中断，不补造结束时间或结果。

    Args:
        session：启动恢复事务，由调用者提交。
    """
    await session.execute(update(ToolExecutionDetail).where(ToolExecutionDetail.expires_at <= datetime.now(timezone.utc))
                          .values(input_encrypted=None, output_encrypted=None))
    await session.execute(update(ToolExecutionDetail).where(ToolExecutionDetail.status == 'running').values(status='interrupted'))
