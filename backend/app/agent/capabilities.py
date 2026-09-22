"""统一本次工具能力：配置预览、上下文预算和实际工厂共享一个已解析快照。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from langchain_core.utils.function_calling import convert_to_openai_tool

from sqlalchemy import select

from ..context.domain import ContextBuildError
from ..context.fingerprint import stable_hash
from ..models import AgentExecution, Conversation, Role
from .tools import classify_tool


async def role_tool_policy(session, *, conversation: Conversation, role: Role, triggered_by_user_id: int | None) -> dict:
    """解析角色可用的原生能力，不依赖某一次 allocation；新类别在这里接入自身资源规则。"""
    from ..workspaces.tools import workspace_tool_policy
    from ..memory.tools import specs
    base = await workspace_tool_policy(session, conversation=conversation, role=role, triggered_by_user_id=triggered_by_user_id)
    memory = specs(role.builtin_tools_json or [])
    if not memory:
        return base
    from ..context.access import require_context_access
    try:
        await require_context_access(session, conversation_id=conversation.id, role_id=role.id, user_id=triggered_by_user_id)
    except ContextBuildError:
        memory = []
    return {**base, 'memory_version': 1, 'exposed_tools': [*base['exposed_tools'], *memory]}


@dataclass(frozen=True)
class ExecutionCapabilities:
    """服务端绑定的能力快照；公开部分不含目录、凭据或任意模型提供的身份。"""
    conversation_id: int
    role_id: int
    triggered_by_user_id: int | None
    execution_id: str | None
    policy: dict
    sources: dict[str, str]
    triggered_by_owner: bool

    @property
    def fingerprint(self) -> str:
        return stable_hash({'policy': self.policy, 'triggered_by_owner': self.triggered_by_owner})

    def view(self) -> dict:
        tools = [{**deepcopy(spec), 'source': self.sources[spec['name']],
            'danger': classify_tool(spec['name'])} for spec in self.policy['exposed_tools']]
        return {'fingerprint': self.fingerprint, 'tools': tools}

    def receipt(self) -> dict:
        return {'fingerprint': self.fingerprint,
            'tools': [{'name': item['name'], 'fingerprint': stable_hash(item)} for item in self.policy['exposed_tools']]}


async def resolve_capabilities(session, *, conversation: Conversation, role: Role,
                               triggered_by_user_id: int | None, execution_id: str | None = None) -> ExecutionCapabilities:
    """结合角色资源与本次任务授权；未实现的 Skills/MCP 配置不产生虚假工具。"""
    base = await role_tool_policy(session, conversation=conversation, role=role, triggered_by_user_id=triggered_by_user_id)
    if execution_id:
        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
        if execution is None or execution.conversation_id != conversation.id or execution.role_id != role.id:
            raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
    from ..workflows.allocations import policy
    resolved = await policy(session, execution_id, base)
    # 来源取自实际解析分支，不能按模型或外部服务提供的名称前缀猜测权限类别。
    source_names = {tool['name'] for tool in base['exposed_tools']}
    from ..memory.tools import NAMES
    sources = {tool['name']: ('memory' if tool['name'] in NAMES else 'workspace') if tool['name'] in source_names else 'workflow'
        for tool in resolved['exposed_tools']}
    return ExecutionCapabilities(conversation.id, role.id, triggered_by_user_id, execution_id, deepcopy(resolved), sources, triggered_by_user_id == role.created_by)


async def create_execution_tools(session, capabilities: ExecutionCapabilities, *, role: Role, allow_dangerous: bool, material=None):
    """物化相同快照，当前权限有变则拒绝派发；各工具执行时仍做原有逐次授权。"""
    if not capabilities.execution_id:
        raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
    conversation = await session.get(Conversation, capabilities.conversation_id)
    if conversation is None or role.id != capabilities.role_id:
        raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
    current = await resolve_capabilities(session, conversation=conversation, role=role,
        triggered_by_user_id=capabilities.triggered_by_user_id, execution_id=capabilities.execution_id)
    if current.receipt() != capabilities.receipt():
        raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
    from ..workspaces.tools import create_workspace_tools
    from ..workflows.coordination import create_control_tools
    tools = []
    if 'workspace' in capabilities.sources.values():
        tools.extend(await create_workspace_tools(session, execution_id=capabilities.execution_id,
            conversation_id=conversation.id, role=role, triggered_by_user_id=capabilities.triggered_by_user_id,
            allow_dangerous=allow_dangerous))
    if 'workflow' in capabilities.sources.values():
        tools.extend(await create_control_tools(session, execution_id=capabilities.execution_id))
    if 'memory' in capabilities.sources.values():
        from ..memory.tools import create_tools
        tools.extend(await create_tools(capabilities, material))
    available = {tool.name: tool for tool in tools}
    names = [spec['name'] for spec in capabilities.policy['exposed_tools']]
    if set(available) != set(names) or len(available) != len(tools):
        raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
    # 描述来自同一权限快照，不能让工厂推荐预算/预览中没有暴露的工具。
    bound = []
    for spec in capabilities.policy['exposed_tools']:
        tool = available[spec['name']].model_copy(update={'description': spec['description']})
        if convert_to_openai_tool(tool)['function']['parameters'] != spec['parameters']:
            raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
        bound.append(tool)
    return bound
