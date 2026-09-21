"""节点反馈与处置共用服务：原意见不变，状态转移有版本、幂等和可核对依据。"""
from uuid import uuid4

from sqlalchemy import select, or_
from fastapi import HTTPException

from ..db import SessionLocal, with_locked_retry
from ..models import (
    AgentExecution, CoordinationSession, ExecutionAllocation, WorkflowActivation,
    WorkflowAttempt, WorkflowFeedback, WorkflowFeedbackEvent, WorkflowGraphRevision, WorkflowRun, Conversation,
)
from ..realtime import store as events
from . import service
from .feedback_schemas import CreateFeedback, FeedbackUpdate
from .graph_service import digest

CLOSED = {'resolved', 'dismissed', 'accepted', 'obsolete'}
OPEN = {'open', 'in_progress', 'waiting', 'review'}


async def append_event(session, item, action, reason, uid, execution_id=None, *, request_key=None, request_digest='', data=None, actor_kind=None):
    """在调用方短事务中追加处置事实，不把私有意见写入机器日志。

    Args:
        session：已持有控制边界的数据库会话。
        item：当前反馈及其新修订号。
        action：固定操作名。
        reason：处置依据，仅进入 Owner 业务记录。
        uid：已鉴权的 Owner。
        execution_id：模型处置的真实执行，可空。
        request_key：用于重发去重；后台观察事件自行生成。
        request_digest：本次处置摘要指纹。
        data：可追溯的角色、节点或验证引用。
        actor_kind：后台观察或交接明确标为 system，不冒充 Owner 操作。
    """
    session.add(WorkflowFeedbackEvent(
        id=uuid4().hex, feedback_id=item.id, revision=item.revision, action=action,
        status=item.status, reason=reason, actor_user_id=uid, actor_execution_id=execution_id,
        request_key=request_key or uuid4().hex, request_digest=request_digest,
        data_json={**(data or {}), 'actor_kind': actor_kind or ('role' if execution_id else 'owner')}, created_at=service.now(),
    ))


async def source_is_current(session, run, item):
    """按精确激活判断意见是否仍对应当前结果。

    Args:
        session：读取会话。
        run：已授权运行。
        item：带原始尝试关联的反馈。
    """
    from .engine import current_activations
    if run.runtime_version != 2: return run.selected.get(item.node_id) == item.attempt_id
    rows = list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id == run.id))).all())
    activation = current_activations(run, rows).get(item.node_id)
    return bool(activation and activation.selected_attempt_id == item.attempt_id)


async def view(session, run, item):
    """返回原意见、处置历史及实际图修改，不复制原工具内容。

    Args:
        session：已授权短读取会话。
        run：反馈归属运行。
        item：待序列化的反馈。
    """
    attempt = await session.get(WorkflowAttempt, item.attempt_id)
    activation = await session.get(WorkflowActivation, attempt.activation_id) if attempt and attempt.activation_id else None
    history = list((await session.scalars(select(WorkflowFeedbackEvent).where(
        WorkflowFeedbackEvent.feedback_id == item.id).order_by(WorkflowFeedbackEvent.revision))).all())
    coordinations = list((await session.scalars(select(CoordinationSession).where(CoordinationSession.run_id == run.id))).all())
    executions = [g.execution_id for g in coordinations if item.id in (g.feedback_ids_json or []) and g.execution_id]
    linked_versions = [e.data_json['graph_revision'] for e in history if e.action == 'assign' and e.data_json.get('graph_revision')]
    revisions = list((await session.scalars(select(WorkflowGraphRevision).where(
        WorkflowGraphRevision.run_id == run.id,
        or_(WorkflowGraphRevision.source_execution_id.in_(executions), WorkflowGraphRevision.number.in_(linked_versions)),
        WorkflowGraphRevision.created_at >= item.created_at)
        .order_by(WorkflowGraphRevision.number))).all()) if executions or linked_versions else []
    return {
        'id': item.id, 'run_id': item.run_id, 'attempt_id': item.attempt_id, 'node_id': item.node_id,
        'graph_revision': item.graph_revision, 'result_revision': item.source_result_revision,
        'iteration': activation.iteration if activation else 0, 'source_role_id': item.source_role_id,
        'source_current': await source_is_current(session, run, item),
        'actor_user_id': item.actor_user_id, 'actor_execution_id': item.actor_execution_id,
        'category': item.category, 'summary': item.summary, 'details': item.details,
        'blocking': item.blocking, 'status': item.status, 'revision': item.revision,
        'suggested_role_id': item.suggested_role_id, 'handler_role_id': item.handler_role_id,
        'handler_node_ids': item.handler_node_ids, 'verification_attempt_id': item.verification_attempt_id,
        'coordination_session_id': item.coordination_session_id, 'capability_check': item.capability_check,
        'coordination_requested': item.coordination_requested,
        'created_at': item.created_at, 'updated_at': item.updated_at,
        'history': [{'id': e.id, 'revision': e.revision, 'action': e.action, 'status': e.status,
                     'reason': e.reason, 'actor_user_id': e.actor_user_id, 'actor_execution_id': e.actor_execution_id,
                     'actor_kind': e.data_json.get('actor_kind', 'owner'),
                     'data': e.data_json, 'created_at': e.created_at} for e in history],
        'graph_changes': [{'graph_revision': r.number, 'status': r.status, 'changes': r.changes_json} for r in revisions],
    }


async def for_run(session, run):
    """复用调用方的授权和快照控制边界读取本运行反馈。

    Args:
        session：调用方数据库会话。
        run：已完成归属检查的运行。
    """
    rows = list((await session.scalars(select(WorkflowFeedback).where(WorkflowFeedback.run_id == run.id)
        .order_by(WorkflowFeedback.created_at, WorkflowFeedback.id))).all())
    return [await view(session, run, item) for item in rows]


async def blockers(session, run, current):
    """仅阻塞仍被选择的来源及其后继，明确分配的处置节点可以通过。

    Args:
        session：调度器持有的短事务。
        run：当前运行；使用有效图，未采用修订不参与调度。
        current：按当前轮次和图选择的激活。

    Returns:
        等待处置的节点、不能进入下一轮的循环和有效阻塞意见。
    """
    from .graph import descendants
    items = list((await session.scalars(select(WorkflowFeedback).where(
        WorkflowFeedback.run_id == run.id, WorkflowFeedback.status.in_(OPEN), WorkflowFeedback.blocking.is_(True)))).all())
    blocked, loops, active = set(), set(), []
    for item in items:
        source = current.get(item.node_id)
        if not source or source.selected_attempt_id != item.attempt_id: continue
        active.append(item)
        blocked.update(descendants(run.snapshot, item.node_id) - set(item.handler_node_ids or []))
        if source.loop_id: loops.add(source.loop_id)
    return blocked, loops, active


async def listing(cid, uid, rid):
    """Owner 读取反馈，跨群和已删除会话沿用原隐藏语义。

    Args:
        cid：当前群。
        uid：已鉴权 Owner。
        rid：指定运行。
    """
    async with service.control_lock, SessionLocal() as session:
        run = await service.get_run(session, cid, uid, rid)
        return {'items': await for_run(session, run)}


async def create_in_session(session, run, attempt, payload, *, uid, execution_id=None, result_revision=None):
    """绑定真实来源并幂等登记意见，与模型结果可在同一事务落库。

    Args:
        session：已持有控制锁的短事务。
        run：已授权运行。
        attempt：真实来源，不接受模型改写。
        payload：已经通过 schema 的问题内容。
        uid：宿主 Owner。
        execution_id：上报模型身份；Owner 直接提交为空。
        result_revision：本次已确认的结构化结果版本。
    """
    if attempt.run_id != run.id or (execution_id and attempt.execution_id != execution_id):
        service.reject('WORKFLOW_ATTEMPT_NOT_FOUND', 404)
    if run.runtime_version != 2: service.reject('WORKFLOW_VERSION_REQUIRED', 422)
    if not payload.summary.strip(): service.reject('WORKFLOW_FEEDBACK_INVALID', 422)
    content = payload.model_dump(mode='json', exclude={'attempt_id'})
    request_digest = digest({**content, 'actor_user_id': uid, 'actor_execution_id': execution_id})
    old = await session.scalar(select(WorkflowFeedback).where(
        WorkflowFeedback.attempt_id == attempt.id, WorkflowFeedback.request_key == payload.request_key))
    if old:
        if old.request_digest != request_digest: service.reject('WORKFLOW_FEEDBACK_REQUEST_CONFLICT')
        return old, False
    conv = await service.owned(session, run.conversation_id, uid)
    if payload.suggested_role_id is not None:
        await service.validate_roles(session, conv, [{'kind': 'role', 'role_id': payload.suggested_role_id}], uid)
    allocation = await session.get(ExecutionAllocation, attempt.execution_id) if attempt.execution_id else None
    execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == attempt.execution_id)) if attempt.execution_id else None
    check = {}
    if payload.category == 'capability':
        from .coordination import capabilities
        caps = {r['role_id']: set(r['tools']) for r in await capabilities(session, conv, uid)}
        available = caps.get(execution.role_id if execution else None, set())
        assigned = set(allocation.tools_json) if allocation else set()
        requested = sorted(set(payload.requested_tools))
        check = {'requested_tools': requested, 'assigned_tools': sorted(assigned),
                 'available_tools': sorted(available), 'unavailable_tools': [t for t in requested if t not in available],
                 'unassigned_tools': [t for t in requested if t in available and t not in assigned]}
    now = service.now()
    item = WorkflowFeedback(
        id=uuid4().hex, run_id=run.id, attempt_id=attempt.id, actor_user_id=uid,
        actor_execution_id=execution_id, source_role_id=execution.role_id if execution else None,
        graph_revision=attempt.graph_revision, source_result_revision=result_revision if result_revision is not None else (attempt.result_json or {}).get('revision'),
        node_id=attempt.node_id, category=payload.category, summary=payload.summary.strip(), details=payload.details,
        blocking=payload.blocking, requested_tools=payload.requested_tools, capability_check=check,
        suggested_role_id=payload.suggested_role_id, handler_node_ids=[], status='open', revision=1,
        request_key=payload.request_key, request_digest=request_digest, created_at=now, updated_at=now,
    )
    session.add(item); await session.flush()
    await append_event(session, item, 'created', '已登记节点反馈', uid, execution_id, data={'attempt_id': attempt.id})
    return item, True


async def create(cid, uid, rid, payload: CreateFeedback):
    """Owner 提交反馈；同键重发不制造第二条业务事实。

    Args:
        cid：当前群。
        uid：Owner 身份。
        rid：运行 ID。
        payload：有来源尝试的反馈。
    """
    async def operation():
        async with service.control_lock, SessionLocal() as session:
            run = await service.get_run(session, cid, uid, rid)
            attempt = await session.get(WorkflowAttempt, payload.attempt_id)
            if attempt is None or attempt.run_id != rid: service.reject('WORKFLOW_ATTEMPT_NOT_FOUND', 404)
            item, fresh = await create_in_session(session, run, attempt, payload, uid=uid)
            notice = await service.changed(session, run) if fresh else None
            await session.commit()
            result = await view(session, run, item)
        if notice: await events.publish_events(notice)
        return result, fresh
    return await with_locked_retry(operation)


async def update(cid, uid, rid, fid, payload: FeedbackUpdate, *, execution_id=None):
    """人工和模型使用同一处置校验；验证和人工签字不能互相冒充。

    Args:
        cid：当前群。
        uid：宿主 Owner。
        rid：已授权运行。
        fid：本运行反馈。
        payload：版本化处置。
        execution_id：协调模型的真实执行，可空。
    """
    async def operation():
        async with service.control_lock, SessionLocal() as session:
            run = await service.get_run(session, cid, uid, rid)
            if execution_id:
                from .planning import authorized
                grant = await authorized(session, execution_id, 'workflow_feedback_update')
                if grant.run_id != rid: service.reject('WORKFLOW_GRAPH_SCOPE', 403)
            item = await session.get(WorkflowFeedback, fid)
            if not item or item.run_id != rid: service.reject('WORKFLOW_FEEDBACK_NOT_FOUND', 404)
            request_digest = digest({**payload.model_dump(mode='json'), 'actor_execution_id': execution_id})
            old = await session.scalar(select(WorkflowFeedbackEvent).where(
                WorkflowFeedbackEvent.feedback_id == fid, WorkflowFeedbackEvent.request_key == payload.request_key))
            if old:
                if old.request_digest != request_digest: service.reject('WORKFLOW_FEEDBACK_REQUEST_CONFLICT')
                return await view(session, run, item)
            if item.revision != payload.expected_revision: service.reject('WORKFLOW_FEEDBACK_REVISION_CONFLICT')
            if not payload.reason.strip(): service.reject('WORKFLOW_FEEDBACK_INVALID', 422)
            if item.status in CLOSED and payload.action != 'reopen': service.reject('WORKFLOW_FEEDBACK_STATE_CONFLICT')
            if item.status not in CLOSED and payload.action == 'reopen': service.reject('WORKFLOW_FEEDBACK_STATE_CONFLICT')
            if execution_id and (payload.manual_verification or payload.action in ('dismiss', 'accept', 'obsolete', 'coordinate', 'reopen')):
                service.reject('WORKFLOW_FEEDBACK_OWNER_REQUIRED', 403)
            data = {}
            if payload.action == 'assign':
                if not payload.handler_role_id or not payload.handler_node_ids:
                    service.reject('WORKFLOW_FEEDBACK_HANDLER_REQUIRED', 422)
                conv = await service.owned(session, cid, uid)
                await service.validate_roles(session, conv, [{'kind': 'role', 'role_id': payload.handler_role_id}], uid)
                nodes = {n['id']: n for n in run.snapshot['nodes']}
                from .engine import current_activations
                current = current_activations(run, list((await session.scalars(select(WorkflowActivation).where(WorkflowActivation.run_id == rid))).all()))
                handler_activations = {}
                for nid in payload.handler_node_ids:
                    node = nodes.get(nid)
                    assignment = run.state_json.get('assignments', {}).get(nid, {})
                    if (node is None or node['kind'] != 'role' or nid == item.node_id
                        or assignment.get('role_id', node.get('role_id')) != payload.handler_role_id):
                        service.reject('WORKFLOW_FEEDBACK_HANDLER_REQUIRED', 422)
                    activation = current.get(nid)
                    if not activation or activation.status not in ('pending', 'waiting_feedback'):
                        service.reject('WORKFLOW_FEEDBACK_HANDLER_REQUIRED', 422)
                    handler_activations[nid] = activation.id
                item.handler_role_id = payload.handler_role_id
                item.handler_node_ids = list(dict.fromkeys(payload.handler_node_ids))
                item.handler_activation_ids = handler_activations
                item.verification_attempt_id = None
                item.status = 'in_progress'
                if run.status not in service.ACTIVE:
                    if await session.scalar(select(WorkflowRun.id).where(WorkflowRun.conversation_id == cid,
                        WorkflowRun.id != rid, WorkflowRun.status.in_(service.ACTIVE))): service.reject('WORKFLOW_RUN_ACTIVE')
                    run.status = 'queued'
                data = {'handler_role_id': item.handler_role_id, 'handler_node_ids': item.handler_node_ids, 'graph_revision': run.graph_revision}
            elif payload.action == 'resolve':
                if not payload.manual_verification:
                    proof = await session.get(WorkflowAttempt, payload.verification_attempt_id) if payload.verification_attempt_id else None
                    activation = await session.get(WorkflowActivation, proof.activation_id) if proof and proof.activation_id else None
                    if (proof is None or proof.run_id != rid or proof.node_id not in item.handler_node_ids
                        or item.handler_activation_ids.get(proof.node_id) != proof.activation_id
                        or not activation or activation.selected_attempt_id != proof.id
                        or proof.status != 'completed' or (proof.result_json or {}).get('values', {}).get('feedback_resolved') is not True):
                        service.reject('WORKFLOW_FEEDBACK_EVIDENCE_REQUIRED', 422)
                    for aid in item.handler_activation_ids.values():
                        handler_activation = await session.get(WorkflowActivation, aid)
                        handler_attempt = await session.get(WorkflowAttempt, handler_activation.selected_attempt_id) if handler_activation and handler_activation.selected_attempt_id else None
                        if not handler_attempt or handler_attempt.status != 'completed':
                            service.reject('WORKFLOW_FEEDBACK_EVIDENCE_REQUIRED', 422)
                    item.verification_attempt_id = proof.id
                item.status = 'resolved'
                data = {'verification_attempt_id': item.verification_attempt_id, 'manual_verification': payload.manual_verification}
            elif payload.action == 'coordinate':
                from .coordination import coordinator
                await coordinator(session, await service.owned(session, cid, uid))
                if run.status in ('stopping', 'stopped'): service.reject('WORKFLOW_STATE_CONFLICT')
                item.status = 'open'
                item.last_dispatch_revision = 0
                item.coordination_requested = True
                item.coordination_session_id = None
                data = {'request_coordination': True}
            else:
                item.status = {'wait': 'waiting', 'review': 'review', 'dismiss': 'dismissed', 'accept': 'accepted',
                               'obsolete': 'obsolete', 'reopen': 'open'}[payload.action]
                if payload.action == 'reopen':
                    # 旧验证仍在历史中，重新打开的问题不能直接复用上次证明关闭。
                    item.handler_role_id = None; item.handler_node_ids = []; item.handler_activation_ids = {}
                    item.verification_attempt_id = None; item.coordination_session_id = None
            if payload.action != 'coordinate': item.coordination_requested = False
            item.revision += 1; item.updated_at = service.now()
            # 模型说“待复核”不是新的验证结果，不能据此自我唤醒并持续计费。
            # 后台观察到真实处理尝试结束时会另增 revision，或由 Owner 明确再次委托。
            if execution_id and payload.action == 'review': item.last_dispatch_revision = item.revision
            await append_event(session, item, payload.action, payload.reason.strip(), uid, execution_id,
                               request_key=payload.request_key, request_digest=request_digest, data=data)
            notice = await service.changed(session, run)
            await session.commit()
            result = await view(session, run, item)
        await events.publish_events(notice)
        return result
    return await with_locked_retry(operation)


async def claimable(session, run, ids, expected):
    """在新协调授权的同一事务校验版本与范围，防止重复派发或沿用旧任命。

    Args:
        session：已持有控制锁的事务。
        run：本次重规划的运行。
        ids：待合并交接的反馈 ID。
        expected：后台观察到的反馈版本，人工明确请求可空。
    """
    if await session.scalar(select(CoordinationSession.id).where(CoordinationSession.run_id == run.id,
        CoordinationSession.status.in_(('queued', 'running', 'stopping')))):
        service.reject('WORKFLOW_FEEDBACK_COORDINATION_ACTIVE')
    if expected is not None:
        from .engine import check_run
        await check_run(session, run)
    items = []
    for fid in dict.fromkeys(ids):
        item = await session.get(WorkflowFeedback, fid)
        if not item or item.run_id != run.id: service.reject('WORKFLOW_FEEDBACK_NOT_FOUND', 404)
        if item.status in CLOSED: service.reject('WORKFLOW_FEEDBACK_STATE_CONFLICT')
        if expected is not None:
            if item.revision != expected.get(fid) or item.last_dispatch_revision >= item.revision:
                service.reject('WORKFLOW_FEEDBACK_REVISION_CONFLICT')
            if not item.coordination_requested and run.snapshot.get('feedback_mode') != 'automatic':
                service.reject('WORKFLOW_FEEDBACK_OWNER_REQUIRED', 403)
            if not await source_is_current(session, run, item): service.reject('WORKFLOW_FEEDBACK_STATE_CONFLICT')
            if item.coordination_session_id:
                prior = await session.get(CoordinationSession, item.coordination_session_id)
                if not prior or prior.status not in ('queued', 'running', 'completed'):
                    service.reject('WORKFLOW_COORDINATION_REVOKED', 403)
                from .coordination import coordinator
                conv = await service.owned(session, run.conversation_id, run.owner_id)
                await coordinator(session, conv, expected_id=prior.role_id, expected_revision=prior.appointment_revision)
        items.append(item)
    return items


async def observe_handlers(session, run):
    """实际处理尝试全部结束后才交回复核；失败和未知也保留原终态。

    Args:
        session：调度器的短事务。
        run：已授权运行。
    """
    from .engine import TERMINAL
    touched = False
    items = list((await session.scalars(select(WorkflowFeedback).where(
        WorkflowFeedback.run_id == run.id, WorkflowFeedback.status.in_(OPEN)))).all())
    for item in items:
        if not item.handler_activation_ids or not await source_is_current(session, run, item): continue
        attempts = []
        for aid in item.handler_activation_ids.values():
            activation = await session.get(WorkflowActivation, aid)
            attempt = await session.get(WorkflowAttempt, activation.selected_attempt_id) if activation and activation.selected_attempt_id else None
            if not attempt or attempt.status not in TERMINAL: break
            attempts.append(attempt)
        else:
            proof = max(attempts, key=lambda a: (a.created_at, a.id))
            if item.verification_attempt_id == proof.id: continue
            item.verification_attempt_id = proof.id
            item.status = 'review'; item.revision += 1; item.updated_at = service.now()
            await append_event(session, item, 'verification', '处理尝试已结束，等待核对实际结果', run.owner_id,
                data={'attempt_ids': [a.id for a in attempts], 'verification_attempt_id': proof.id}, actor_kind='system')
            # Owner 明确委托的一次反馈处理包含处理后的复核，沿用原任命；不扩大为整个运行的自动管理。
            grant = await session.get(CoordinationSession, item.coordination_session_id) if item.coordination_session_id else None
            conv = await session.get(Conversation, run.conversation_id)
            if (grant and grant.status in ('queued', 'running', 'completed') and conv.orchestrator_enabled
                and conv.orchestrator_role_id == grant.role_id and conv.orchestrator_revision == grant.appointment_revision):
                item.coordination_requested = True
            touched = True
    return touched


async def dispatch_pending():
    """合并同一运行的待处置反馈，每个版本最多派发一次，复用原链预算。

    这里只建立新的明确协调授权，不在控制锁中执行模型；失败不自动重试计费。
    """
    batches = []
    async with service.control_lock, SessionLocal() as session:
        runs = list((await session.scalars(select(WorkflowRun).join(WorkflowFeedback, WorkflowFeedback.run_id == WorkflowRun.id)
            .where(WorkflowRun.runtime_version == 2, WorkflowRun.status.not_in(('stopping', 'stopped')),
                   WorkflowFeedback.status.in_(('open', 'review')), WorkflowFeedback.last_dispatch_revision < WorkflowFeedback.revision)
            .distinct())).all())
        for run in runs:
            if await session.scalar(select(CoordinationSession.id).where(CoordinationSession.run_id == run.id,
                CoordinationSession.status.in_(('queued', 'running', 'stopping')))): continue
            items = list((await session.scalars(select(WorkflowFeedback).where(WorkflowFeedback.run_id == run.id,
                WorkflowFeedback.status.in_(('open', 'review')), WorkflowFeedback.last_dispatch_revision < WorkflowFeedback.revision))).all())
            selected = []
            for item in items:
                automatic = run.status in service.ACTIVE and run.snapshot.get('feedback_mode') == 'automatic'
                attempt = await session.get(WorkflowAttempt, item.attempt_id)
                if ((automatic or item.coordination_requested) and attempt and attempt.status not in ('pending', 'queued', 'running')
                    and await source_is_current(session, run, item)):
                    selected.append(item)
            if not selected: continue
            conv = await session.get(Conversation, run.conversation_id)
            from .graph_service import target
            _, number, _ = await target(session, run.conversation_id, run.owner_id, 'run', run.id)
            batches.append((run.conversation_id, run.owner_id, run.id, conv.orchestrator_role_id, number,
                            {i.id: i.revision for i in selected[:20]}))
    for cid, uid, rid, role_id, number, revisions in batches:
        try:
            if not role_id: service.reject('ORCHESTRATOR_REQUIRED', 422)
            from .graph_schemas import Coordinate
            from .planning import start
            await start(cid, uid, Coordinate(role_id=role_id, mode='replan', run_id=rid,
                goal='处理所关联的节点反馈；核对实际依据，必要时局部补图并关联处理节点，验证完成后复核。',
                request_key='feedback-' + digest(revisions)[:48], expected_graph_revision=number,
                feedback_ids=list(revisions)), feedback_revisions=revisions)
        except HTTPException as exc:
            code = exc.detail if isinstance(exc.detail, str) else exc.detail.get('code')
            # 版本竞争由下一次最新快照处理，不把正常竞争报告成任务故障。
            if code in ('WORKFLOW_FEEDBACK_REVISION_CONFLICT', 'WORKFLOW_FEEDBACK_COORDINATION_ACTIVE', 'WORKFLOW_GRAPH_REVISION_CONFLICT'): continue
            async with service.control_lock, SessionLocal() as session:
                run = await session.get(WorkflowRun, rid)
                for fid, revision in revisions.items():
                    item = await session.get(WorkflowFeedback, fid)
                    if not item or item.revision != revision: continue
                    item.status = 'waiting'; item.revision += 1; item.last_dispatch_revision = item.revision
                    item.coordination_requested = False; item.updated_at = service.now()
                    await append_event(session, item, 'wait', '反馈协调未启动，需要人工检查', uid, data={'error_code': code}, actor_kind='system')
                notice = await service.changed(session, run)
                await session.commit()
            await events.publish_events(notice)
