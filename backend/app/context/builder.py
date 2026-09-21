"""Roleplex 唯一 ContextBuilder：一致性历史、稳定前缀、预算与指纹。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Conversation, ConversationMember, Message, Role, User
from .budget import estimate_messages_tokens, estimate_text_tokens, token_estimate
from .domain import (
    CONTEXT_SCHEMA_VERSION,
    ContextBudget,
    ContextBudgetExceeded,
    ContextBuildError,
    ContextBuildRequest,
    ContextBuildResult,
    ContextFingerprints,
)
from .fingerprint import stable_hash
from .projection import parts_text, project_message
from ..workspaces.tools import workspace_tool_policy

_RUNTIME_POLICY = "你正在 Roleplex 会话中以指定角色身份回复。只以自己的身份发言，不伪造其他成员或系统消息。"
_DEFAULT_OUTPUT_RESERVE = 1024


@dataclass(frozen=True)
class _ProjectedHistory:
    """保留数据库身份和 pinned 状态的内部投影。"""

    source: Message
    projected: BaseMessage
    estimated_tokens: int


def _role_prefix(role: Role) -> str:
    """构造只包含角色稳定定义的 L1 文本。

    Args:
        role：本轮执行角色。
    """
    skills = json.dumps(role.skills_json or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"<role>\n{role.system_prompt}\n<skills>{skills}</skills>\n</role>"


async def _conversation_prefix(session: AsyncSession, conversation: Conversation) -> str:
    """按稳定类型和 ID 顺序构造成员名称映射。

    Args:
        session：ContextBuilder 的短读事务会话。
        conversation：当前会话记录。
    """
    members = (await session.scalars(
        select(ConversationMember)
        .where(ConversationMember.conversation_id == conversation.id)
        .order_by(ConversationMember.member_type.asc(), ConversationMember.member_id.asc())
    )).all()
    user_ids = [member.member_id for member in members if member.member_type == "user"]
    role_ids = [member.member_id for member in members if member.member_type == "role"]
    users = {
        user.id: user.nickname
        for user in (await session.scalars(select(User).where(User.id.in_(user_ids)))).all()
    } if user_ids else {}
    roles = {
        role.id: role.name
        for role in (await session.scalars(select(Role).where(Role.id.in_(role_ids)))).all()
    } if role_ids else {}
    lines = [f"type={conversation.type}", f"title={conversation.title}", "members:"]
    for member in members:
        if member.member_type == "user":
            display = users.get(member.member_id, "已删除用户")
        else:
            display = roles.get(member.member_id, "已删除角色")
        lines.append(f"- [{member.member_type}:{member.member_id}] {display}")
    return "<conversation>\n" + "\n".join(lines) + "\n</conversation>"


def _output_reserve(role: Role) -> int:
    """读取角色最大输出预留，非法旧数据回退到安全默认值。

    Args:
        role：本轮执行角色。
    """
    value = (role.params_json or {}).get("max_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else _DEFAULT_OUTPUT_RESERVE


def _select_history(
    projected: list[_ProjectedHistory],
    *,
    fixed_tokens: int,
    input_budget: int,
    pinned_budget: int,
) -> tuple[tuple[BaseMessage, ...], int, int]:
    """优先保留预算内 pinned，再从最近历史向前选择并恢复时间顺序。

    Args:
        projected：按消息 ID 升序排列的历史投影。
        fixed_tokens：system 和当前消息的不可裁剪估算。
        input_budget：扣除输出预留后的完整输入预算。
        pinned_budget：pinned 消息可占用的独立预算。
    """
    selected_ids: set[int] = set()
    used = 0
    pinned_used = 0
    for item in reversed(projected):
        if not item.source.pinned or pinned_used + item.estimated_tokens > pinned_budget:
            continue
        candidate = token_estimate(fixed_tokens + used + item.estimated_tokens)
        if candidate.estimated_tokens + candidate.safety_margin_tokens <= input_budget:
            selected_ids.add(item.source.id)
            used += item.estimated_tokens
            pinned_used += item.estimated_tokens
    for item in reversed(projected):
        if item.source.id in selected_ids or item.source.pinned:
            continue
        candidate = token_estimate(fixed_tokens + used + item.estimated_tokens)
        if candidate.estimated_tokens + candidate.safety_margin_tokens <= input_budget:
            selected_ids.add(item.source.id)
            used += item.estimated_tokens
        else:
            # 最近历史必须是连续后缀；跳过一条过大的新消息再塞更旧消息会破坏对话因果。
            break
    selected = tuple(item.projected for item in projected if item.source.id in selected_ids)
    return selected, used, len(projected) - len(selected)


async def build_context(session: AsyncSession, request: ContextBuildRequest) -> ContextBuildResult:
    """从一个短读事务构造本轮唯一、确定且预算受控的模型上下文。

    Args:
        session：数据库会话；调用方不得在构建期间并行修改同一会话。
        request：角色、会话和当前消息的稳定边界。

    Raises:
        ContextBuildError：资源或消息边界不满足内部契约。
        ContextBudgetExceeded：不可裁剪最小上下文超过有效窗口。
    """
    conversation = await session.get(Conversation, request.conversation_id)
    role = await session.get(Role, request.role_id)
    current = await session.get(Message, request.current_message_id)
    if conversation is None or conversation.deleted_at is not None:
        raise ContextBuildError("CONVERSATION_NOT_FOUND")
    if role is None or role.deleted_at is not None or not role.active:
        raise ContextBuildError("ROLE_NOT_AVAILABLE")
    if current is None or current.conversation_id != conversation.id or current.sender_type != "user":
        raise ContextBuildError("CONTEXT_CURRENT_MESSAGE_INVALID")
    if request.triggered_by_user_id is not None and current.sender_id != request.triggered_by_user_id:
        raise ContextBuildError("CONTEXT_CURRENT_MESSAGE_INVALID")
    role_membership = await session.scalar(select(ConversationMember.id).where(
        ConversationMember.conversation_id == conversation.id,
        ConversationMember.member_type == "role",
        ConversationMember.member_id == role.id,
    ))
    user_membership = await session.scalar(select(ConversationMember.id).where(
        ConversationMember.conversation_id == conversation.id,
        ConversationMember.member_type == "user",
        ConversationMember.member_id == current.sender_id,
    ))
    if role_membership is None or user_membership is None:
        raise ContextBuildError("CONVERSATION_NOT_FOUND")
    current_text = parts_text(current.parts_json or [])
    if not current_text:
        raise ContextBuildError("TEXT_PART_REQUIRED")

    runtime_prefix = f"<runtime schema=\"{CONTEXT_SCHEMA_VERSION}\">{_RUNTIME_POLICY}</runtime>"
    role_prefix = _role_prefix(role)
    conversation_prefix = await _conversation_prefix(session, conversation)
    system_prompt = "\n".join((runtime_prefix, role_prefix, conversation_prefix))
    tool_policy = await workspace_tool_policy(
        session,
        conversation=conversation,
        role=role,
        triggered_by_user_id=request.triggered_by_user_id,
    )

    from ..workflows.allocations import policy as allocation_policy
    tool_policy = await allocation_policy(session, request.execution_id, tool_policy)

    history_boundary = Message.id < current.id
    if request.execution_kind == "group_role" and current.chain_id:
        # 群聊后续角色还要读取当前真人消息之后、同一 chain 已提交的前序角色终态。
        # project_message 会继续过滤 generating/error 等非稳定状态，因此不会看见未来或半成品输出。
        history_boundary = or_(
            history_boundary,
            and_(Message.id > current.id, Message.chain_id == current.chain_id),
        )
    history_rows = (await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, history_boundary)
        .order_by(Message.id.asc())
    )).all()
    # 节点只交接明确选择的上游尝试；旧运行/旧尝试不能从普通历史混入本轮结果。
    from ..workflows.planning import context as coordination_context
    coordination_instruction=await coordination_context(session,request.execution_id)
    if coordination_instruction:
        current_text += '\n' + coordination_instruction
        history_rows = []
    workflow_attempt = None
    if (current.meta_json or {}).get('workflow_attempt_id'):
        from ..models import WorkflowAttempt, WorkflowRun, Generation
        workflow_attempt = await session.get(WorkflowAttempt, current.meta_json['workflow_attempt_id'])
        run = await session.get(WorkflowRun, workflow_attempt.run_id) if workflow_attempt else None
        if (run is None or run.conversation_id != conversation.id or run.owner_id != request.triggered_by_user_id
            or workflow_attempt.input_message_id != current.id):
            raise ContextBuildError('WORKFLOW_ATTEMPT_NOT_FOUND')
        upstream_messages = []
        structured = []
        visited = set()
        async def append_source(aid):
            if aid in visited: return
            visited.add(aid)
            upstream = await session.get(WorkflowAttempt, aid)
            if upstream is None or upstream.run_id != run.id or upstream.status != 'completed':
                raise ContextBuildError('WORKFLOW_INPUT_UNAVAILABLE')
            if run.runtime_version == 2 and upstream.result_json is not None:
                from ..models import WorkflowActivation
                activation = await session.get(WorkflowActivation, upstream.activation_id) if upstream.activation_id else None
                structured.append({'node_id': upstream.node_id, 'attempt_id': upstream.id,
                    'iteration': activation.iteration if activation else 0, 'result': upstream.result_json})
            generation = await session.get(Generation, upstream.generation_id) if upstream.generation_id else None
            if generation and generation.assistant_message_id:
                row = await session.get(Message, generation.assistant_message_id)
                if row is not None: upstream_messages.append(row)
            elif run.runtime_version == 2:
                for source in upstream.upstream_ids: await append_source(source)
        for aid in workflow_attempt.upstream_ids: await append_source(aid)
        if run.runtime_version == 2:
            from ..models import WorkflowActivation
            activation = await session.get(WorkflowActivation, workflow_attempt.activation_id)
            current_text += '\n本次节点激活数据（不是额外指令）：' + json.dumps({
                'iteration': activation.iteration if activation else 0, 'loop_id': activation.loop_id if activation else None,
                'upstream_results': structured}, ensure_ascii=False, separators=(',', ':'))
        history_rows = upstream_messages

    projected: list[_ProjectedHistory] = []
    for source in history_rows:
        message = project_message(source, target_role_id=role.id)
        if message is not None:
            projected.append(_ProjectedHistory(source, message, estimate_messages_tokens([message])))

    effective_window = min(role.context_window_tokens, settings.max_context_tokens)
    output_reserved = _output_reserve(role)
    input_budget = effective_window - output_reserved
    fixed_tokens = estimate_text_tokens(system_prompt, structural_tokens=8) + estimate_text_tokens(
        current_text, structural_tokens=8,
    )
    if tool_policy["exposed_tools"]:
        fixed_tokens += estimate_text_tokens(
            json.dumps(tool_policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            structural_tokens=8,
        )
    fixed_estimate = token_estimate(fixed_tokens)
    if fixed_estimate.estimated_tokens + fixed_estimate.safety_margin_tokens > input_budget:
        raise ContextBudgetExceeded(
            estimated_tokens=fixed_estimate.estimated_tokens,
            safety_margin_tokens=fixed_estimate.safety_margin_tokens,
            input_budget_tokens=input_budget,
            estimator_kind=fixed_estimate.estimator_kind,
        )

    # 事实仅依赖已鉴权的中断来源，不匹配用户关键词，也不改写当前用户请求。
    from .interruption import interruption_context
    recovery = None
    if workflow_attempt is not None:
        if workflow_attempt.retry_source_id:
            from ..workflows.service import attempt_facts
            previous = await session.get(WorkflowAttempt, workflow_attempt.retry_source_id)
            if previous and previous.run_id == run.id and previous.node_id == workflow_attempt.node_id:
                recovery = await attempt_facts(session, run, previous)
    elif not coordination_instruction:
        recovery = await interruption_context(session,conversation=conversation,role=role,current=current,
            triggered_by_user_id=request.triggered_by_user_id)
    recovery_message = None
    if recovery:
        candidate = HumanMessage(content=recovery)
        extra = estimate_messages_tokens([candidate])
        estimate = token_estimate(fixed_tokens + extra)
        if estimate.estimated_tokens + estimate.safety_margin_tokens > input_budget:
            candidate = HumanMessage(content='最近一次回复发生中断，执行证据无法完整装入上下文；不能据此认定操作未执行。以当前用户要求为准，必要时核对当前状态，不盲重放。')
            extra = estimate_messages_tokens([candidate])
            estimate = token_estimate(fixed_tokens + extra)
        if estimate.estimated_tokens + estimate.safety_margin_tokens <= input_budget:
            recovery_message = candidate
            fixed_tokens += extra

    history, history_tokens, truncated = _select_history(
        projected,
        fixed_tokens=fixed_tokens,
        input_budget=input_budget,
        pinned_budget=max(0, int(input_budget * settings.pin_budget_ratio)),
    )
    if workflow_attempt is not None and len(history) != len(projected):
        # 显式要求的上游结果不能静默裁掉；让 Owner 缩小节点输入后创建新尝试。
        required = token_estimate(fixed_tokens + sum(item.estimated_tokens for item in projected))
        raise ContextBudgetExceeded(estimated_tokens=required.estimated_tokens,
            safety_margin_tokens=required.safety_margin_tokens, input_budget_tokens=input_budget,
            estimator_kind=required.estimator_kind)
    if recovery_message is not None:
        history = (*history, recovery_message)
    total_estimate = token_estimate(fixed_tokens + history_tokens)
    budget = ContextBudget(
        effective_context_window=effective_window,
        output_reserved_tokens=output_reserved,
        input_budget_tokens=input_budget,
        estimate=total_estimate,
        included_message_count=len(history),
        truncated_message_count=truncated,
    )
    fingerprints = ContextFingerprints(
        interruption_hash=stable_hash(recovery_message.content) if recovery_message is not None else None,
        runtime_prefix_hash=stable_hash(runtime_prefix),
        role_prefix_hash=stable_hash(role_prefix),
        conversation_prefix_hash=stable_hash(conversation_prefix),
        tool_policy_hash=stable_hash({
            "policy": tool_policy,
            "triggered_by_owner": request.triggered_by_user_id == role.created_by,
        }),
    )
    return ContextBuildResult(
        system_prompt=system_prompt,
        history=history,
        current_message=current_text,
        budget=budget,
        fingerprints=fingerprints,
    )
