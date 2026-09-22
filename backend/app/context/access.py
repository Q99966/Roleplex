"""材料查询和每次模型派发的当前会话授权。"""
from sqlalchemy import select

from ..models import Conversation, ConversationMember, Role
from .domain import ContextBuildError


async def require_context_access(session, *, conversation_id: int, role_id: int, user_id: int | None):
    """当前角色及触发者都仍为成员；不把创建执行时的授权冻结为永久权限。"""
    conversation = await session.scalar(select(Conversation.id).where(
        Conversation.id == conversation_id, Conversation.deleted_at.is_(None)))
    role = await session.scalar(select(Role.id).where(Role.id == role_id,
        Role.active.is_(True), Role.deleted_at.is_(None)))
    members = set((await session.execute(select(ConversationMember.member_type, ConversationMember.member_id).where(
        ConversationMember.conversation_id == conversation_id))).all())
    if conversation is None or ('user', user_id) not in members or ('role', role_id) not in members:
        raise ContextBuildError('CONVERSATION_NOT_FOUND')
    if role is None:
        raise ContextBuildError('ROLE_NOT_AVAILABLE')
