"""任务分配与执行时权限复核，普通执行没有分配记录时维持原契约。"""
from sqlalchemy import select
from ..models import ExecutionAllocation, WorkflowAttempt, WorkflowActivation, WorkflowRun, Conversation, Role, User, ConversationMember, Generation, AgentExecution


async def tools_for(session, execution_id):
    allocation = await session.get(ExecutionAllocation, execution_id) if execution_id else None
    if allocation is None: return None
    if not await allowed(session,execution_id): return []
    if allocation.coordination_session_id or not allocation.attempt_id or allocation.authority_json.get('phase') not in ('work','judge','plan','summary'): return []
    return list(allocation.tools_json)


async def allowed(session, execution_id, tool_name=None, *, check_generation=True):
    """每次调用核对当前激活与任命；旧分配快照不能越过撤权、停止或重试。"""
    allocation = await session.get(ExecutionAllocation, execution_id)
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id)) if execution_id else None
    if execution:
        from ..world_orchestrator.tasks import child_allowed
        if not await child_allowed(session, execution.chain_id):
            return False
    if allocation is None:
        return True
    if allocation.coordination_session_id:
        from .planning import authorized
        from fastapi import HTTPException
        try:
            await authorized(session,execution_id,tool_name,check_generation=check_generation)
            return tool_name is None or tool_name in allocation.control_tools_json
        except HTTPException: return False
    if tool_name is not None and tool_name not in [*allocation.tools_json,*allocation.control_tools_json]:
        return False
    if not allocation.attempt_id: return False
    attempt = await session.get(WorkflowAttempt, allocation.attempt_id)
    activation = await session.get(WorkflowActivation, attempt.activation_id) if attempt and attempt.activation_id else None
    run = await session.get(WorkflowRun, attempt.run_id) if attempt else None
    if not run or run.status not in ('queued', 'running', 'waiting') or not activation or activation.selected_attempt_id != attempt.id or activation.status not in ('ready', 'active'):
        return False
    conv = await session.get(Conversation, run.conversation_id)
    owner = await session.get(User, run.owner_id)
    if not conv or conv.deleted_at or conv.created_by != run.owner_id or not owner or not owner.is_owner or conv.workspace_binding_id != allocation.workspace_binding_id:
        return False
    authority = allocation.authority_json
    if authority.get('phase')!=attempt.phase or attempt.phase not in ('work','judge','plan','summary'): return False
    execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==execution_id))
    if not execution or execution.role_id!=authority.get('role_id') or execution.generation_id!=attempt.generation_id or execution.chain_id!=run.chain_id or execution.conversation_id!=run.conversation_id: return False
    role = await session.get(Role, authority['role_id'])
    if not role or not role.active or role.deleted_at or role.created_by != run.owner_id:
        return False
    for kind, mid in [('user', run.owner_id), ('role', role.id)]:
        if await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == conv.id,
            ConversationMember.member_type == kind, ConversationMember.member_id == mid)) is None:
            return False
    if run.snapshot.get('mode') == 'coordinated':
        if (not conv.orchestrator_enabled or conv.orchestrator_role_id != run.snapshot.get('coordinator_role_id')
            or conv.orchestrator_revision != run.snapshot.get('appointment_revision')):
            return False
        coordinator = await session.get(Role, conv.orchestrator_role_id)
        if coordinator is None or not coordinator.active or coordinator.deleted_at:
            return False
        if await session.scalar(select(ConversationMember.id).where(ConversationMember.conversation_id == conv.id,
            ConversationMember.member_type == 'role', ConversationMember.member_id == coordinator.id)) is None:
            return False
    if not set(allocation.tools_json).issubset(set(role.builtin_tools_json or [])):
        return False
    # 工具类别负责自身资源前置；任务分配复用同一能力解析，不把文件根作为所有工具的前置。
    from ..agent.capabilities import role_tool_policy
    policy = await role_tool_policy(session, conversation=conv, role=role, triggered_by_user_id=run.owner_id)
    available = {tool['name'] for tool in policy['exposed_tools']}
    if not set(allocation.tools_json).issubset(available): return False
    if not check_generation: return True
    generation = await session.get(Generation, attempt.generation_id) if attempt.generation_id else None
    return bool(generation and generation.status in ('queued', 'running') and generation.stop_requested_at is None)


async def policy(session, execution_id, base):
    """工具 schema、预算指纹和工厂共享分配，不因资源等待删除 schema。"""
    allocation = await session.get(ExecutionAllocation, execution_id) if execution_id else None
    if not allocation:
        return base
    if not await allowed(session,execution_id): return {**base,'exposed_tools':[]}
    if allocation.coordination_session_id:
        from .graph_tools import specs
        return {**base, 'allocation_revision':allocation.revision,'exposed_tools':specs(allocation.control_tools_json)}
    from .coordination import control_specs
    attempt = await session.get(WorkflowAttempt, allocation.attempt_id) if allocation.attempt_id else None
    if not attempt or attempt.phase not in ('work','judge','plan','summary'): return {**base,'exposed_tools':[]}
    return {**base, 'allocation_revision': allocation.revision,
        'exposed_tools': [tool for tool in base['exposed_tools'] if tool['name'] in allocation.tools_json] + control_specs(attempt.phase,names=allocation.control_tools_json,result_fields=allocation.authority_json.get('result_fields'))}
