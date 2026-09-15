"""Agent 循环与框架防腐层。

本模块是项目里唯一允许出现 LangGraph / LangChain 私有事件名的地方：它把
`astream_events` 的框架事件翻译成 `app.agent.domain` 中的领域事件，业务层因此
不会与某个框架版本的事件形态耦合。新增框架事件映射只改本文件，不改调度和存储。
"""
from __future__ import annotations

import asyncio
import logging
import json
from uuid import uuid4
from collections.abc import AsyncIterator, Sequence, Awaitable
from contextlib import aclosing
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableBinding, RunnableLambda
from langchain_core.callbacks.manager import adispatch_custom_event
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorV1
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent, ToolNode

from .domain import ToolCallsNotDispatched, UndispatchedTool, AgentEvent, MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from .tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX, summarize_tool_args, summarize_tool_output, command_result_summary
from .tool_capture import capture_input, capture_output

logger = logging.getLogger("roleplex.agent.loop")

# 图步数上限不是工具次数；触顶只交付系统事实，不追加收费模型请求。
DEFAULT_RECURSION_LIMIT = 15  # 仅保留旧图保护基线供诊断。
DEFAULT_DECISION_LIMIT = 8

# 框架事件名集中在此，业务层不感知。
_EVENT_MODEL_STREAM = "on_chat_model_stream"
_EVENT_MODEL_START = "on_chat_model_start"
_EVENT_MODEL_END = "on_chat_model_end"
_EVENT_TOOL_START = "on_tool_start"
_EVENT_TOOL_END = "on_tool_end"


class _DecisionBudgetReached(Exception):
    """图准备再次调用模型时，直接计数已耗尽。"""


class _InvalidResponse(Exception):
    """响应不能用于派发工具，只携带稳定错误码。"""

    def __init__(self, code: str):
        """Args:
            code：白名单错误码，不包含模型正文。
        """
        self.code = code
        super().__init__(code)


def _response_error(output: Any) -> str | None:
    """判断显式不完整响应，不从自然语言或 Token 数猜测终态。

    Args:
        output：框架归一化模型响应；只检查元数据及工具结构。
    """
    metadata = getattr(output, 'response_metadata', None) or {}
    reason = metadata.get('finish_reason') or metadata.get('stop_reason')
    if reason in {'length', 'max_tokens', 'content_filter', 'model_context_window_exceeded'} or metadata.get('status') == 'incomplete':
        return 'PROVIDER_RESPONSE_INCOMPLETE'
    if getattr(output, 'invalid_tool_calls', None):
        return 'AGENT_TOOL_CALL_ID_INVALID'
    proposals = getattr(output, 'tool_calls', None) or []
    ids = [call.get('id') for call in proposals]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        return 'AGENT_TOOL_CALL_ID_INVALID'
    if reason in {'tool_calls', 'tool_use'} and not proposals:
        return 'AGENT_TOOL_CALL_MISSING'
    return None


def _normalize_tool_response(output):
    """将可关联的坏 JSON 转成待拒绝调用，不修复参数或允许执行空参数。

    Args:
        output：模型原始消息；坏参数正文不会放入诊断或新工具参数。
    """
    if not isinstance(output, AIMessage):
        return output
    invalid = output.invalid_tool_calls or []
    if any(not isinstance(call.get('id'), str) or not call.get('id') for call in invalid):
        return output
    calls = [dict(call) for call in output.tool_calls]
    invalid_ids = []
    for call in invalid:
        calls.append({'name':call.get('name') or 'unknown_tool', 'args':{}, 'id':call['id'], 'type':'tool_call'})
        invalid_ids.append(call['id'])
    # 某些适配器会宽松补齐未闭合 JSON；原始 arguments 可用时再做严格语法检查。
    for raw in output.additional_kwargs.get('tool_calls', []) or []:
        if not isinstance(raw, dict) or not isinstance(raw.get('function'), dict):
            continue
        arguments = raw['function'].get('arguments')
        if not isinstance(arguments, str):
            continue
        try:
            parsed = json.loads(arguments)
            invalid_json = not isinstance(parsed, dict)
        except (ValueError, TypeError):
            invalid_json = True
        if invalid_json:
            for call in calls:
                if call['id'] == raw.get('id'):
                    call['args'] = {}
                    invalid_ids.append(call['id'])
    # 宿主标记始终由本次解析重建，不信任 Provider 自带同名 metadata。
    return output.model_copy(update={'tool_calls':calls, 'invalid_tool_calls':[],
        'response_metadata':{**output.response_metadata, '_roleplex_invalid_argument_ids':invalid_ids}})


class _ResponseModel(RunnableBinding):
    """保留真实模型回调，在图路由前规范化可恢复的参数拒绝。"""

    def bind_tools(self, tools, **kwargs):
        """Args:
            tools：构造时已按同一集合绑定，此处不重复覆盖厂商格式。
            kwargs：框架的可选绑定参数，当前调用不使用。
        """
        return self


class _CheckedToolNode(ToolNode):
    """在副作用之前拒绝参数错误，并向消息所有者交付确定未执行的事实。"""

    async def ainvoke(self, input, config=None, **kwargs):
        """Args:
            input：图传入的消息状态。
            config：继承的框架执行配置。
            kwargs：框架附加选项。
        """
        messages = input.get('messages', []) if isinstance(input, dict) else input
        self._invalid_argument_ids = set()
        if messages:
            code = _response_error(messages[-1])
            if code:
                raise _InvalidResponse(code)
            self._invalid_argument_ids = set(messages[-1].response_metadata.get('_roleplex_invalid_argument_ids', []))
        return await super().ainvoke(input, config, **kwargs)

    async def _arun_one(self, call, input_type, config):
        """Args:
            call：一个模型提议，保留原身份用于反馈配对。
            input_type：锁定框架提供的输入形式。
            config：当前节点的回调与取消上下文。
        """
        from .argument_errors import argument_error, rejection_text
        tool = self.tools_by_name.get(call['name'])
        detail = None
        reason = 'arguments_invalid'
        if tool is None:
            detail = argument_error('TOOL_NOT_AVAILABLE')
            reason = 'tool_unavailable'
        elif call['id'] in self._invalid_argument_ids:
            detail = argument_error('TOOL_ARGUMENT_JSON_INVALID')
        else:
            schema = tool.get_input_schema()
            try:
                if hasattr(schema, 'model_validate'): schema.model_validate(call['args'])
                else: schema.parse_obj(call['args'])
            except (ValidationError, ValidationErrorV1) as exc:
                detail = argument_error('TOOL_ARGUMENT_INVALID', exc, schema)
        if detail is not None:
            await adispatch_custom_event('roleplex_tool_not_dispatched', {
                'provider_id':call['id'], 'tool_name':call['name'] if tool is not None else 'unknown_tool',
                'reason':reason, 'argument_error':detail,
            }, config=config)
            return ToolMessage(content=rejection_text(detail), tool_call_id=call['id'], status='error')
        return await super()._arun_one(call, input_type, config)


def _error_code(exc: Exception) -> str:
    """把厂商异常翻译为稳定错误码。

    不导入任何厂商 SDK：按状态码和异常类名判断，避免为了识别错误而引入依赖。
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    name = type(exc).__name__.lower()
    if status == 429 or "ratelimit" in name:
        return "PROVIDER_RATE_LIMITED"
    if status in {401, 403} or "authentication" in name or "permission" in name:
        return "PROVIDER_AUTH_FAILED"
    if status == 400 or "badrequest" in name or "invalidrequest" in name:
        return "PROVIDER_BAD_REQUEST"
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name:
        return "PROVIDER_TIMEOUT"
    return "PROVIDER_ERROR"


def loop_time() -> float:
    """返回当前事件循环的单调时间，用于测量工具调用耗时。"""
    return asyncio.get_running_loop().time()


def _chunk_text(chunk: Any) -> str:
    """从模型流式分片中取出纯文本，忽略工具调用等非文本内容。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    # 部分厂商以结构化块返回内容，这里只保留文本块。
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _non_negative_int(value: Any) -> int | None:
    """把厂商返回的非负整数安全归一化，未知或异常值返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _first_token_value(*values: Any) -> int | None:
    """按兼容字段优先级返回第一个可用的 token 数。"""
    for value in values:
        normalized = _non_negative_int(value)
        if normalized is not None:
            return normalized
    return None


def _cache_hit_ratio(input_tokens: int | None, cache_hit_tokens: int | None) -> float | None:
    """仅用同一份厂商 usage 计算合法缓存命中比。

    Args:
        input_tokens：厂商或框架归一化后的完整输入 token。
        cache_hit_tokens：厂商报告的缓存命中 token。
    """
    if input_tokens is None or input_tokens <= 0 or cache_hit_tokens is None:
        return None
    if cache_hit_tokens > input_tokens:
        return None
    return cache_hit_tokens / input_tokens


def normalize_provider_usage(output: Any) -> dict[str, int | float | bool]:
    """将 LangChain 与常见厂商 usage 形态映射为稳定字段。

    支持标准 `usage_metadata`、OpenAI-compatible/DeepSeek 的 `token_usage`，
    以及 Anthropic 的 `usage`。没有厂商数据时返回空字典，不进行字符数估算。
    """
    standard = getattr(output, "usage_metadata", None) or {}
    response = getattr(output, "response_metadata", None) or {}
    raw = response.get("token_usage") or response.get("usage") or {}
    standard_details = standard.get("input_token_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}

    cache_hit_tokens = _first_token_value(
        standard_details.get("cache_read"),
        standard_details.get("cached_tokens"),
        raw.get("prompt_cache_hit_tokens"),
        raw.get("cache_read_input_tokens"),
        prompt_details.get("cached_tokens"),
    )
    cache_write_tokens = _first_token_value(
        standard_details.get("cache_creation"),
        standard_details.get("cache_write"),
        raw.get("cache_creation_input_tokens"),
    )
    standard_input = _first_token_value(standard.get("input_tokens"))
    raw_prompt_input = _first_token_value(raw.get("prompt_tokens"))
    raw_input = _first_token_value(raw.get("input_tokens"))
    if standard_input is not None:
        # LangChain Anthropic 适配器已把 cache read/create 加入 input_tokens，不能重复累加。
        input_tokens = standard_input
    elif raw_prompt_input is not None:
        # OpenAI-compatible/DeepSeek 的 prompt_tokens 已包含缓存命中与未命中。
        input_tokens = raw_prompt_input
    elif raw_input is not None and (
        "cache_read_input_tokens" in raw or "cache_creation_input_tokens" in raw
    ):
        # Anthropic 原始 input_tokens 明确排除缓存读写，需要恢复为完整输入口径。
        input_tokens = raw_input + (cache_hit_tokens or 0) + (cache_write_tokens or 0)
    else:
        input_tokens = raw_input
    output_tokens = _first_token_value(
        standard.get("output_tokens"), raw.get("output_tokens"), raw.get("completion_tokens")
    )
    total_tokens = _first_token_value(standard.get("total_tokens"), raw.get("total_tokens"))
    total_tokens_derived = False
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
        total_tokens_derived = True
    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cache_hit_ratio": _cache_hit_ratio(input_tokens, cache_hit_tokens),
        "total_tokens_derived": True if total_tokens_derived else None,
    }
    return {key: value for key, value in values.items() if value is not None}


def _aggregate_usage(calls: list[dict[str, int | float | bool]]) -> dict[str, int | float | None]:
    """仅在每次调用都报告某字段时汇总，避免用部分数据冒充整轮总量。"""
    keys = ("input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_write_tokens")
    result: dict[str, int | float | None] = {
        key: sum(call[key] for call in calls) if calls and all(key in call for call in calls) else None
        for key in keys
    }
    result["cache_hit_ratio"] = _cache_hit_ratio(
        result["input_tokens"] if isinstance(result["input_tokens"], int) else None,
        result["cache_hit_tokens"] if isinstance(result["cache_hit_tokens"], int) else None,
    )
    return result


def _tool_status(output: Any) -> str:
    """判断工具结束事件的结果状态。

    被执行层拒绝的调用是正常结束而不是错误，因此单独标记为 rejected，
    让业务层可以区分"工具失败"和"权限不允许"。
    """
    text = getattr(output, "content", output)
    if isinstance(text, str) and text.startswith(REJECTED_OUTPUT_PREFIX):
        return "rejected"
    if isinstance(text, str) and text.startswith(FAILED_OUTPUT_PREFIX):
        return "error"
    if getattr(output, "status", "success") == "error":
        return "error"
    return "ok"


async def run_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    prompt: str,
    system_prompt: str | None = None,
    history: Sequence[BaseMessage] | None = None,
    recursion_limit: int | None = None,
    decision_limit: int = DEFAULT_DECISION_LIMIT,
    before_decision: Callable[[int], Awaitable[bool]] | None = None,
    time_source: Callable[[], float] | None = None,
) -> AsyncIterator[AgentEvent]:
    """执行一次 Agent 循环，按领域事件流式产出结果。

    Args:
        model：已按角色配置构造好的模型；fake provider 与真实 provider 走同一路径。
        tools：本次可用的工具，调用方必须已经过执行层危险分级包装。
        prompt：触发本轮的用户文本。
        system_prompt：角色的系统提示词。
        history：更早的对话历史，按框架消息类型传入。
        recursion_limit：内部强制图保护；省略时按当前拓扑为决策和结果交接预留空间。
        decision_limit：本轮冻结的模型决策上限，1..256。
        before_decision：可选共享预算原子消费，参数为本执行的决策序号。
        time_source：用于确定性测试的单调时钟；正常运行使用事件循环时钟。

    Yields:
        `TextDelta` / `ToolCallStarted` / `ToolCallFinished` / `ProviderCallCompleted` /
        `MessageDone` / `ProviderError`。
        正常结束或图预算停止以 `MessageDone` 收尾，失败以 `ProviderError` 收尾，两者互斥。

    Raises:
        asyncio.CancelledError：调用方取消本次生成时原样向上传播，
            由调度层按 stopped 收尾，不在此处伪装成错误事件。
    """
    if isinstance(decision_limit, bool) or not isinstance(decision_limit, int) or not 1 <= decision_limit <= 256:
        raise ValueError('decision_limit must be an integer in 1..256')
    if recursion_limit is not None and (isinstance(recursion_limit, bool) or not isinstance(recursion_limit, int) or recursion_limit < 1):
        raise ValueError('recursion_limit must be a positive integer')
    graph_limit = recursion_limit if recursion_limit is not None else 2 * decision_limit + 2
    decisions = 0
    remaining_steps: int | None = None

    async def graph_prompt(state: dict) -> list:
        """在防腐层观察锁定图版本的派发预算，不把框架状态暴露给业务层。

        Args:
            state：每次模型调用前的图状态；检查决策额度、读取图余量并保留原消息。
        """
        nonlocal remaining_steps, decisions
        if decisions >= decision_limit:
            raise _DecisionBudgetReached()
        if before_decision is not None and not await before_decision(decisions + 1):
            raise _DecisionBudgetReached()
        decisions += 1
        remaining_steps = state.get('remaining_steps')
        return [*([SystemMessage(content=system_prompt)] if system_prompt else []), *state['messages']]

    from .argument_errors import execution_error_text
    tools = [tool.model_copy(update={'handle_tool_error':execution_error_text}) for tool in tools]
    bound_model = model.bind_tools(list(tools)) if tools else model
    response_model = _ResponseModel(bound=bound_model | RunnableLambda(_normalize_tool_response))
    agent = create_react_agent(response_model, _CheckedToolNode(list(tools), handle_tool_errors=False), prompt=graph_prompt)
    messages: list[Any] = [*(history or []), ("user", prompt)]
    accumulated = ""
    clock = time_source or loop_time
    call_usages: list[dict[str, int | float | bool]] = []
    started_at: dict[str, float] = {}
    provider_started_at: dict[str, float] = {}
    provider_ttft_ms: dict[str, int] = {}
    provider_call_index: dict[str, int] = {}
    provider_stream_usage: dict[str, dict[str, int | float | bool]] = {}
    next_provider_call_index = 0
    pending_ids: set[str] = set()
    protocol_broken = False
    graph_completed = False
    model_completed = False
    last_response_has_tools = False
    response_error: str | None = None
    budget_blocked = False
    blocked_ids: set[str] = set()
    blocked_calls: tuple[UndispatchedTool, ...] = ()
    known_tools = {tool.name for tool in tools}

    try:
        async with aclosing(agent.astream_events(
            {"messages": messages}, version="v2", config={"recursion_limit": graph_limit}
        )) as graph_events:
            async for event in graph_events:
                kind = event["event"]
                run_id = str(event.get("run_id"))
                if kind == 'on_chain_end' and not event.get('parent_ids'):
                    graph_completed = True
                if kind == _EVENT_MODEL_START:
                    next_provider_call_index += 1
                    provider_call_index[run_id] = next_provider_call_index
                    provider_started_at[run_id] = clock()
                    yield ProviderCallStarted(call_index=next_provider_call_index)
                elif kind == _EVENT_MODEL_STREAM:
                    chunk = event["data"].get("chunk")
                    if run_id in provider_started_at and run_id not in provider_ttft_ms:
                        provider_ttft_ms[run_id] = int((clock() - provider_started_at[run_id]) * 1000)
                    chunk_usage = normalize_provider_usage(chunk)
                    if chunk_usage:
                        provider_stream_usage[run_id] = chunk_usage
                    text = _chunk_text(chunk)
                    if text:
                        accumulated += text
                        yield TextDelta(text=text)
                elif kind == _EVENT_MODEL_END:
                    output = _normalize_tool_response(event["data"].get("output"))
                    model_completed = True
                    response_error = _response_error(output)
                    proposals = getattr(output, 'tool_calls', None) or []
                    last_response_has_tools = bool(proposals)
                    ids = [call.get('id') for call in proposals]
                    if pending_ids or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
                        protocol_broken = True
                    pending_ids.update(value for value in ids if isinstance(value, str))
                    # 与锁定版本派发条件一致，不能通过兜底文本或 pending 数量猜测触顶。
                    budget_blocked = remaining_steps is not None and bool(proposals) and remaining_steps < 2
                    blocked_ids = set(ids) if budget_blocked else set()
                    blocked_calls = tuple(UndispatchedTool(
                        call_id=uuid4().hex,
                        tool_name=call['name'] if call.get('name') in known_tools else 'unknown_tool',
                        args_summary=summarize_tool_args(call['name'], call.get('args')) if call.get('name') in known_tools else '{}',
                        private_input=capture_input(call['name'], call.get('args')) if call.get('name') in known_tools else None,
                    ) for call in proposals) if budget_blocked and not protocol_broken else ()
                    stream_usage = provider_stream_usage.pop(run_id, {})
                    normalized_usage = normalize_provider_usage(output) or stream_usage
                    call_usages.append(normalized_usage)
                    started = provider_started_at.pop(run_id, None)
                    duration_ms = int((clock() - started) * 1000) if started is not None else 0
                    yield ProviderCallCompleted(
                        call_index=provider_call_index.pop(run_id, len(call_usages)),
                        ttft_ms=provider_ttft_ms.pop(run_id, None),
                        duration_ms=duration_ms,
                        input_tokens=normalized_usage.get("input_tokens"),
                        output_tokens=normalized_usage.get("output_tokens"),
                        total_tokens=normalized_usage.get("total_tokens"),
                        cache_hit_tokens=normalized_usage.get("cache_hit_tokens"),
                        cache_write_tokens=normalized_usage.get("cache_write_tokens"),
                        cache_hit_ratio=normalized_usage.get("cache_hit_ratio"),
                        total_tokens_derived=normalized_usage.get("total_tokens_derived"),
                    )
                elif kind == 'on_custom_event' and event.get('name') == 'roleplex_tool_not_dispatched':
                    rejected = event['data']
                    if rejected['provider_id'] not in pending_ids:
                        protocol_broken = True
                    else:
                        pending_ids.remove(rejected['provider_id'])
                    yield ToolCallsNotDispatched((UndispatchedTool(call_id=uuid4().hex, tool_name=rejected['tool_name'],
                        args_summary='{}', private_input={'text':'{}','bytes':2,'truncated':False}, argument_error=rejected['argument_error']),), reason=rejected['reason'])
                elif kind == _EVENT_TOOL_START:
                    call_id = str(event.get("run_id"))
                    started_at[call_id] = clock()
                    yield ToolCallStarted(
                        call_id=call_id,
                        tool_name=event.get("name", ""),
                        args_summary=summarize_tool_args(event.get("name", ""), event["data"].get("input")),
                        private_input=capture_input(event.get('name', ''), event['data'].get('input')),
                    )
                elif kind == _EVENT_TOOL_END:
                    call_id = str(event.get("run_id"))
                    output = event["data"].get("output")
                    provider_id = getattr(output, 'tool_call_id', None)
                    if provider_id not in pending_ids:
                        protocol_broken = True
                    else:
                        pending_ids.remove(provider_id)
                    tool_started = started_at.pop(call_id, None)
                    from .write_capture import take_write_capture
                    private_output = capture_output(event.get('name', ''), output)
                    from ..workspaces.catalog import WORKSPACE_CAPTURE_TOOLS
                    if event.get('name') in WORKSPACE_CAPTURE_TOOLS:
                        private_output = take_write_capture(call_id, private_output)
                    yield ToolCallFinished(
                        call_id=call_id,
                        tool_name=event.get("name", ""),
                        status=_tool_status(output),
                        duration_ms=int((clock() - tool_started) * 1000) if tool_started is not None else 0,
                        output_summary=summarize_tool_output(getattr(output, "content", output)),
                        command_summary=command_result_summary(event.get('name', ''), output),
                        private_output=private_output,
                    )
    except _DecisionBudgetReached:
        if response_error or protocol_broken or pending_ids or started_at or provider_call_index:
            code = response_error or ('AGENT_TOOL_RESULT_MISMATCH' if protocol_broken else 'AGENT_TOOL_RESULT_MISSING')
            yield ProviderError(code=code, message=code, stop_reason='provider_failed' if code == 'PROVIDER_RESPONSE_INCOMPLETE' else 'protocol_error')
        else:
            yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages), stop_reason='decision_budget')
        return
    except _InvalidResponse as exc:
        yield ProviderError(code=exc.code, message=exc.code,
                            stop_reason='provider_failed' if exc.code == 'PROVIDER_RESPONSE_INCOMPLETE' else 'protocol_error')
        return
    except GraphRecursionError:
        if response_error:
            yield ProviderError(code=response_error, message=response_error,
                                stop_reason='provider_failed' if response_error == 'PROVIDER_RESPONSE_INCOMPLETE' else 'protocol_error')
            return
        if protocol_broken:
            yield ProviderError(code='AGENT_TOOL_RESULT_MISMATCH', message='AGENT_TOOL_RESULT_MISMATCH', stop_reason='protocol_error')
            return
        # 框架在最后一个 super-step 后抛出上限异常，不能覆盖已完整返回的无工具终态。
        if model_completed and not last_response_has_tools and not pending_ids and not started_at and not provider_call_index:
            yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages))
            return
        if blocked_calls and not started_at and pending_ids == blocked_ids:
            yield ToolCallsNotDispatched(blocked_calls)
        yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages), stop_reason='graph_budget',
                          undispatched_proposals=len(blocked_ids) if budget_blocked and not started_at else None)
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        provider_failure = bool(provider_call_index)
        code = _error_code(exc) if provider_failure else 'AGENT_RUNTIME_ERROR'
        if provider_failure:
            active_run = max(provider_call_index, key=provider_call_index.get)
            started = provider_started_at.get(active_run)
            logger.warning('provider.call_failed', extra={
                'error_code': code, 'error_type': type(exc).__name__,
                'provider_call_index': provider_call_index[active_run],
                'ttft_ms': provider_ttft_ms.get(active_run),
                'duration_ms': int((clock() - started) * 1000) if started is not None else None,
                'status': 'timeout' if code == 'PROVIDER_TIMEOUT' else 'rejected'
                    if code in {'PROVIDER_AUTH_FAILED', 'PROVIDER_BAD_REQUEST'} else 'failed',
            })
        from .argument_errors import safe_exception_type
        yield ProviderError(code=code, message=code,
                            stop_reason='provider_failed' if provider_failure else 'protocol_error',
                            error_type=safe_exception_type(exc),
                            error_phase='provider_request' if provider_failure else 'tool_execution' if started_at else 'event_processing')
        return

    if response_error:
        yield ProviderError(code=response_error, message=response_error,
                            stop_reason='provider_failed' if response_error == 'PROVIDER_RESPONSE_INCOMPLETE' else 'protocol_error')
    elif budget_blocked and graph_completed and not protocol_broken and pending_ids == blocked_ids and not started_at:
        if blocked_calls:
            yield ToolCallsNotDispatched(blocked_calls)
        yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages), stop_reason='graph_budget',
                          undispatched_proposals=len(blocked_ids))
    elif protocol_broken or pending_ids or started_at or not graph_completed or not model_completed:
        code = 'AGENT_TOOL_RESULT_MISMATCH' if protocol_broken else 'AGENT_TOOL_RESULT_MISSING' if pending_ids or started_at else 'AGENT_EVENT_STREAM_INCOMPLETE'
        yield ProviderError(code=code, message=code, stop_reason='protocol_error')
    else:
        yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages))
