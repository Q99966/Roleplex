"""独立协调请求：模型运行前建立明确能力、作用域、预算与取消归属。"""
import json
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from ..db import SessionLocal
from ..models import (CoordinationSession, ExecutionAllocation, AgentExecution, Generation, QueueJob,
    WorkflowRun, WorkflowDefinition, WorkflowBudget, Message, User, ConversationMember)
from ..realtime import store as events
from ..realtime.events import current_epoch
from ..config.logging import current_request_id
from ..scheduling import conversation_scheduler
from . import service
from .coordination import coordinator
from .graph_service import digest, target, constraints_for

ACTIVE={'queued','running','stopping'}
DESIGN_TOOLS=['workflow_read_graph','workflow_write_graph','workflow_edit_graph']
MODE_TOOLS={'design':DESIGN_TOOLS,'execute':[*DESIGN_TOOLS,'workflow_start'],
    'replan':[*DESIGN_TOOLS,'workflow_inspect_run','workflow_control']}


async def authorized(session,execution_id,tool=None,*,check_generation=True):
    """校验真实执行与当前任命/资源；模型不能提供或覆盖这些归属参数。"""
    allocation=await session.get(ExecutionAllocation,execution_id)
    grant=await session.get(CoordinationSession,allocation.coordination_session_id) if allocation and allocation.coordination_session_id else None
    if not grant or grant.mode not in MODE_TOOLS or grant.status not in ('queued','running') or grant.execution_id!=execution_id:
        service.reject('WORKFLOW_COORDINATION_REVOKED',403)
    if tool and (tool not in allocation.control_tools_json or tool not in MODE_TOOLS.get(grant.mode,[])):
        service.reject('WORKFLOW_GRAPH_SCOPE',403)
    conv=await service.owned(session,grant.conversation_id,grant.owner_id)
    owner=await session.get(User,grant.owner_id)
    if not owner or not owner.is_owner: service.reject('WORKFLOW_COORDINATION_REVOKED',403)
    await coordinator(session,conv,expected_id=grant.role_id,expected_revision=grant.appointment_revision)
    if conv.workspace_binding_id!=grant.workspace_binding_id: service.reject('WORKFLOW_RESOURCE_CHANGED')
    from ..runtime.models import RuntimeGate
    gate=await session.get(RuntimeGate,1)
    if gate and gate.closing: service.reject('WORKFLOW_WORLD_CLOSING')
    if allocation.authority_json.get('target_existed') and not grant.run_id and not await session.get(WorkflowDefinition,grant.definition_id): service.reject('WORKFLOW_COORDINATION_REVOKED',403)
    if grant.run_id:
        run=await service.get_run(session,grant.conversation_id,grant.owner_id,grant.run_id)
        if run.status in ('stopping','stopped'):
            service.reject('WORKFLOW_COORDINATION_REVOKED',403)
        if run.chain_id!=grant.chain_id: service.reject('WORKFLOW_GRAPH_SCOPE',403)
    execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==execution_id))
    if not execution or execution.role_id!=grant.role_id or execution.chain_id!=grant.chain_id or execution.conversation_id!=grant.conversation_id:
        service.reject('WORKFLOW_COORDINATION_REVOKED',403)
    if check_generation:
        generation=await session.get(Generation,execution.generation_id)
        if execution.status not in ('queued','running') or not generation or generation.status not in ('queued','running') or generation.stop_requested_at is not None:
            service.reject('WORKFLOW_COORDINATION_REVOKED',403)
    return grant


async def view(session,grant):
    execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==grant.execution_id)) if grant.execution_id else None
    generation=await session.get(Generation,execution.generation_id) if execution else None
    budget=await session.get(WorkflowBudget,grant.chain_id)
    from ..models import ModelCallUsage
    calls=list((await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id==grant.execution_id))).all()) if grant.execution_id else []
    usage={field:sum(getattr(c,field) for c in calls) if calls and all(getattr(c,field) is not None for c in calls) else None for field in ['input_tokens','output_tokens']}
    return {'id':grant.id,'definition_id':grant.definition_id,'run_id':grant.run_id,'started_run_id':grant.started_run_id,
        'role_id':grant.role_id,'appointment_revision':grant.appointment_revision,'mode':grant.mode,'goal':grant.goal,
        'status':grant.status,'revision':grant.revision,'execution_id':grant.execution_id,'chain_id':grant.chain_id,
        'message_id':generation.assistant_message_id if generation else None,'error_code':grant.error_code,
        'decision_limit':budget.decision_limit if budget else None,'used_decisions':budget.used_decisions if budget else None,
        'usage':usage,'constraints':grant.constraints_json,'created_at':grant.created_at,'ended_at':grant.ended_at}


async def changed(session,grant):
    grant.revision+=1
    return await events.append_event(session,grant.conversation_id,'workflow_coordination_updated',
        {'coordination_id':grant.id,'status':grant.status,'revision':grant.revision},revision=grant.revision)


async def start(cid,uid,payload):
    """规划不要求可运行图；run 创建、继续规划和重规划复用原任务预算。"""
    async with service.control_lock,SessionLocal() as session:
        conv=await service.owned(session,cid,uid)
        from ..runtime.models import RuntimeGate
        gate=await session.get(RuntimeGate,1)
        if gate and gate.closing: service.reject('WORKFLOW_WORLD_CLOSING')
        request_hash=digest(payload.model_dump(mode='json'))
        existing=await session.scalar(select(CoordinationSession).where(CoordinationSession.conversation_id==cid,
            CoordinationSession.request_key==payload.request_key))
        if existing:
            if existing.request_digest!=request_hash: service.reject('WORKFLOW_REQUEST_CONFLICT')
            return await view(session,existing)
        role=await coordinator(session,conv)
        if role.id!=payload.role_id: service.reject('ORCHESTRATOR_ROLE_MISMATCH',422)
        if payload.mode=='replan' and not payload.run_id: service.reject('WORKFLOW_GRAPH_SCOPE',422)
        if payload.mode!='replan' and payload.run_id: service.reject('WORKFLOW_GRAPH_SCOPE',422)
        run=await service.get_run(session,cid,uid,payload.run_id) if payload.run_id else None
        if run and (run.runtime_version!=2 or run.status in ('stopping','stopped')): service.reject('WORKFLOW_STATE_CONFLICT')
        if run and payload.definition_id and run.definition_id!=payload.definition_id: service.reject('WORKFLOW_GRAPH_SCOPE',403)
        previous=await session.get(CoordinationSession,payload.continue_session_id) if payload.continue_session_id else None
        if payload.continue_session_id and (not previous or previous.conversation_id!=cid or previous.owner_id!=uid): service.reject('WORKFLOW_GRAPH_SCOPE',403)
        did=run.definition_id if run else payload.definition_id or (previous.definition_id if previous else uuid4().hex)
        if not previous and not run and payload.definition_id:
            candidate=await session.scalar(select(CoordinationSession).where(CoordinationSession.conversation_id==cid,
                CoordinationSession.definition_id==did,CoordinationSession.run_id.is_(None),
                CoordinationSession.role_id==role.id,CoordinationSession.appointment_revision==conv.orchestrator_revision)
                .order_by(CoordinationSession.created_at.desc()))
            if candidate and not await session.scalar(select(WorkflowRun.id).where(WorkflowRun.chain_id==candidate.chain_id)):
                previous=candidate

        kind,tid=('run',run.id) if run else ('definition',did)
        row,number,graph=await target(session,cid,uid,kind,tid)
        if payload.definition_id and row is None and previous is None: service.reject('WORKFLOW_NOT_FOUND',404)
        if row and payload.expected_graph_revision is None: service.reject('WORKFLOW_GRAPH_REVISION_REQUIRED',422)
        if payload.expected_graph_revision is not None and payload.expected_graph_revision!=number: service.reject('WORKFLOW_GRAPH_REVISION_CONFLICT')
        chain_id=run.chain_id if run else uuid4().hex
        parent=run.state_json.get('plan_execution_id') if run else None
        inherited=run.snapshot.get('constraints',{}) if run else {}
        if previous:
            if not previous or previous.conversation_id!=cid or previous.owner_id!=uid or previous.definition_id!=did or previous.run_id!=payload.run_id:
                service.reject('WORKFLOW_GRAPH_SCOPE',403)
            if previous.role_id!=role.id or previous.appointment_revision!=conv.orchestrator_revision:
                service.reject('WORKFLOW_COORDINATION_REVOKED',403)
            if not run and await session.scalar(select(WorkflowRun.id).where(WorkflowRun.chain_id==previous.chain_id)):
                service.reject('WORKFLOW_COORDINATION_ALREADY_STARTED')
            chain_id,parent,inherited=previous.chain_id,previous.execution_id,previous.constraints_json
        constraints=constraints_for(graph,payload.protected_nodes or [])
        if inherited and payload.protected_nodes is None:
            from .graph_service import validate_constraints
            validate_constraints(graph,inherited)
            constraints={'nodes':{**inherited.get('nodes',{}),**constraints['nodes']},
                'edges':list({tuple(e):e for e in inherited.get('edges',[])+constraints['edges']}.values()),
                'loops':list({l['id']:l for l in inherited.get('loops',[])+constraints['loops']}.values())}
        if run and payload.protected_nodes is not None:
            run.snapshot={**run.snapshot,'constraints':constraints}
        grant=CoordinationSession(id=uuid4().hex,conversation_id=cid,owner_id=uid,role_id=role.id,
            appointment_revision=conv.orchestrator_revision,definition_id=did,run_id=run.id if run else None,
            chain_id=chain_id,mode=payload.mode,goal=payload.goal,status='queued',revision=0,
            request_key=payload.request_key,request_digest=request_hash,constraints_json=constraints,
            workspace_binding_id=conv.workspace_binding_id,created_at=service.now())
        message=Message(conversation_id=cid,sender_type='user',sender_id=uid,
            parts_json=[{'type':'text','text':payload.goal}],mentions_json=[role.id],status='done',revision=0,
            chain_id=chain_id,meta_json={'coordination_session_id':grant.id,'coordination_mode':grant.mode},created_at=service.now())
        session.add(message);await session.flush()
        grant.trigger_message_id=message.id
        if not await session.get(WorkflowBudget,chain_id):
            from ..services.agent_budget import freeze
            await freeze(session,message)
        session.add(grant);await session.flush()
        generation=Generation(conversation_id=cid,stream_epoch=current_epoch(),status='queued',run_id=chain_id)
        session.add(generation);await session.flush()
        execution=AgentExecution(execution_id=uuid4().hex,conversation_id=cid,generation_id=generation.id,
            parent_execution_id=parent,chain_id=chain_id,role_id=role.id,execution_kind='group_role',attempt=1,status='queued',created_at=service.now())
        session.add(execution);await session.flush();grant.execution_id=execution.execution_id
        session.add(ExecutionAllocation(execution_id=execution.execution_id,attempt_id=None,coordination_session_id=grant.id,
            control_tools_json=list(MODE_TOOLS[grant.mode]),tools_json=[],workspace_binding_id=conv.workspace_binding_id,
            resource_root=None,authority_json={'owner_id':uid,'role_id':role.id,'appointment_revision':grant.appointment_revision,
                'target_kind':kind,'target_id':tid,'target_existed':row is not None},revision=1,created_at=service.now()))
        job=QueueJob(conversation_id=cid,generation_id=generation.id,status='queued',payload_json={
            'current_message_id':message.id,'triggered_by_user_id':uid,'allow_dangerous':True,
            'request_id':current_request_id(),'parallel_workflow':True},attempts=0,cancel_requested=False,created_at=service.now())
        session.add(job);await session.flush()
        from ..services.chat import message_payload
        pending=[await events.append_event(session,cid,'message_created',{'message':message_payload(message)},revision=0),await changed(session,grant)]
        if run and payload.protected_nodes is not None: pending.append(await service.changed(session,run))
        await session.commit();result=await view(session,grant);job_id=job.id
    await events.publish_events(*pending)
    await conversation_scheduler.enqueue_parallel(cid,job_id)
    return result


async def advance_all():
    """协调会话与原 execution 收口同步；恢复/撤销不重放模型或图修改。"""
    stop_ids=[];pending=[]
    async with service.control_lock,SessionLocal() as session:
        grants=list((await session.scalars(select(CoordinationSession).where(CoordinationSession.status.in_(ACTIVE)))).all())
        for grant in grants:
            execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==grant.execution_id))
            previous=(grant.status,grant.error_code)
            generation=await session.get(Generation,execution.generation_id) if execution else None
            if not grant.error_code and generation and generation.stop_requested_at is not None: grant.status='stopping'
            if grant.status!='stopping':
                try: await authorized(session,grant.execution_id,check_generation=False)
                except HTTPException as exc:
                    grant.status,grant.error_code='stopping',str(exc.detail) if isinstance(exc.detail,str) else exc.detail['code']
            if execution and execution.status in ('queued','running'):
                if grant.status=='stopping': stop_ids.append((grant.conversation_id,execution.generation_id))
                elif grant.status!=execution.status: grant.status=execution.status
            else:
                grant.status='blocked' if grant.error_code else 'stopped' if grant.status=='stopping' else execution.status if execution else 'interrupted'
                grant.error_code=grant.error_code or (execution.error_code if execution else 'WORKFLOW_INTERRUPTED')
                grant.ended_at=service.now()
            if previous!=(grant.status,grant.error_code): pending.append(await changed(session,grant))
        await session.commit()
    if pending: await events.publish_events(*pending)
    for cid,gid in stop_ids: await conversation_scheduler.stop_generations(cid,[gid])


async def cancel(cid,uid,sid,payload):
    async with service.control_lock,SessionLocal() as session:
        await service.owned(session,cid,uid)
        grant=await session.get(CoordinationSession,sid)
        if not grant or grant.conversation_id!=cid or grant.owner_id!=uid: service.reject('WORKFLOW_NOT_FOUND',404)
        if grant.revision!=payload.expected_revision: service.reject('WORKFLOW_REVISION_CONFLICT')
        if grant.status not in ACTIVE: return await view(session,grant)
        grant.status='stopping'
        pending=[await changed(session,grant)]
        run=await session.get(WorkflowRun,grant.started_run_id) if grant.started_run_id else None
        if run and run.status in service.ACTIVE:
            run.status='stopping';pending.append(await service.changed(session,run))
        if grant.run_id:
            target_run=await session.get(WorkflowRun,grant.run_id)
            if target_run:
                from .replanning import cancel_pending
                await cancel_pending(session,target_run,'WORKFLOW_COORDINATION_REVOKED',source_execution_id=grant.execution_id)
                pending.append(await service.changed(session,target_run))
        execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==grant.execution_id))
        await session.commit();result=await view(session,grant)
    await events.publish_events(*pending)
    if run: await conversation_scheduler.stop_chain(cid,grant.chain_id)
    elif execution: await conversation_scheduler.stop_generations(cid,[execution.generation_id])
    return result


async def recover(session):
    for grant in (await session.scalars(select(CoordinationSession).where(CoordinationSession.status.in_(ACTIVE)))).all():
        grant.status,grant.error_code,grant.ended_at='interrupted','WORKFLOW_INTERRUPTED',service.now()
        grant.revision+=1


async def context(session,execution_id):
    """仅本次明确授予的身份/能力进入协调指令，目标正文作为 Owner 请求保留。"""
    allocation=await session.get(ExecutionAllocation,execution_id) if execution_id else None
    if not allocation or not allocation.coordination_session_id: return None
    grant=await authorized(session,execution_id)
    return '本次为明确授权的群流程协调请求。先调用 workflow_read_graph 读取图、成员能力、约束及版本。用 workflow_write_graph 创建完整图或整体替换，用 workflow_edit_graph 按 ID 局部调整；两者独立。错误后按定位修正，修改内容换新 mutation_key；重发同一修改保持原键。保存不等于执行，只有工具列表包含 workflow_start 时才可启动；不得代签人工确认。任务需要结构化结果时设置 result_schema。保持未改节点 ID、位置和颜色。不要只用文字宣称已改图。不要持续轮询等待其他节点，提交后简短结束。\n本次图管理授权：' + json.dumps({
        'coordination_session_id':grant.id,'mode':grant.mode,'definition_id':grant.definition_id,'run_id':grant.run_id,
        'capabilities':allocation.control_tools_json,'protected':grant.constraints_json},ensure_ascii=False)


async def check_retry_evidence(session,execution_id,run,attempt):
    """模型不能自称核对未知写入；须先观察当前运行，未知副作用交回 Owner。"""
    from ..models import FileEffect
    from ..services.file_effects import decode
    allocation=await session.get(ExecutionAllocation,execution_id)
    if (allocation.authority_json.get('inspected_revision')!=run.revision
        or attempt.id not in allocation.authority_json.get('inspected_attempts',[])):
        service.reject('WORKFLOW_RETRY_REVIEW_REQUIRED')
    generation=await session.get(Generation,attempt.generation_id) if attempt.generation_id else None
    message=await session.get(Message,generation.assistant_message_id) if generation and generation.assistant_message_id else None
    mutation_calls={p.get('call_id') for p in (message.parts_json if message else []) if p.get('type')=='tool_call' and p.get('tool_name') in ('workspace_write','workspace_edit')}
    for call_id in mutation_calls:
        rows=(await session.scalars(select(FileEffect).where(FileEffect.execution_id==attempt.execution_id,FileEffect.call_id==call_id))).all()
        if not rows: service.reject('WORKFLOW_RETRY_REVIEW_REQUIRED')
        for row in rows:
            try: confirmed=decode(row)['state']=='confirmed'
            except Exception: confirmed=False
            if not confirmed: service.reject('WORKFLOW_RETRY_REVIEW_REQUIRED')
