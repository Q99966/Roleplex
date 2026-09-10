"""三层配额和回收门槛：只用短事务，不把进程运行期变成全库锁。"""
from datetime import datetime, timezone
import logging
import uuid

from sqlalchemy import func, or_, select, update

from ..config.logging import current_logging_session
from ..db import SessionLocal, with_locked_retry
from ..models import AgentExecution, Conversation, InstanceSettings, WorkspaceBinding
from ..workspaces.commands import WorkspaceCommandError
from .models import CleanupItem, CleanupOperation, RuntimeEntry, RuntimeGate

ACTIVE = ('pending', 'starting', 'waiting_ready', 'ready', 'unhealthy', 'running', 'stopping', 'cleanup_required')
TERMINAL = ('stopped', 'exited', 'failed', 'rejected', 'expired', 'interrupted')
logger = logging.getLogger('roleplex.runtime')


class RuntimeRejected(WorkspaceCommandError):
    """稳定的运行策略拒绝，不携带脚本、环境或异常原文。"""
    def __init__(self, code: str):
        """Args:
            code：已登记的稳定错误码。
        """
        super().__init__(code)
        self.code = code


def instance_id() -> str:
    """沿用日志进程身份，不建立另一套业务 Trace。"""
    session = current_logging_session()
    return session.process_instance_id if session else 'uninitialized'


async def gate(session) -> int:
    """通过标准 UPDATE 串行化配额与范围修改；调用者必须马上提交短事务。

    Args:
        session：本次操作的独立数据库会话。
    """
    return await session.scalar(update(RuntimeGate).where(RuntimeGate.id == 1)
        .values(sequence=RuntimeGate.sequence + 1).returning(RuntimeGate.sequence))


def scope_filter(scope: str, scope_id: int):
    """Args:
        scope：已校验的 World/工作区/会话范围。
        scope_id：范围身份；World 为 0。
    """
    if scope == 'world':
        return RuntimeEntry.id.is_not(None)
    if scope == 'workspace':
        return RuntimeEntry.workspace_id == scope_id
    if scope == 'conversation':
        return RuntimeEntry.conversation_id == scope_id
    raise RuntimeRejected('RUNTIME_SCOPE_INVALID')


async def _config(session, scope: str, scope_id: int):
    """读取持久配置；不存在时拒绝，不猜测资源归属。

    Args:
        session：当前短事务。
        scope：配置范围。
        scope_id：范围身份。
    """
    model = {'world': InstanceSettings, 'workspace': WorkspaceBinding, 'conversation': Conversation}.get(scope)
    if model is None or (scope == 'world' and scope_id != 0):
        raise RuntimeRejected('RUNTIME_SCOPE_INVALID')
    row = await session.get(model, 1 if scope == 'world' else scope_id)
    if row is None:
        raise RuntimeRejected('RUNTIME_NOT_FOUND')
    return row


async def quota_view(scope: str, scope_id: int) -> dict:
    """读取实际用量与配置，停止中和未知回收状态仍占名额。

    Args:
        scope：已授权范围。
        scope_id：资源身份。
    """
    async with SessionLocal() as session:
        config = await _config(session, scope, scope_id)
        used = await session.scalar(select(func.count()).select_from(RuntimeEntry).where(
            scope_filter(scope, scope_id), RuntimeEntry.state.in_(ACTIVE)))
        return {'scope': scope, 'scope_id': scope_id, 'limit': config.process_limit,
            'revision': config.process_limit_version, 'used': used,
            **({'services_enabled': config.services_enabled} if scope == 'workspace' else {})}


async def configure(scope: str, scope_id: int, *, limit: int, expected_revision: int, actor_id: int,
                    services_enabled: bool | None = None) -> dict:
    """版本化修改上限；降低上限不自动回收已有任务。

    Args:
        scope：已授权配置范围。
        scope_id：资源身份。
        limit：新的正整数上限。
        expected_revision：客户端所见配置版本。
        actor_id：已通过授权的 Owner。
        services_enabled：仅工作区允许使用的独立服务开关。
    """
    if type(limit) is not int or not 1 <= limit <= 2**31 - 1:
        raise RuntimeRejected('RUNTIME_LIMIT_INVALID')
    async def write():
        """在门槛锁内比较版本并修改配置。"""
        async with SessionLocal() as session:
            await gate(session)
            row = await _config(session, scope, scope_id)
            if row.process_limit_version != expected_revision:
                raise RuntimeRejected('RUNTIME_REVISION_CONFLICT')
            old = row.process_limit
            row.process_limit = limit
            row.process_limit_version += 1
            if services_enabled is not None:
                if scope != 'workspace':
                    raise RuntimeRejected('RUNTIME_SCOPE_INVALID')
                row.services_enabled = services_enabled
            await session.commit()
            return old
    old = await with_locked_retry(write)
    logger.info('runtime.quota_changed', extra={'scope': scope, 'scope_id': scope_id, 'actor_id': actor_id,
        'old_limit': old, 'new_limit': limit, 'status': 'success'})
    return await quota_view(scope, scope_id)


async def _admission(session, cid: int, wid: int, *, adding: bool):
    """复核三层配额和所有未完成清理门槛。

    Args:
        session：门槛写锁内会话。
        cid：目标会话。
        wid：目标工作区。
        adding：预留前加一，已有实例启动复核不重复占位。
    """
    closing = await session.scalar(select(RuntimeGate.closing).where(RuntimeGate.id == 1))
    blocked = await session.scalar(select(CleanupOperation.id).where(CleanupOperation.state.in_(('running', 'prepared', 'failed')),
        or_(CleanupOperation.scope == 'world', (CleanupOperation.scope == 'conversation') & (CleanupOperation.scope_id == cid),
            (CleanupOperation.scope == 'workspace') & (CleanupOperation.scope_id == wid))).limit(1))
    if closing or blocked:
        raise RuntimeRejected('RUNTIME_SCOPE_CLOSING')
    conversation = await session.get(Conversation, cid)
    workspace = await session.get(WorkspaceBinding, wid)
    if not conversation or conversation.deleted_at is not None or conversation.workspace_binding_id != wid or not workspace or not workspace.active:
        raise RuntimeRejected('RUNTIME_NOT_FOUND')
    for scope, scope_id in [('world', 0), ('workspace', wid), ('conversation', cid)]:
        config = await _config(session, scope, scope_id)
        used = await session.scalar(select(func.count()).select_from(RuntimeEntry).where(scope_filter(scope, scope_id), RuntimeEntry.state.in_(ACTIVE)))
        if used + int(adding) > config.process_limit:
            raise RuntimeRejected(f'RUNTIME_{scope.upper()}_LIMIT')


async def reserve(*, owner_id: int, conversation_id: int, workspace_id: int, execution_id: str, role_id: int,
                  tool_call_id: str, tool_name: str, kind: str, port: int | None = None, runtime_id: str | None = None) -> RuntimeEntry:
    """原子预留三层名额和端口；输入身份只能来自已授权执行层。

    Args:
        owner_id：原始触发 Owner。
        conversation_id：来源会话。
        workspace_id：捕获的工作区。
        execution_id：来源 execution。
        role_id：来源角色。
        tool_call_id：防腐层的真实调用身份。
        tool_name：固定工具名。
        kind：command 或 service。
        port：后台服务声明端口，普通命令为空。
        runtime_id：宿主预先建立的交接身份，默认生成随机资源 ID。
    """
    async def write():
        """短事务中的竞争以数据库顺序为准，不只依赖 Python 锁。"""
        async with SessionLocal() as session:
            sequence = await gate(session)
            duplicate = await session.scalar(select(RuntimeEntry.id).where(RuntimeEntry.execution_id == execution_id, RuntimeEntry.tool_call_id == tool_call_id))
            if duplicate:
                raise RuntimeRejected('RUNTIME_REQUEST_CONFLICT')
            await _admission(session, conversation_id, workspace_id, adding=True)
            if port is not None and await session.scalar(select(RuntimeEntry.id).where(RuntimeEntry.leased_port == port)):
                raise RuntimeRejected('RUNTIME_PORT_BUSY')
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
            row = RuntimeEntry(id=runtime_id or uuid.uuid4().hex, sequence=sequence, owner_id=owner_id,
                conversation_id=conversation_id, conversation_ref_id=conversation_id, workspace_id=workspace_id, execution_id=execution_id,
                chain_id=execution.chain_id if execution else None, role_id=role_id, tool_call_id=tool_call_id,
                tool_name=tool_name, kind=kind, state='pending', process_instance_id=instance_id(),
                port=port, leased_port=port, created_at=datetime.now(timezone.utc))
            session.add(row)
            await session.commit()
            return row
    row = await with_locked_retry(write)
    logger.info('runtime.reserved', extra=links(row))
    return row


def links(row: RuntimeEntry) -> dict:
    """只提取允许记录的归属身份，不序列化任意运行对象。

    Args:
        row：持久运行实例。
    """
    return {'runtime_id': row.id, 'conversation_id': row.conversation_id, 'workspace_binding_id': row.workspace_id,
        'execution_id': row.execution_id, 'chain_id': row.chain_id, 'role_id': row.role_id,
        'tool_call_id': row.tool_call_id, 'runtime_state': row.state, 'pid': row.pid, 'process_birth': row.birth}


async def get(runtime_id: str) -> RuntimeEntry | None:
    """Args:
        runtime_id：已授权或宿主登记的运行身份。
    """
    async with SessionLocal() as session:
        return await session.get(RuntimeEntry, runtime_id)


async def check_start(runtime_id: str) -> None:
    """启动/批准前重查最新配额和关闭门槛。

    Args:
        runtime_id：已经预留的实例。
    """
    async def check():
        """短事务重查，退出后仍需由宿主交接处理并发取消。"""
        async with SessionLocal() as session:
            await gate(session)
            row = await session.get(RuntimeEntry, runtime_id)
            if row is None or row.state not in ('pending', 'starting'):
                raise RuntimeRejected('RUNTIME_NOT_STARTABLE')
            await _admission(session, row.conversation_id, row.workspace_id, adding=False)
            await session.commit()
    await with_locked_retry(check)


async def change(runtime_id: str, **values) -> RuntimeEntry:
    """单一宿主更新状态；终态不被迟到回调复活。

    Args:
        runtime_id：宿主拥有的实例。
        values：内部状态字段，禁止来自未验证 HTTP 对象。
    """
    async def write():
        """按 revision 更新，冲突不能静默覆盖。"""
        async with SessionLocal() as session:
            row = await session.get(RuntimeEntry, runtime_id)
            if row is None:
                raise RuntimeRejected('RUNTIME_NOT_FOUND')
            if row.state in TERMINAL:
                return row
            result = await session.execute(update(RuntimeEntry).where(RuntimeEntry.id == runtime_id, RuntimeEntry.revision == row.revision)
                .values(**values, revision=row.revision + 1).execution_options(synchronize_session=False))
            if not result.rowcount:
                raise RuntimeRejected('RUNTIME_REVISION_CONFLICT')
            await session.commit()
            await session.refresh(row)
            return row
    row = await with_locked_retry(write)
    if 'state' in values:
        logger.info('runtime.state_changed', extra=links(row))
    return row


async def finish(runtime_id: str, state: str, *, verified: bool = False, **values) -> RuntimeEntry:
    """只有确认从未启动或回收完成后才释放端口和名额。

    Args:
        runtime_id：当前实例。
        state：已登记终态。
        verified：宿主是否已经验证进程回收。
        values：实测退出/错误字段。
    """
    row = await get(runtime_id)
    if state not in TERMINAL or (row and row.pid is not None and not verified):
        raise RuntimeRejected('RUNTIME_CLEANUP_UNCONFIRMED')
    return await change(runtime_id, state=state, leased_port=None, ended_at=datetime.now(timezone.utc), **values)


async def begin_cleanup(scope: str, scope_id: int, *, reason: str, actor_id: int | None):
    """冻结精确范围，逐条持久化完整目标，后续停止不能漏掉启动交接。

    Args:
        scope：会话、工作区或 World。
        scope_id：范围身份。
        reason：已登记生命周期原因。
        actor_id：触发 Owner，系统退出为空。
    """
    async def write():
        """配额预留和关闭门槛使用同一个数据库串行化点。"""
        async with SessionLocal() as session:
            await gate(session)
            operation = CleanupOperation(id=uuid.uuid4().hex, scope=scope, scope_id=scope_id, reason=reason,
                actor_id=actor_id, process_instance_id=instance_id(), state='running', target_count=0, created_at=datetime.now(timezone.utc))
            rows = list((await session.scalars(select(RuntimeEntry).where(scope_filter(scope, scope_id), RuntimeEntry.state.in_(ACTIVE))
                .order_by(RuntimeEntry.sequence.desc(), RuntimeEntry.id))).all())
            operation.target_count = len(rows)
            session.add(operation)
            items = [CleanupItem(operation_id=operation.id, runtime_id=row.id, ordinal=i, state='pending') for i, row in enumerate(rows)]
            session.add_all(items)
            await session.commit()
            return operation, items, rows
    operation, items, rows = await with_locked_retry(write)
    try:
        logger.info('runtime.cleanup_started', extra={'cleanup_id': operation.id, 'scope': scope, 'scope_id': scope_id,
            'reason': reason, 'actor_id': actor_id, 'target_count': len(items)})
        for item, row in zip(items, rows):
            logger.info('runtime.cleanup_target_selected', extra={**links(row), 'cleanup_id': operation.id,
                'target_ordinal': item.ordinal, 'scope': scope, 'scope_id': scope_id, 'reason': reason})
    except Exception:
        async with SessionLocal() as session:
            await session.execute(update(CleanupOperation).where(CleanupOperation.id == operation.id).values(state='failed'))
            await session.commit()
        raise RuntimeRejected('RUNTIME_AUDIT_UNAVAILABLE') from None
    return operation, items


async def end_cleanup(operation_id: str, *, success: bool) -> None:
    """记录批次终态；失败门槛继续封闭，不能当作已释放。

    Args:
        operation_id：已冻结的操作身份。
        success：清单对账与资源变更是否均成功。
    """
    async def write():
        """只修改对应批次，不覆盖其他范围的回收。"""
        async with SessionLocal() as session:
            await gate(session)
            operation = await session.get(CleanupOperation, operation_id)
            reconciled = []
            if success and operation:
                previous = list((await session.scalars(select(CleanupOperation).where(
                    CleanupOperation.scope == operation.scope, CleanupOperation.scope_id == operation.scope_id,
                    CleanupOperation.id != operation_id, CleanupOperation.state == 'failed'))).all())
                for row in previous:
                    row.state = 'superseded'
                    reconciled.append(row.id)
            await session.execute(update(CleanupOperation).where(CleanupOperation.id == operation_id)
                .values(state='complete' if success else 'failed', ended_at=datetime.now(timezone.utc)))
            await session.commit()
            return reconciled
    reconciled = await with_locked_retry(write)
    for previous in reconciled:
        logger.info('runtime.cleanup_reconciled', extra={'cleanup_id': previous, 'superseded_by': operation_id, 'previous_state': 'failed', 'status': 'success'})
    logger.info('runtime.cleanup_completed' if success else 'runtime.cleanup_failed',
        extra={'cleanup_id': operation_id, 'status': 'success' if success else 'failed'})
