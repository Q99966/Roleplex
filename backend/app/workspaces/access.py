"""文件执行与只读服务发现共用身份边界，资源写入门槛由各操作另行复核。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import WORKSPACE_FILE_TOOLS

from ..models import AgentExecution, Conversation, ConversationMember, Generation, Role, User


async def authorized_member(session: AsyncSession, *, conversation_id: int, role_id: int,
                            user_id: int | None, tool_name: str, require_tool: bool = True) -> tuple[Conversation, Role] | None:
    """核验 Owner、角色能力和双方成员关系；群聊仅允许原生文件工具，不据此授予执行权限。

    Args:
        session：调用方的短数据库会话。
        conversation_id：宿主绑定的会话。
        role_id：宿主执行角色。
        user_id：原始触发用户，不接受模型参数覆盖。
        tool_name：本次真实工具名。
        require_tool：默认检查工具开关；仅拒绝诊断可先核验身份，再单独报告能力变化，不能因此授予执行权限。
    """
    if user_id is None:
        return None
    conversation = await session.get(Conversation, conversation_id)
    role = await session.get(Role, role_id)
    user = await session.get(User, user_id)
    if (conversation is None or conversation.type not in ('single', 'group')
        or (conversation.type == 'group' and tool_name not in WORKSPACE_FILE_TOOLS)
        or conversation.created_by != user_id or conversation.deleted_at is not None
        or role is None or not role.active or role.deleted_at is not None or role.created_by != user_id
        or (require_tool and tool_name not in (role.builtin_tools_json or [])) or user is None or not user.is_owner):
        return None
    for kind, member_id in [('user', user_id), ('role', role_id)]:
        if await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == conversation_id,
            ConversationMember.member_type == kind, ConversationMember.member_id == member_id)) is None:
            return None
    return conversation, role


async def authorized_execution(session: AsyncSession, *, execution_id: str, conversation_id: int,
                               role_id: int, user_id: int | None, tool_name: str, require_tool: bool = True) -> tuple[Conversation, Role] | None:
    """在实际调用时额外核验 execution/generation 未终结或取消。

    Args:
        session：本次复核的数据库会话。
        execution_id：调度器创建的持久身份。
        conversation_id：宿主会话。
        role_id：宿主角色。
        user_id：触发 Owner。
        tool_name：本次实际工具名。
        require_tool：拒绝诊断可延后能力检查，调用者仍必须检查后才能返回可执行服务。
    """
    actor = await authorized_member(session, conversation_id=conversation_id, role_id=role_id, user_id=user_id, tool_name=tool_name,
        require_tool=require_tool)
    if actor is None:
        return None
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id,
        AgentExecution.conversation_id == conversation_id, AgentExecution.role_id == role_id,
        AgentExecution.execution_kind == ('single' if actor[0].type == 'single' else 'group_role'), AgentExecution.status == 'running'))
    generation = await session.get(Generation, execution.generation_id) if execution else None
    if (generation is None or generation.conversation_id != conversation_id or generation.status != 'running'
        or generation.stop_requested_at is not None):
        return None
    return actor
