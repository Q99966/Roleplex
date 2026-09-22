"""群协调模型的结构化交接工具；普通工作节点只能报告结果，不能获得分派权。"""
import json
from sqlalchemy import select
from pydantic import Field, ConfigDict, ValidationError, StrictInt
from langchain_core.tools import StructuredTool
from ..models import WorkflowRun, WorkflowAttempt, ExecutionAllocation, Role, Conversation
from ..db import SessionLocal
from ..agent.tools import guard_tools, REJECTED_OUTPUT_PREFIX
from .schemas import Strict, Scalar
from .feedback_schemas import FeedbackItem


class Assignment(Strict):
    node_id: str
    role_id: int
    tools: list[str]
    instruction: str = ''


class PlanInput(Strict):
    assignments: list[Assignment]


class ResultInput(Strict):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    expected_result_revision: StrictInt | None = Field(default=None,ge=0,description='修正已接受的结果时携带上次返回的 result_revision，避免并发报告互相覆盖。')
    values: dict[str, Scalar]
    summary: str = ''
    feedback: list[FeedbackItem] = Field(default_factory=list, max_length=20)


class SummaryInput(Strict):
    summary: str = Field(min_length=1)


DESCRIPTIONS = {
    'workflow_plan': '提交本次流程的结构化任务分工。为每个 role/judge 节点提交一次 node_id、role_id、tools 和可选 instruction。保留用户指定角色/必要工具；只能从当前群授权能力中选择。后台校验和持久化成功才表示计划被接受；本工具不会修改角色全局配置。',
    'workflow_result': '报告本节点的结构化结果。values 使用命名标量，条件只读取这些值。summary 简述实际依据。需要后续处置时用 feedback 登记 implementation 实现问题、contract 契约冲突、capability 能力缺口、unverified 未验证项或 suggestion 建议；不能把缺少验证能力当作实现失败。每项使用稳定 request_key；blocking=true 暂停受影响的后继，普通建议设 false。没有问题省略 feedback。不授予图管理或额外工具权限。',
    'workflow_summary': '提交当前群流程的最终汇总，明确失败、跳过或未知结果，不再派发任务。',
}
SCHEMAS = {'workflow_plan': PlanInput, 'workflow_result': ResultInput, 'workflow_summary': SummaryInput}


def names_for(phase):
    return {'plan':['workflow_plan'],'summary':['workflow_summary'],'judge':['workflow_result'],'work':['workflow_result']}.get(phase,[])


def control_specs(phase, *, names=None, result_fields=None):
    from .results import schema
    from ..agent.tool_definitions import tool_definition
    return [tool_definition(name, DESCRIPTIONS[name], schema(result_fields) if name == 'workflow_result' and result_fields is not None else SCHEMAS[name])
        for name in names_for(phase) if names is None or name in names]


async def coordinator(session, conv, *, expected_id=None, expected_revision=None):
    """任命是本群关系；旧预留字段不构成新功能的隐式授权。"""
    from .service import reject, validate_roles
    if conv.type != 'group' or not conv.orchestrator_enabled or not conv.orchestrator_role_id or conv.orchestrator_revision <= 0:
        reject('ORCHESTRATOR_REQUIRED', 422)
    if expected_id is not None and (conv.orchestrator_role_id != expected_id or conv.orchestrator_revision != expected_revision):
        reject('ORCHESTRATOR_APPOINTMENT_CHANGED')
    await validate_roles(session, conv, [{'kind': 'role', 'role_id': conv.orchestrator_role_id}], conv.created_by)
    return await session.get(Role, conv.orchestrator_role_id)


async def capabilities(session, conv, owner_id):
    """提供可分配工具名，不读取 Key、完整模型配置或宿主绝对路径。"""
    from ..models import ConversationMember
    from ..agent.capabilities import role_tool_policy
    roles = (await session.scalars(select(Role).join(ConversationMember,
        (ConversationMember.member_type == 'role') & (ConversationMember.member_id == Role.id)).where(
        ConversationMember.conversation_id == conv.id, Role.created_by == owner_id, Role.active.is_(True), Role.deleted_at.is_(None)))).all()
    result = []
    for role in roles:
        policy = await role_tool_policy(session, conversation=conv, role=role, triggered_by_user_id=owner_id)
        result.append({'role_id': role.id, 'name': role.name, 'tools': [t['name'] for t in policy['exposed_tools']]})
    return result


async def validate_assignments(session, run, assignments):
    """模型只提出需求，后台建立精确交集；必要工具缺口不静默降级。"""
    from .service import owned, reject
    conv = await owned(session, run.conversation_id, run.owner_id)
    await coordinator(session, conv, expected_id=run.snapshot['coordinator_role_id'], expected_revision=run.snapshot['appointment_revision'])
    caps = {r['role_id']: set(r['tools']) for r in await capabilities(session, conv, run.owner_id)}
    nodes = {n['id']: n for n in run.snapshot['nodes'] if n['kind'] in ('role', 'judge')}
    if len(assignments) != len(nodes) or {a['node_id'] for a in assignments} != set(nodes):
        reject('ORCHESTRATOR_PLAN_INVALID', 422)
    result = {}
    for assignment in assignments:
        node = nodes[assignment['node_id']]
        rid, requested = assignment['role_id'], assignment['tools']
        specified = run.snapshot['coordinator_role_id'] if node['kind'] == 'judge' else node.get('role_id')
        if rid not in caps or (specified is not None and rid != specified):
            reject('ORCHESTRATOR_PLAN_INVALID', 422)
        available = caps[rid] if node['kind'] == 'role' else set()
        if len(set(requested)) != len(requested) or not set(requested).issubset(available):
            reject('WORKFLOW_TOOL_NOT_GRANTED', 422)
        if node.get('tools') is not None and set(requested) != set(node['tools']):
            reject('WORKFLOW_TOOL_NOT_GRANTED', 422)
        result[node['id']] = assignment
    return result


async def create_control_tools(session, *, execution_id):
    allocation = await session.get(ExecutionAllocation, execution_id)
    if allocation is None: return []
    from .allocations import allowed
    if not await allowed(session,execution_id): return []
    if allocation.coordination_session_id:
        from .graph_tools import create
        return await create(session,execution_id)
    attempt = await session.get(WorkflowAttempt, allocation.attempt_id) if allocation.attempt_id else None
    if not attempt: return []
    from .results import schema as result_schema
    result_model=result_schema(allocation.authority_json['result_fields']) if 'result_fields' in allocation.authority_json else ResultInput
    async def submit(name, payload):
        from fastapi import HTTPException
        from . import service
        from .allocations import allowed
        async with service.control_lock:
            async with SessionLocal() as fresh:
                if not await allowed(fresh, execution_id, name):
                    return f'{REJECTED_OUTPUT_PREFIX} WORKFLOW_ALLOCATION_REVOKED'
                a = await fresh.get(WorkflowAttempt, allocation.attempt_id)
                run = await fresh.get(WorkflowRun, a.run_id)
                if name not in names_for(a.phase):
                    return f'{REJECTED_OUTPUT_PREFIX} WORKFLOW_TOOL_NOT_GRANTED'
                try:
                    if name == 'workflow_plan':
                        result = {'assignments': await validate_assignments(fresh, run, payload['assignments'])}
                    elif name == 'workflow_summary':
                        result = {'summary': payload['summary']}
                    else:
                        node = a.node_snapshot_json or next(n for n in run.snapshot['nodes'] if n['id'] == a.node_id)
                        keys = set(node.get('result_keys', []))
                        if node['kind'] == 'judge': keys.add(node['condition']['key'])
                        if not keys.issubset(payload['values']):
                            service.reject('WORKFLOW_RESULT_INVALID', 422)
                        result = result_model.model_validate(payload).model_dump(mode='json',by_alias=True)
                    if name=='workflow_result':
                        expected=result.pop('expected_result_revision',None)
                        reports = result.pop('feedback', [])
                        from .graph_service import digest
                        if reports: result['feedback_digest'] = digest(reports)
                        prior=a.result_json
                        revision=prior.get('revision',1) if prior else 0
                        if prior and {k:v for k,v in prior.items() if k not in ('revision', 'feedback_ids')}==result:
                            return json.dumps({'recorded':True,'replayed':True,'result_revision':revision})
                        if (prior and expected!=revision) or (not prior and expected not in (None,0)):
                            return f'{REJECTED_OUTPUT_PREFIX} '+json.dumps({'code':'WORKFLOW_RESULT_REVISION_CONFLICT','result_revision':revision})
                        result['revision']=revision+1
                        feedback_ids = list((prior or {}).get('feedback_ids', []))
                        from .feedback import create_in_session
                        for report in reports:
                            item, _ = await create_in_session(fresh, run, a, FeedbackItem.model_validate(report),
                                uid=run.owner_id, execution_id=execution_id, result_revision=revision+1)
                            if item.id not in feedback_ids: feedback_ids.append(item.id)
                        if feedback_ids: result['feedback_ids'] = feedback_ids
                    elif a.result_json is not None:
                        if a.result_json==result: return json.dumps({'recorded':True,'replayed':True})
                        return f'{REJECTED_OUTPUT_PREFIX} WORKFLOW_RESULT_ALREADY_RECORDED'
                    a.result_json = result
                    notice = await service.changed(fresh, run) if name == 'workflow_result' and reports else None
                    await fresh.commit()
                    output = json.dumps({'recorded':True,**({'result_revision':result['revision'], 'feedback_ids': result.get('feedback_ids', [])} if name=='workflow_result' else {})},ensure_ascii=False)
                except ValidationError:
                    return f'{REJECTED_OUTPUT_PREFIX} WORKFLOW_RESULT_INVALID'
                except HTTPException as exc:
                    return f'{REJECTED_OUTPUT_PREFIX} {exc.detail}'
        if notice:
            from ..realtime import store as events
            await events.publish_events(notice)
        return output
    async def plan(assignments: list):
        return await submit('workflow_plan', {'assignments': [a.model_dump() if hasattr(a, 'model_dump') else a for a in assignments]})
    async def result(values: dict, summary: str = '', expected_result_revision: int | None = None, feedback: list | None = None):
        return await submit('workflow_result', {'values': values.model_dump(mode='json',by_alias=True) if hasattr(values,'model_dump') else values, 'summary': summary,'expected_result_revision':expected_result_revision,
            'feedback': [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in feedback or []]})
    async def summary(summary: str):
        return await submit('workflow_summary', {'summary': summary})
    functions = {'workflow_plan': plan, 'workflow_result': result, 'workflow_summary': summary}
    return guard_tools([StructuredTool.from_function(coroutine=functions[name], name=name, description=DESCRIPTIONS[name],
        args_schema=result_model if name=='workflow_result' else SCHEMAS[name], handle_validation_error=lambda _: f'{REJECTED_OUTPUT_PREFIX} WORKFLOW_RESULT_INVALID')
        for name in names_for(attempt.phase) if name in allocation.control_tools_json], allow_dangerous=True)


async def revoke_runs(session, conversation_id):
    """任命更换在同一事务封闭旧运行，循环/子节点不能继承新任命。"""
    from .service import ACTIVE, changed
    runs = (await session.scalars(select(WorkflowRun).where(WorkflowRun.conversation_id == conversation_id,
        WorkflowRun.runtime_version == 2, WorkflowRun.status.in_(ACTIVE)))).all()
    events, chains = [], []
    for run in runs:
        if run.snapshot.get('mode') != 'coordinated': continue
        run.status, run.error_code = 'stopping', 'ORCHESTRATOR_APPOINTMENT_CHANGED'
        events.append(await changed(session, run)); chains.append(run.chain_id)
    from ..models import CoordinationSession
    from .planning import changed as coordination_changed, ACTIVE as coordination_active
    grants=(await session.scalars(select(CoordinationSession).where(CoordinationSession.conversation_id==conversation_id,CoordinationSession.status.in_(coordination_active)))).all()
    for grant in grants:
        grant.status,grant.error_code='stopping','ORCHESTRATOR_APPOINTMENT_CHANGED'
        events.append(await coordination_changed(session,grant));chains.append(grant.chain_id)
    return events, list(dict.fromkeys(chains))


async def revoke_role_runs(session, role_id):
    """角色停用/删除立即留下收口状态；重新启用不能复活旧协调授权。"""
    conversations = (await session.scalars(select(Conversation).where(Conversation.orchestrator_role_id == role_id))).all()
    pending = []
    for conversation in conversations:
        events, _ = await revoke_runs(session, conversation.id)
        pending.extend(events)
    return pending
