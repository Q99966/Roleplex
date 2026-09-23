"""宿主声明消息来源及执行输入；正文、@ 和模型参数均不能伪造授权。"""
from copy import deepcopy
from sqlalchemy import select
from ..db import now_utc
from ..models import (AgentExecution, ExecutionInput, Generation, Message, Role, User,
    WorldCoordinationGrant, WorldTaskChild, WorldTask)
from ..context.domain import ContextBuildError


async def actor(session, kind, identity=None, *, historical=False, duty=None):
    """返回可公开的身份快照，不携带角色 Prompt、模型配置或凭据。"""
    if kind == 'system': return {'kind': kind, 'id': None, 'name': '工作流派发'}
    row = await session.get(User if kind == 'user' else Role, identity) if identity else None
    name = f'{"用户" if kind == "user" else "角色"} #{identity}' if historical else (
        row.nickname if kind == 'user' and row else row.name if row else f'角色 #{identity}')
    if kind == 'world_manager':
        return {'kind': kind, 'id': 1, 'role_id': identity, 'name': name, 'historical': historical}
    return {'kind': kind, 'id': identity, 'name': name, 'historical': historical, **({'duty': duty} if duty else {})}


def envelope(kind, sender, recipients=(), *, owner_id=None, source=None, report_to=(), via=None):
    return {'version': 1, 'kind': kind, 'actor': sender, 'recipients': list(recipients),
        'authorized_by_user_id': owner_id, 'source': source or {}, 'report_to': list(report_to), 'via': via}


def stamp(message, value):
    message.meta_json = {**(message.meta_json or {}), 'communication': deepcopy(value)}


def public_parts(message):
    """旧节点原文保留在原记录，公开历史只显示来源摘要，避免再次传播执行专用提示。"""
    value = (message.meta_json or {}).get('communication') or {}
    if value.get('kind') == 'legacy_execution_input':
        return [{'type': 'text', 'text': '历史节点执行输入；完整要求可在对应执行记录中核对。'}]
    if value.get('kind') == 'workflow_started' and value.get('legacy'):
        return [{'type': 'text', 'text': '工作流已启动；目标见关联的委派记录。'}]
    return message.parts_json


def sender_identity(message):
    """旧数据库字段作为原始证据保留，对外及共享材料使用已核实的真实主体。"""
    value = (message.meta_json or {}).get('communication') or {}
    sender = value.get('actor') or {}
    kind = sender.get('kind')
    if kind == 'world_manager': return 'orchestrator', sender.get('role_id')
    if kind in {'system', 'role', 'user'}: return kind, sender.get('id')
    return message.sender_type, message.sender_id


async def save_input(session, execution, message, owner_id, text, source, *, batch_id=None):
    """与 execution/队列同事务冻结任务文字和来源；读取不重放调用。"""
    row = ExecutionInput(execution_id=execution.execution_id, conversation_id=execution.conversation_id,
        owner_id=owner_id, message_id=message.id, batch_id=batch_id, text=text,
        source_json=deepcopy(source), created_at=now_utc())
    session.add(row)
    return row


async def authorized_input(session, request):
    if not request.execution_id: return None
    value = await session.get(ExecutionInput, request.execution_id)
    if value is None: return None
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == request.execution_id))
    generation = await session.get(Generation, execution.generation_id) if execution else None
    if (not execution or value.owner_id != request.triggered_by_user_id or value.conversation_id != request.conversation_id
        or execution.conversation_id != request.conversation_id or execution.role_id != request.role_id
        or value.message_id != request.current_message_id or not value.message_id
        or execution.status not in {'queued', 'running'} or not generation or generation.stop_requested_at):
        raise ContextBuildError('CONTEXT_CURRENT_MESSAGE_INVALID')
    from ..workflows.allocations import allowed
    if not await allowed(session, request.execution_id):
        raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
    return value


async def report_targets(session, run, phase, *, historical=False):
    """工作节点向本次群协调者报告；总结按原父任务关系交回世界管理者或 Owner。"""
    if phase not in {'summary', 'plan'} and run.snapshot.get('coordinator_role_id'):
        return [await actor(session, 'role', run.snapshot['coordinator_role_id'], historical=historical, duty='group_coordinator')]
    child = await session.scalar(select(WorldTaskChild).where(WorldTaskChild.coordination_id == run.snapshot.get('coordination_session_id')))
    task = await session.get(WorldTask, child.task_id) if child else None
    return [await actor(session, 'world_manager', task.role_id, historical=historical)] if task else [await actor(session, 'user', run.owner_id, historical=historical)]


async def reply_envelope(session, execution_id, current_message_id, role, owner_id):
    """实际回复的主体/收件人来自已授权输入，发出正文中的 @ 不产生新任务。"""
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
    input_row = await session.get(ExecutionInput, execution_id)
    current = await session.get(Message, current_message_id) if current_message_id else None
    source = input_row.source_json if input_row else {}
    world = execution and execution.execution_kind == 'world_coord'
    coordinator = source.get('coordination_session_id') or source.get('coordination_phase') in {'plan', 'summary', 'judge'}
    sender = await actor(session, 'world_manager' if world else 'role', role.id, duty='group_coordinator' if coordinator else None)
    targets = source.get('communication', {}).get('report_to') or [await actor(session, 'user', owner_id)]
    value = envelope('workflow_result' if input_row and source.get('workflow_attempt_id') else 'coordination_reply' if world or input_row else 'chat',
        sender, targets, owner_id=owner_id, source={**source.get('communication', {}).get('source', {}),
            'execution_id': execution_id, 'reply_to_message_id': current.id if current else None})
    return value


async def routing_context(session, request, conversation, role, current, input_row):
    """供模型理解本次讲话身份与回报对象；不把输入发送者误当作执行角色自身。"""
    world = conversation.purpose == 'world_coord'
    communication = input_row.source_json.get('communication', {}) if input_row else (current.meta_json or {}).get('communication', {})
    uid = request.triggered_by_user_id if request.triggered_by_user_id is not None else current.sender_id
    grant = await session.get(WorldCoordinationGrant, request.execution_id) if world and request.execution_id else None
    source = input_row.source_json if input_row else {}
    duty = 'world_manager' if world else 'group_coordinator' if source.get('coordination_session_id') or source.get('coordination_phase') in {'plan','summary','judge'} else 'workflow_role' if input_row else 'conversation_role'
    return {'version': 1, 'speaker': await actor(session, 'world_manager' if world else 'role', role.id), 'duty': duty,
        'input_from': communication.get('actor') or await actor(session, 'user', uid),
        'reply_to': communication.get('report_to') or [await actor(session, 'user', uid)],
        'mode': 'execute' if input_row or grant and grant.task_id else 'chat',
        'notice': '使用实际工具提交结果和反馈；回复对象与任务身份由后台核对。正文 @ 不产生新的调用。' +
            (' 当前为对话与查看模式；如需启动新任务，可提示 Owner 选择执行世界任务。' if world and not (grant and grant.task_id) else '')}
