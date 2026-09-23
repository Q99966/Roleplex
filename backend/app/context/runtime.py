"""每次 Provider 派发前的实际容量检查与执行私有压缩；原工具记录永不重放或改写。"""
import asyncio
import json
from time import monotonic
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import AgentExecution, ConversationContext, Generation, Message, Role
from . import automatic, compaction, policy
from .budget import estimate_messages_tokens, estimate_request, message_content_text
from .compaction_schema import Start, private_text
from .domain import ContextBudgetExceeded, ContextBuildError
from .fingerprint import stable_hash


def closed(messages):
    """只有全部 call/result 唯一配对且没有悬挂工具调用才可替换。"""
    pending = set()
    seen = set()
    for message in messages:
        calls = getattr(message, 'tool_calls', []) or []
        for call in calls:
            key = call.get('id')
            if not key or key in seen:
                return False
            pending.add(key); seen.add(key)
        if message.type == 'tool':
            key = message.tool_call_id
            if key not in pending:
                return False
            pending.remove(key)
    return not pending


def unit(message, identity):
    payload = {'type': message.type, 'content': message_content_text(message),
        **{key: getattr(message, key) for key in ['tool_calls', 'tool_call_id', 'name', 'status'] if getattr(message, key, None)}}
    return {'sources': [identity], 'text': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}


class RuntimeContext:
    """一次 execution 的输入适配器；摘要正文只在内存中，数据库只留任务与数字凭据。"""
    def __init__(self, request, context):
        self.request, self.context = request, context
        self.anchor = len(context.history) + 1  # 完整 messages 的 system 后当前用户任务位置。
        self.history_end = 1 + len(context.material_snapshot['sources'])
        self.history_summary = None
        self.tool_summary = None
        self.covered = self.anchor + 1
        self.attempted = set()
        self.last_attempt = {}
        self.receipt = {}
        self.receipts = {}
        self.tool_results_recorded = 0
        self.tool_progress = asyncio.Event()

    def recorded_tool_results(self, count=1):
        """由原消息所有者在记录实际工具终态后交接；图状态领先落库时不能摘要旧事实。"""
        self.tool_results_recorded += count
        self.tool_progress.set()

    async def wait_tool_results(self, count):
        while self.tool_results_recorded < count:
            self.tool_progress.clear()
            try:
                await asyncio.wait_for(self.tool_progress.wait(), timeout=30)
            except TimeoutError:
                raise ContextBuildError('AGENT_TOOL_RESULT_MISSING') from None

    def adapted(self, messages):
        history = [self.history_summary, *messages[self.history_end:self.anchor]] if self.history_summary else messages[1:self.anchor]
        return [messages[0], *history, messages[self.anchor],
            *([self.tool_summary] if self.tool_summary else []), *messages[self.covered:]]

    async def compact(self, inputs, view, *, kind, urgent=False, new_tokens=None):
        """工具来源与精确上游各在本执行内汇总；摘要不能成为可检索的会话材料。"""
        from fastapi import HTTPException
        request = self.request
        effective = view['effective']
        amount = estimate_messages_tokens(inputs)
        previous = self.last_attempt.get(kind)
        growth = amount if new_tokens is None else new_tokens
        if not urgent and (growth < effective['min_new_tokens'] or previous is not None and monotonic() - previous < effective['cooldown_seconds']):
            return None
        units = [unit(message, index + 1) for index, message in enumerate(inputs)]
        signature = stable_hash(units)
        if signature in self.attempted:
            return None
        self.attempted.add(signature)
        self.last_attempt[kind] = monotonic()
        async with SessionLocal() as session:
            await automatic.validate_parent(session, request.execution_id, request.conversation_id, request.triggered_by_user_id)
            state = await session.get(ConversationContext, request.conversation_id)
            revision = state.revision
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == request.execution_id))
            generation = await session.get(Generation, execution.generation_id)
            message = await session.get(Message, generation.assistant_message_id)
            # 保留服务器的副作用事实，模型摘要无权将 unknown/未派发改成成功。
            facts = [{key: part[key] for key in ('call_id', 'tool_name', 'status', 'effect_state', 'confirmed_applied_items') if key in part}
                for part in (message.parts_json or []) if part.get('type') == 'tool_call'] if message else []
        runtime = {'parent_execution_id': request.execution_id, 'policy_stamp': view['stamp'], 'scope': 'execution',
            'boundary': self.context.material_snapshot['visible_through_message_id'],
            'source_signature': signature, 'input_tokens': amount, 'unit_count': len(units), 'kind': kind,
            'sources': self.context.material_snapshot['sources'], 'execution_facts': facts}
        try:
            value = await compaction.start(request.conversation_id, request.triggered_by_user_id, Start(
                request_key='private:' + stable_hash([request.execution_id, signature, view['stamp']]),
                expected_revision=revision, role_id=effective['model_role_id'] or request.role_id,
                keep_recent=0, target_tokens=min(effective['summary_tokens'], max(128, effective['target_tokens'])),
                instructions=effective['instructions']), runtime=runtime)
            result = await automatic.execute(value['id'], private_units=units)
            if result is None:
                return None
            replacement = HumanMessage(content=private_text(result, facts))
            self.receipt = {**self.receipt, kind + '_compression_id': value['id'],
                kind + '_source_hash': signature, kind + '_source_count': len(inputs)}
            return replacement
        except (HTTPException, ContextBuildError, SQLAlchemyError):
            return None

    async def prepare(self, messages, definitions, index):
        """以实际 Schema、全部输入、输出预留与安全余量复核；不靠裁剪后数字判断历史压力。"""
        async with SessionLocal() as session:
            await automatic.validate_parent(session, self.request.execution_id, self.request.conversation_id, self.request.triggered_by_user_id)
            view = await policy.resolve(session, self.request.conversation_id)
            role = await session.get(Role, self.request.role_id)
            window = min(self.context.budget.effective_context_window, role.context_window_tokens)
        effective = view['effective']
        output = self.context.budget.output_reserved_tokens
        adapted = self.adapted(messages)
        estimate = estimate_request(adapted, definitions)
        fixed = estimate_request([messages[0], messages[self.anchor]], definitions)
        if fixed['estimated_tokens'] + fixed['safety_margin_tokens'] + output > window:
            raise ContextBudgetExceeded(estimated_tokens=fixed['estimated_tokens'], safety_margin_tokens=fixed['safety_margin_tokens'],
                input_budget_tokens=window - output, estimator_kind=fixed['estimator_kind'])
        pressured = lambda e: e['estimated_tokens'] >= effective['trigger_tokens'] or e['estimated_tokens'] + e['safety_margin_tokens'] + output > window
        if effective['enabled'] and pressured(estimate):
            await self.wait_tool_results(sum(message.type == 'tool' for message in messages[self.anchor + 1:]))
            urgent = estimate['estimated_tokens'] + estimate['safety_margin_tokens'] + output > window
            if self.context.material_snapshot['scope'] == 'workflow_upstream' and not self.history_summary and self.anchor > 1:
                self.history_summary = await self.compact(messages[1:self.history_end], view, kind='upstream', urgent=urgent)
            rounds = messages[self.covered:]
            if rounds and closed(rounds):
                replacement = await self.compact([*([self.tool_summary] if self.tool_summary else []), *rounds], view, kind='tools',
                    urgent=urgent, new_tokens=estimate_messages_tokens(rounds))
                if replacement:
                    self.tool_summary = replacement
                    self.covered = len(messages)
            adapted = self.adapted(messages)
            estimate = estimate_request(adapted, definitions)
        if estimate['estimated_tokens'] + estimate['safety_margin_tokens'] + output > window:
            raise ContextBudgetExceeded(estimated_tokens=estimate['estimated_tokens'],
                safety_margin_tokens=estimate['safety_margin_tokens'], input_budget_tokens=window - output,
                estimator_kind=estimate['estimator_kind'])
        self.receipt = {**self.receipt, 'policy_stamp': view['stamp'], 'effective_context_window': window,
            'output_reserved_tokens': output, 'target_reached': estimate['estimated_tokens'] <= effective['target_tokens']}
        self.receipts[index] = self.receipt.copy()
        return adapted
