"""只读服务发现，不取得文件租用、不启动/停止进程、不读取脚本或日志。"""
import base64
import binascii
import json
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_, select

from ..agent.tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX
from ..db import SessionLocal
from ..realtime.events import current_epoch
from ..runtime.models import RuntimeEntry
from ..runtime.registry import ACTIVE
from .access import authorized_execution

TOOL_NAME = 'workspace_service_status'
OUTPUT_LIMIT = 65536


class ServiceStatusInput(BaseModel):
    """不传 ID 列表查询；旧 ID 查询不接受分页参数混用。"""
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    runtime_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode='before')
    @classmethod
    def exclusive_mode(cls, value):
        """Args:
            value：原始工具参数，null ID 与混传默认值也拒绝。
        """
        if not isinstance(value, dict) or ('runtime_id' in value and (
            value['runtime_id'] is None or 'cursor' in value or 'limit' in value)):
            raise ValueError('RUNTIME_ARGUMENT_INVALID')
        return value


class ServiceQueryError(ValueError):
    """只携带固定错误码，不回显 ID、游标或数据库异常。"""
    def __init__(self, code: str):
        """Args:
            code：服务发现领域错误码。
        """
        super().__init__(code)
        self.code = code


def _cursor(owner_id: int, conversation_id: int, sequence: int, runtime_id: str) -> str:
    """Args:
        owner_id：当前 Owner。
        conversation_id：当前会话。
        sequence：已返回末项的登记顺序。
        runtime_id：同顺序的稳定第二排序键。
    """
    raw = json.dumps([1, current_epoch(), owner_id, conversation_id, sequence, runtime_id], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _decode(cursor: str, owner_id: int, conversation_id: int) -> tuple[int, str]:
    """位置游标不授予权限；跨会话/身份拒绝，重启或换 World 后重新查询。

    Args:
        cursor：模型返回的不透明位置。
        owner_id：已经鉴权的 Owner。
        conversation_id：宿主会话。
    """
    try:
        if len(cursor) > 512:
            raise ValueError()
        version, epoch, uid, cid, sequence, runtime_id = json.loads(base64.b64decode(
            cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if (type(version) is not int or version != 1 or type(uid) is not int or uid != owner_id
            or type(cid) is not int or cid != conversation_id or type(sequence) is not int or not 0 < sequence <= 2**63 - 1
            or not isinstance(runtime_id, str) or not re.fullmatch(r'[a-f0-9]{32}', runtime_id)):
            raise ValueError()
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise ServiceQueryError('RUNTIME_QUERY_CURSOR_INVALID') from None
    if epoch != current_epoch():
        raise ServiceQueryError('RUNTIME_QUERY_CURSOR_EXPIRED')
    return sequence, runtime_id


async def query_status(session, *, owner_id: int, conversation_id: int, runtime_id: str | None,
                       cursor: str | None, limit: int) -> dict:
    """仅读取登记的必要列，绝不加载完整日志密文或访问文件/进程。

    Args:
        session：已核验当前 Agent 身份的数据库会话。
        owner_id：经授权 Owner。
        conversation_id：当前会话，匹配存活关联而非仅历史快照。
        runtime_id：指定实例或 None。
        cursor：列表续页位置。
        limit：已校验的每页上限。
    """
    statement = select(RuntimeEntry.id, RuntimeEntry.sequence, RuntimeEntry.state, RuntimeEntry.port,
        RuntimeEntry.health_code, RuntimeEntry.workspace_id).where(RuntimeEntry.owner_id == owner_id,
        RuntimeEntry.conversation_ref_id == conversation_id, RuntimeEntry.conversation_id == conversation_id)
    if runtime_id is not None:
        row = (await session.execute(statement.where(RuntimeEntry.id == runtime_id))).first()
        if row is None:
            raise ServiceQueryError('RUNTIME_NOT_FOUND')
        return {'runtime_id': row.id, 'state': row.state, 'port': row.port, 'health_code': row.health_code}
    statement = statement.where(RuntimeEntry.kind == 'service', RuntimeEntry.state.in_(ACTIVE))
    if cursor is not None:
        sequence, rid = _decode(cursor, owner_id, conversation_id)
        statement = statement.where(or_(RuntimeEntry.sequence < sequence,
            and_(RuntimeEntry.sequence == sequence, RuntimeEntry.id < rid)))
    rows = (await session.execute(statement.order_by(RuntimeEntry.sequence.desc(), RuntimeEntry.id.desc()).limit(limit + 1))).all()
    selected, more = rows[:limit], len(rows) > limit
    return {'items': [{'runtime_id': row.id, 'state': row.state, 'port': row.port, 'health_code': row.health_code,
                      'workspace_binding_id': row.workspace_id} for row in selected], 'has_more': more,
            'next_cursor': _cursor(owner_id, conversation_id, selected[-1].sequence, selected[-1].id) if more else None}


def create_status_tool(*, execution_id: str, conversation_id: int, role_id: int, user_id: int, description: str):
    """构造只读查询工具；权限在每次调用重新核验，不捕获可写租用。

    Args:
        execution_id：宿主执行身份。
        conversation_id：宿主会话。
        role_id：执行角色。
        user_id：触发者。
        description：和 ContextBuilder 共用的工具说明。
    """
    def error(code: str, *, failed: bool = False) -> str:
        """Args:
            code：固定业务错误。
            failed：查询基础设施故障与正常拒绝分开。
        """
        return f'{FAILED_OUTPUT_PREFIX if failed else REJECTED_OUTPUT_PREFIX} ' + json.dumps({'ok': False, 'error_code': code})

    async def status(runtime_id: str | None = None, cursor: str | None = None, limit: int = 50) -> str:
        """Args:
            runtime_id：可选的指定实例身份。
            cursor：不授予权限的分页位置。
            limit：有界页长。
        """
        try:
            async with SessionLocal() as session:
                if await authorized_execution(session, execution_id=execution_id, conversation_id=conversation_id,
                    role_id=role_id, user_id=user_id, tool_name=TOOL_NAME) is None:
                    return error('WORKSPACE_TOOL_NOT_AVAILABLE')
                value = await query_status(session, owner_id=user_id, conversation_id=conversation_id,
                    runtime_id=runtime_id, cursor=cursor, limit=limit)
            output = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            if len(output.encode()) > OUTPUT_LIMIT:
                return error('RUNTIME_QUERY_FAILED', failed=True)
            return output
        except ServiceQueryError as exc:
            return error(exc.code)
        except Exception:
            # 查询失败绝不伪装成空列表，也不将数据库异常及可能的敏感参数传回模型/日志。
            return error('RUNTIME_QUERY_FAILED', failed=True)

    return StructuredTool.from_function(coroutine=status, name=TOOL_NAME, description=description,
        args_schema=ServiceStatusInput, handle_validation_error=lambda _error: error('RUNTIME_ARGUMENT_INVALID'))
