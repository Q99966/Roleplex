"""类型工具接入同一能力快照/工厂/实际准入，未显式启用的工具不暴露。"""
import json
from langchain_core.tools import StructuredTool
from sqlalchemy import select
from fastapi import HTTPException

from ..db import SessionLocal
from ..models import AgentExecution, Conversation, InstanceSettings, Role, WorkflowBudget, WorldCoordinationGrant, WorldTaskChild, WorldTypeState
from ..context.access import require_context_access
from ..context.domain import ContextBuildError
from ..agent.tool_definitions import tool_definition
from ..agent.tools import guard_tools, REJECTED_OUTPUT_PREFIX, FAILED_OUTPUT_PREFIX
from . import service
from .contracts import WorldToolDefinition


async def definitions(session, conversation, role, uid, execution_id=None):
    descriptor = service.descriptor()
    if descriptor.resolve_tools is None:
        return [], None, None
    await require_context_access(session, conversation_id=conversation.id, role_id=role.id, user_id=uid)
    state = await session.get(WorldTypeState, 1)
    if not state or state.status != 'ready':
        return [], None, None
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id)) if execution_id else None
    if execution and execution.execution_kind == 'world_coord':
        from ..world_orchestrator.service import authorized
        await authorized(session, execution_id)
    actor = await service.execution_context(session, uid, role_id=role.id,
        conversation_id=conversation.id, execution_id=execution_id, execution_kind=execution.execution_kind if execution else None,
    )
    try:
        values = await descriptor.resolve_tools(session, actor, service.configuration(state))
    except Exception:
        raise ContextBuildError('WORLD_TYPE_CONTEXT_INVALID') from None
    if (not isinstance(values, list) or len(values) > 64 or any(not isinstance(v, WorldToolDefinition) or not v.name.startswith(f'type_{descriptor.id}_') for v in values)
        or len({v.name for v in values}) != len(values)):
        raise ContextBuildError('WORLD_TYPE_CONTEXT_INVALID')
    return [v for v in values if v.name in (role.builtin_tools_json or [])], actor, service.configuration(state)


async def specs(session, conversation, role, uid, execution_id=None):
    values, _, _ = await definitions(session, conversation, role, uid, execution_id)
    return [tool_definition(value.name, value.description, value.args_model) for value in values]


async def create(capabilities, allow_dangerous):
    names = {name for name, kind in capabilities.sources.items() if kind == 'world_type'}
    async with SessionLocal() as session:
        role = await session.get(Role, capabilities.role_id)
        conversation = await session.get(Conversation, capabilities.conversation_id)
        values, _, _ = await definitions(session, conversation, role, capabilities.triggered_by_user_id, capabilities.execution_id)
    result = []
    for definition in values:
        if definition.name not in names:
            continue
        def handler(selected):
            async def call(**kwargs):
                try:
                    async with SessionLocal() as session:
                        from ..workflows.allocations import allowed
                        if not await allowed(session, capabilities.execution_id, selected.name):
                            raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
                        execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == capabilities.execution_id))
                        from ..models import Generation
                        generation = await session.get(Generation, execution.generation_id) if execution else None
                        if not execution or execution.status != 'running' or not generation or generation.stop_requested_at:
                            raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
                        role = await session.get(Role, capabilities.role_id)
                        conversation = await session.get(Conversation, capabilities.conversation_id)
                        current, actor, configuration = await definitions(session, conversation, role, capabilities.triggered_by_user_id, capabilities.execution_id)
                        actual = next((item for item in current if item.name == selected.name), None)
                        if actual is None or actual.args_model.model_json_schema() != selected.args_model.model_json_schema():
                            raise ContextBuildError('AGENT_CAPABILITIES_CHANGED')
                    output = await actual.execute(actor, actual.args_model.model_validate(kwargs), configuration)
                    return json.dumps(output, ensure_ascii=False, default=str)
                except (HTTPException, ContextBuildError):
                    return REJECTED_OUTPUT_PREFIX + ' WORLD_TYPE_TOOL_NOT_AVAILABLE'
                except Exception:
                    # 类型执行可能已经产生副作用，异常只报告未知结果；不输出原异常或自动重试。
                    return FAILED_OUTPUT_PREFIX + ' WORLD_TYPE_TOOL_RESULT_UNKNOWN'
            return call
        result.append(StructuredTool.from_function(coroutine=handler(definition), name=definition.name,
            description=definition.description, args_schema=definition.args_model))
    return guard_tools(result, allow_dangerous=allow_dangerous)
