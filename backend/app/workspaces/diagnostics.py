"""已授权原生修改的拒绝诊断；完整信息只供模型与 Owner 私有详情。"""
from dataclasses import dataclass
import json

from sqlalchemy import and_, case, func, or_, select

from .files import WorkspaceFileError, WorkspaceFileService
from ..runtime.models import RuntimeEntry, RuntimeGate, CleanupOperation
from ..runtime.registry import ACTIVE

DENIALS = {
    'WORKSPACE_SERVICE_ACTIVE': ('service_active', '同一工作区存在尚未结束的托管服务，按现行策略本项未写入。'),
    'WORKSPACE_SERVICE_STOPPING': ('service_stopping', '服务正在停止或回收，本项未写入；停止请求发出不等于回收完成。'),
    'WORKSPACE_CLEANUP_REQUIRED': ('cleanup_required', '存在回收未确认的服务或失败的清理操作，本项未写入；不能仅凭 PID 消失放行。'),
    'WORKSPACE_SCOPE_CLOSING': ('scope_closing', 'World 正在关闭，本项未写入。'),
    'WORKSPACE_SCOPE_CLEANUP': ('scope_cleanup', '相关范围正在执行清理操作，本项未写入。'),
    'WORKSPACE_TOOL_CAPABILITY_CHANGED': ('capability_changed', '角色工具权限或工作区能力开关已变化，本项未写入。'),
    'WORKSPACE_BINDING_CHANGED': ('binding_changed', '会话绑定或工作区根已变化，本项未写入，旧执行快照不能继续使用。'),
    'WORKSPACE_LEASE_UNAVAILABLE': ('lease_unavailable', '当前执行的工作区租用已不可用，本项未写入。'),
    'WORKSPACE_UNAVAILABLE': ('workspace_unavailable', '工作区目录当前不可用，本项未写入。'),
}
DENIAL_CODES = frozenset(DENIALS)


@dataclass(frozen=True)
class AccessDecision:
    """把可用服务与拒绝事实分开；不以 None 混同所有失败原因。"""
    service: WorkspaceFileService | None = None
    error_code: str = 'WORKSPACE_TOOL_NOT_AVAILABLE'
    diagnostic: dict | None = None


class AccessRejected(WorkspaceFileError):
    """仅在单项写入前抛出，携带有界私有诊断，不把它写入异常文本。"""
    def __init__(self, decision: AccessDecision):
        """Args:
            decision：服务端已完成的可用性判定。
        """
        super().__init__(decision.error_code)
        self.diagnostic = decision.diagnostic


def denied(code: str, scope: str, *, query_available: bool = False, services: list | None = None,
           truncated: bool = False, other: bool | None = None) -> AccessDecision:
    """生成固定文案与明确下一步，不返回路径、他人 ID 或自动停止指令。

    Args:
        code：已定稿拒绝码。
        scope：本次门槛的范围。
        query_available：本次真实工具集和当前权限都允许状态查询。
        services：最多三个本会话阻塞服务的必要投影；None 表示未授权或未查询。
        truncated：是否还有本会话服务未列出。
        other：是否存在非本会话可管理的工作区服务，未判断时为 None。
    """
    reason, message = DENIALS[code]
    if reason in {'capability_changed', 'binding_changed', 'lease_unavailable', 'workspace_unavailable'}:
        steps = ['请 Owner 检查角色权限、工作区配置和当前绑定，再以有效执行重新发起本项；不要改用其他入口绕过限制。']
        query_available = False
    elif reason in {'cleanup_required', 'scope_closing', 'scope_cleanup'}:
        steps = ['请 Owner 核查并完成相关回收或关闭操作；缺少回收证明时不要盲目重试写入。']
    else:
        steps = ['协调停止必要服务并确认回收后，重新读取文件版本再重试本项；重启仍需重新审批，不得擅停其他会话服务。']
    if query_available:
        steps.insert(0, '可调用本轮已启用的 workspace_service_status 查询当前会话服务，以实时返回状态为准。')
    elif reason not in {'capability_changed', 'binding_changed', 'lease_unavailable', 'workspace_unavailable'}:
        steps.insert(0, '本轮没有可用的状态查询工具，请 Owner 通过 /ps 查看并协调处理。')
    value = {'version': 1, 'reason': reason, 'scope': scope, 'executed': False, 'message': message,
        'next_steps': steps, 'recommended_tool': 'workspace_service_status' if query_available else None,
        'services': services if query_available else None, 'services_truncated': truncated if query_available else False,
        'other_sessions_blocking': other}
    # 不截断 JSON 或把被删去的服务伪装为完整清单；固定文案和最多三项通常远小于此预算。
    while len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) > 2048 and value['services']:
        value['services'].pop()
        value['services_truncated'] = True
    return AccessDecision(error_code=code, diagnostic=value)


async def mutation_blocker(session, *, workspace_id: int, conversation_id: int, owner_id: int,
                           query_available: bool) -> AccessDecision | None:
    """按严重度检查已授权工作区的运行门槛，不加载任何外会话实例明细。

    Args:
        session：已校验身份/归属的数据库会话。
        workspace_id：本次执行绑定的工作区。
        conversation_id：宿主会话。
        owner_id：已授权 Owner。
        query_available：当前角色权限与本轮暴露工具的交集。
    """
    own = and_(RuntimeEntry.owner_id == owner_id, RuntimeEntry.conversation_ref_id == conversation_id,
        RuntimeEntry.conversation_id == conversation_id)
    own_group = case((own, 1), else_=0)
    conditions = (RuntimeEntry.workspace_id == workspace_id, RuntimeEntry.kind == 'service', RuntimeEntry.state.in_(ACTIVE))
    groups = (await session.execute(select(RuntimeEntry.state, own_group.label('own'), func.count().label('count'))
        .where(*conditions).group_by(RuntimeEntry.state, own_group))).all()
    scopes = or_(CleanupOperation.scope == 'world',
        and_(CleanupOperation.scope == 'workspace', CleanupOperation.scope_id == workspace_id),
        and_(CleanupOperation.scope == 'conversation', CleanupOperation.scope_id == conversation_id))
    operations = (await session.execute(select(CleanupOperation.scope, CleanupOperation.state)
        .where(scopes, CleanupOperation.state.in_(('running', 'prepared', 'failed')))
        .group_by(CleanupOperation.scope, CleanupOperation.state))).all()
    priority = {'world': 0, 'workspace': 1, 'conversation': 2}
    failed = sorted((row.scope for row in operations if row.state == 'failed'), key=priority.get)
    code, scope = None, 'workspace'
    if failed:
        code, scope = 'WORKSPACE_CLEANUP_REQUIRED', failed[0]
    elif any(row.state == 'cleanup_required' for row in groups):
        code = 'WORKSPACE_CLEANUP_REQUIRED'
    elif await session.scalar(select(RuntimeGate.closing).where(RuntimeGate.id == 1)):
        code, scope = 'WORKSPACE_SCOPE_CLOSING', 'world'
    elif operations:
        code, scope = 'WORKSPACE_SCOPE_CLEANUP', min((row.scope for row in operations), key=priority.get)
    elif any(row.state == 'stopping' for row in groups):
        code = 'WORKSPACE_SERVICE_STOPPING'
    elif groups:
        code = 'WORKSPACE_SERVICE_ACTIVE'
    if code is None:
        return None
    services = None
    own_count = sum(row.count for row in groups if row.own)
    if query_available:
        rows = (await session.execute(select(RuntimeEntry.id, RuntimeEntry.state).where(*conditions, own)
            .order_by(RuntimeEntry.sequence.desc(), RuntimeEntry.id.desc()).limit(3))).all()
        services = [{'runtime_id': row.id, 'state': row.state} for row in rows]
    return denied(code, scope, query_available=query_available, services=services,
        truncated=own_count > len(services or []), other=any(not row.own for row in groups))
