"""Agent 循环与框架防腐层。

本模块是项目里唯一允许出现 LangGraph / LangChain 私有事件名的地方：它把
`astream_events` 的框架事件翻译成 `app.agent.domain` 中的领域事件，业务层因此
不会与某个框架版本的事件形态耦合。新增框架事件映射只改本文件，不改调度和存储。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from .domain import AgentEvent, MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from .tools import FAILED_OUTPUT_PREFIX, REJECTED_OUTPUT_PREFIX, summarize_tool_args, summarize_tool_output, command_result_summary

logger = logging.getLogger("roleplex.agent.loop")

# react 循环的最大步数；触顶后改为一次禁用工具的收尾调用，保证有文本结尾。
DEFAULT_RECURSION_LIMIT = 15

# 触顶后要求模型直接收尾的提示，不再允许调用工具。
_WRAP_UP_PROMPT = "已达到本轮工具调用上限，请基于已有信息直接给出最终答复，不要再调用工具。"

# 框架事件名集中在此，业务层不感知。
_EVENT_MODEL_STREAM = "on_chat_model_stream"
_EVENT_MODEL_START = "on_chat_model_start"
_EVENT_MODEL_END = "on_chat_model_end"
_EVENT_TOOL_START = "on_tool_start"
_EVENT_TOOL_END = "on_tool_end"


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


async def _wrap_up(
    model: BaseChatModel,
    messages: list[Any],
    accumulated: str,
    call_usages: list[dict[str, int | float | bool]],
    time_source: Callable[[], float],
) -> AsyncIterator[AgentEvent]:
    """追加一次禁用工具的收尾调用，保证本轮有文本结尾。

    收尾调用只带原始输入和收尾提示，**不带那条包含未完成工具调用的助手消息**：
    把未配对的 tool_use 再发回厂商会被直接拒绝（400），反而让本轮彻底失败。
    """
    started = time_source()
    call_index = len(call_usages) + 1
    yield ProviderCallStarted(call_index=call_index)
    try:
        final = await model.ainvoke([*messages, ("user", _WRAP_UP_PROMPT)])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield ProviderError(code=_error_code(exc), message=str(exc))
        return
    duration_ms = int((time_source() - started) * 1000)
    normalized_usage = normalize_provider_usage(final)
    call_usages.append(normalized_usage)
    yield ProviderCallCompleted(
        call_index=call_index,
        ttft_ms=duration_ms,
        duration_ms=duration_ms,
        input_tokens=normalized_usage.get("input_tokens"),
        output_tokens=normalized_usage.get("output_tokens"),
        total_tokens=normalized_usage.get("total_tokens"),
        cache_hit_tokens=normalized_usage.get("cache_hit_tokens"),
        cache_write_tokens=normalized_usage.get("cache_write_tokens"),
        cache_hit_ratio=normalized_usage.get("cache_hit_ratio"),
        total_tokens_derived=normalized_usage.get("total_tokens_derived"),
    )
    text = _chunk_text(final)
    if text:
        yield TextDelta(text=text)
    yield MessageDone(text=accumulated + text, usage=_aggregate_usage(call_usages))


async def run_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    prompt: str,
    system_prompt: str | None = None,
    history: Sequence[BaseMessage] | None = None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    time_source: Callable[[], float] | None = None,
) -> AsyncIterator[AgentEvent]:
    """执行一次 Agent 循环，按领域事件流式产出结果。

    Args:
        model：已按角色配置构造好的模型；fake provider 与真实 provider 走同一路径。
        tools：本次可用的工具，调用方必须已经过执行层危险分级包装。
        prompt：触发本轮的用户文本。
        system_prompt：角色的系统提示词。
        history：更早的对话历史，按框架消息类型传入。
        recursion_limit：react 循环步数上限。
        time_source：用于确定性测试的单调时钟；正常运行使用事件循环时钟。

    Yields:
        `TextDelta` / `ToolCallStarted` / `ToolCallFinished` / `ProviderCallCompleted` /
        `MessageDone` / `ProviderError`。
        正常结束以 `MessageDone` 收尾，失败以 `ProviderError` 收尾，两者互斥。

    Raises:
        asyncio.CancelledError：调用方取消本次生成时原样向上传播，
            由调度层按 stopped 收尾，不在此处伪装成错误事件。
    """
    agent = create_react_agent(model, list(tools), prompt=system_prompt)
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
    # 最近一次模型回合中尚未拿到结果的工具调用数量。
    # 实测锁定版本的 LangGraph 在达到步数上限时**不会抛异常**，而是直接结束事件流，
    # 留下一条带未配对 tool_use 的助手消息；因此触顶只能靠这个计数自行识别。
    pending_tool_calls = 0

    try:
        async for event in agent.astream_events(
            {"messages": messages}, version="v2", config={"recursion_limit": recursion_limit}
        ):
            kind = event["event"]
            run_id = str(event.get("run_id"))
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
                output = event["data"].get("output")
                pending_tool_calls = len(getattr(output, "tool_calls", None) or [])
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
            elif kind == _EVENT_TOOL_START:
                call_id = str(event.get("run_id"))
                started_at[call_id] = clock()
                yield ToolCallStarted(
                    call_id=call_id,
                    tool_name=event.get("name", ""),
                    args_summary=summarize_tool_args(event.get("name", ""), event["data"].get("input")),
                )
            elif kind == _EVENT_TOOL_END:
                call_id = str(event.get("run_id"))
                output = event["data"].get("output")
                pending_tool_calls = max(0, pending_tool_calls - 1)
                tool_started = started_at.pop(call_id, None)
                yield ToolCallFinished(
                    call_id=call_id,
                    tool_name=event.get("name", ""),
                    status=_tool_status(output),
                    duration_ms=int((clock() - tool_started) * 1000) if tool_started is not None else 0,
                    output_summary=summarize_tool_output(getattr(output, "content", output)),
                    command_summary=command_result_summary(event.get('name', ''), output),
                )
    except GraphRecursionError:
        # 某些版本会抛异常而不是静默结束，两条路径都走同一个收尾流程。
        logger.info("generation.recursion_limit_reached", extra={"recursion_limit": recursion_limit})
        async for event in _wrap_up(model, messages, accumulated, call_usages, clock):
            yield event
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = _error_code(exc)
        active_run = max(provider_call_index, key=provider_call_index.get) if provider_call_index else None
        active_started = provider_started_at.get(active_run) if active_run else None
        logger.warning(
            "provider.call_failed",
            extra={
                "error_code": code,
                "error_type": type(exc).__name__,
                "provider_call_index": provider_call_index.get(active_run) if active_run else None,
                "ttft_ms": provider_ttft_ms.get(active_run) if active_run else None,
                "duration_ms": int((clock() - active_started) * 1000) if active_started is not None else None,
                "status": (
                    "timeout" if code == "PROVIDER_TIMEOUT"
                    else "rejected" if code in {"PROVIDER_AUTH_FAILED", "PROVIDER_BAD_REQUEST"}
                    else "failed"
                ),
            },
        )
        yield ProviderError(code=code, message=str(exc))
        return

    if pending_tool_calls:
        logger.info(
            "tool.calls_unresolved",
            extra={"recursion_limit": recursion_limit, "pending_tool_calls": pending_tool_calls},
        )
        async for event in _wrap_up(model, messages, accumulated, call_usages, clock):
            yield event
        return

    yield MessageDone(text=accumulated, usage=_aggregate_usage(call_usages))
