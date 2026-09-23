"""角色关联、触发者权限和目的地全体成员共享范围的交集。"""
from dataclasses import dataclass
from sqlalchemy import and_, exists, literal, or_, select
from sqlalchemy.orm import aliased
from fastapi import HTTPException

from ..models import AgentExecution, Conversation, ConversationMember, Generation, Role, User


@dataclass(frozen=True)
class MemoryScope:
    conversation_id: int
    role_id: int
    user_id: int
    owner_id: int
    execution_id: str | None = None
    material: dict | None = None
    chain_id: str | None = None
    execution_kind: str | None = None
    world_coordination: bool = False
    world_group_ids: tuple[int, ...] = ()


async def scope_for(session, *, conversation_id, role_id, user_id, execution_id=None, tool_name=None, material=None):
    """所有身份来自认证/实际 execution；源引用和查询参数不能替换身份。"""
    from ..context.access import require_context_access
    from ..context.domain import ContextBuildError
    try:
        await require_context_access(session, conversation_id=conversation_id, role_id=role_id, user_id=user_id)
    except ContextBuildError:
        raise HTTPException(404, 'MEMORY_NOT_AVAILABLE') from None
    conversation = await session.get(Conversation, conversation_id)
    role = await session.get(Role, role_id)
    user = await session.get(User, user_id)
    if user is None or role.created_by != conversation.created_by:
        raise HTTPException(404, 'MEMORY_NOT_AVAILABLE')
    execution = None
    if execution_id:
        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
        generation = await session.get(Generation, execution.generation_id) if execution else None
        from ..workflows.allocations import allowed
        if (not execution or execution.conversation_id != conversation_id or execution.role_id != role_id
            or execution.status != 'running' or not generation or generation.status != 'running'
            or generation.stop_requested_at is not None or tool_name not in (role.builtin_tools_json or [])
            or not await allowed(session, execution_id, tool_name)):
            raise HTTPException(404, 'MEMORY_NOT_AVAILABLE')
        material = material or (execution.context_snapshot_json or {}).get('material')
        if not material or material.get('current_message_id') is None:
            raise HTTPException(404, 'MEMORY_NOT_AVAILABLE')
    elif not user.is_owner or conversation.created_by != user_id:
        raise HTTPException(404, 'MEMORY_NOT_AVAILABLE')
    world_group_ids = ()
    if conversation.purpose == 'world_coord' and execution:
        from ..world_orchestrator.service import authorized
        try:
            grant = await authorized(session, execution_id)
            if grant.task_id:
                from ..world_orchestrator.tasks import authorized as task_authorized
                _, task = await task_authorized(session, execution_id)
                world_group_ids = tuple(task.scope_json['group_ids'])
        except ContextBuildError:
            raise HTTPException(404, 'MEMORY_NOT_AVAILABLE') from None
    return MemoryScope(conversation_id, role_id, user_id, conversation.created_by, execution_id, material,
        execution.chain_id if execution else None, execution.execution_kind if execution else None,
        conversation.purpose == 'world_coord', world_group_ids)


def visible_messages(scope, cid, message_id, chain_id):
    """主动检索不读取当前输入自身或本执行开始之后的新消息；关联会话仍现场鉴权。"""
    if scope.material is None:
        return literal(True)
    current = scope.material['current_message_id']
    history = message_id < current
    if scope.execution_kind == 'group_role':
        history = or_(history, and_(message_id > current, chain_id == scope.chain_id))
    return or_(cid != scope.conversation_id, and_(history, message_id <= scope.material['visible_through_message_id']))


def readable_conversations(scope: MemoryScope, *, related=True):
    """源会话必须包含目的地的每一位成员，不能把单聊资料经角色泄露给群聊。"""
    destination = aliased(ConversationMember)
    source = aliased(ConversationMember)
    matched = exists(select(source.id).where(source.conversation_id == Conversation.id,
        source.member_type == destination.member_type, source.member_id == destination.member_id)
        .correlate(Conversation, destination))
    missing = exists(select(destination.id).where(destination.conversation_id == scope.conversation_id, ~matched)
        .correlate(Conversation))
    associated = exists(select(ConversationMember.id).where(ConversationMember.conversation_id == Conversation.id,
        ConversationMember.member_type == 'role', ConversationMember.member_id == scope.role_id).correlate(Conversation))
    user_member = exists(select(ConversationMember.id).where(ConversationMember.conversation_id == Conversation.id,
        ConversationMember.member_type == 'user', ConversationMember.member_id == scope.user_id).correlate(Conversation))
    if scope.world_coordination:
        # 世界执行的岗位授权只覆盖冻结的目标群；普通人格关系不能导入其他私人会话。
        return select(Conversation.id).where(Conversation.created_by == scope.owner_id, Conversation.deleted_at.is_(None), user_member,
            or_(Conversation.id == scope.conversation_id, and_(Conversation.id.in_(scope.world_group_ids),
                Conversation.type == 'group', Conversation.purpose == 'chat')) if related else Conversation.id == scope.conversation_id)
    return select(Conversation.id).where(Conversation.created_by == scope.owner_id, Conversation.deleted_at.is_(None),
        Conversation.purpose == 'chat',
        associated, user_member, ~missing, True if related else Conversation.id == scope.conversation_id)


async def require_source(session, scope, cid):
    if await session.scalar(readable_conversations(scope).where(Conversation.id == cid)) is None:
        raise HTTPException(404, 'MEMORY_SOURCE_NOT_FOUND')
