"""文件操作边界的最小持久证据；采集故障不反向改变提交结果。"""
import asyncio
from contextvars import copy_context
import base64
import hashlib
import json
import logging
import re
from datetime import datetime,timedelta,timezone
from cryptography.fernet import Fernet
from sqlalchemy import select
from ..models import FileEffect


def _cipher():
    """按用途派生当前World密钥，不与其他密文格式混用。"""
    from ..config import settings
    key=hashlib.sha256(b'roleplex-file-effect-v1\0'+settings.resolved_api_key_secret().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def decode(row):
    """Args:
        row：调用方已验证归属的证据行；过期或身份不符拒绝读取。
    """
    expiry=row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    if expiry<=datetime.now(timezone.utc) or not row.payload_encrypted:
        raise ValueError('EVIDENCE_UNAVAILABLE')
    value=json.loads(_cipher().decrypt(row.payload_encrypted.encode()))
    if value.pop('identity')!=[row.execution_id,row.call_id,row.item_index]:
        raise ValueError('EVIDENCE_UNAVAILABLE')
    if (not isinstance(value.get('path'),str) or value.get('operation') not in {'write','edit'}
        or value.get('state') not in {'prepared','confirmed'}
        or not isinstance(value.get('after_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',value['after_sha256'])
        or type(value.get('bytes')) is not int or value['bytes']<0):
        raise ValueError('EVIDENCE_UNAVAILABLE')
    return value


async def record(*,execution_id,call_id,item_index,payload):
    """短事务幂等保存；取消时等待已开始的事实交接再传播取消。

    Args:
        execution_id：宿主执行身份。
        call_id：既有工具调用身份。
        item_index：批次节点序号，单文件为0。
        payload：服务器生成的最小证据，不含正文。
    """
    from ..db import SessionLocal,with_locked_retry
    from ..agent.argument_errors import safe_exception_type
    async def operation():
        """同一工具任务是本节点唯一写入者，不与消息所有者争抢revision。"""
        now=datetime.now(timezone.utc)
        encrypted=_cipher().encrypt(json.dumps({'identity':[execution_id,call_id,item_index],**payload},ensure_ascii=False).encode()).decode()
        async with SessionLocal() as session:
            row=await session.scalar(select(FileEffect).where(FileEffect.execution_id==execution_id,
                FileEffect.call_id==call_id,FileEffect.item_index==item_index))
            if row is None:
                row=FileEffect(execution_id=execution_id,call_id=call_id,item_index=item_index,
                    expires_at=now+timedelta(days=7))
                session.add(row)
            row.payload_encrypted=encrypted
            row.updated_at=now
            await session.commit()
    async def persist():
        """故障只留安全诊断，不冒充文件未提交。"""
        try:
            await with_locked_retry(operation)
        except Exception as exc:
            logging.getLogger('roleplex.agent').warning('tool.evidence_record_failed',extra={
                'execution_id':execution_id,'tool_call_id':call_id,'error_type':safe_exception_type(exc)})
    task=asyncio.create_task(persist(),context=copy_context())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def checkpoint(service_execution_id,*,path,operation,item_index,before_hash,after_hash,bytes_count,state,created_parent_count=0):
    """只在真实聊天执行的采集作用域启用，不使独立文件服务隐式访问数据库。

    Args:
        service_execution_id：文件服务绑定的执行。
        path：已校验工作区相对路径。
        operation：固定write/edit。
        item_index：服务器分配的批次序号。
        before_hash：写前文件版本或null。
        after_hash：拟提交或已确认的版本。
        bytes_count：新内容字节数。
        state：prepared/confirmed。
        created_parent_count：已观察目录副作用计数。

    Returns:
        True表示发生过持久化等待，调用方必须在提交前重新鉴权；不表示记录必然成功。
    """
    from ..agent.write_capture import write_capture_scope
    from ..agent.tool_context import tool_call_id
    scope=write_capture_scope.get()
    call_id=tool_call_id.get()
    if scope is None or not scope.execution_id or scope.execution_id!=service_execution_id or not call_id:
        return
    await record(execution_id=scope.execution_id,call_id=call_id,item_index=item_index,payload={
        'path':path,'operation':operation,'state':state,'before_sha256':before_hash,
        'after_sha256':after_hash,'bytes':bytes_count,'created_parent_count':created_parent_count})
    return True
