"""Agent 循环与框架防腐层。

本模块是项目里唯一允许出现 LangGraph / LangChain 私有事件名的地方：它把
`astream_events` 的框架事件翻译成 `app.agent.domain` 中的领域事件，业务层因此
不会与某个框架版本的事件形态耦合。新增框架事件映射只改本文件，不改调度和存储。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from .domain import AgentEvent, MessageDone, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from .tools import REJECTED_OUTPUT_PREFIX, summarize_args

logger = logging.getLogger("roleplex.agent.loop")

# react 循环的最大步数；触顶后改为一次禁用工具的收尾调用，保证有文本结尾。
DEFAULT_RECURSION_LIMIT = 15

# 触顶后要求模型直接收尾的提示，不再允许调用工具。
_WRAP_UP_PROMPT = "已达到本轮工具调用上限，请基于已有信息直接给出最终答复，不要再调用工具。"

# 框架事件名集中在此，业务层不感知。
_EVENT_MODEL_STREAM = "on_chat_model_stream"
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


def _tool_status(output: Any) -> str:
    """判断工具结束事件的结果状态。

    被执行层拒绝的调用是正常结束而不是错误，因此单独标记为 rejected，
    让业务层可以区分"工具失败"和"权限不允许"。
    """
    text = getattr(output, "content", output)
    if isinstance(text, str) and text.startswith(REJECTED_OUTPUT_PREFIX):
        return "rejected"
    if getattr(output, "status", "success") == "error":
        return "error"
    return "ok"


async def _wrap_up(
    model: BaseChatModel, messages: list[Any], accumulated: str, usage: dict[str, Any]
) -> AsyncIterator[AgentEvent]:
    """追加一次禁用工具的收尾调用，保证本轮有文本结尾。

    收尾调用只带原始输入和收尾提示，**不带那条包含未完成工具调用的助手消息**：
    把未配对的 tool_use 再发回厂商会被直接拒绝（400），反而让本轮彻底失败。
    """
    try:
        final = await model.ainvoke([*messages, ("user", _WRAP_UP_PROMPT)])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield ProviderError(code=_error_code(exc), message=str(exc))
        return
    text = _chunk_text(final)
    if text:
        yield TextDelta(text=text)
    yield MessageDone(text=accumulated + text, usage=usage)


async def run_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    prompt: str,
    system_prompt: str | None = None,
    history: Sequence[BaseMessage] | None = None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> AsyncIterator[AgentEvent]:
    """执行一次 Agent 循环，按领域事件流式产出结果。

    Args:
        model：已按角色配置构造好的模型；fake provider 与真实 provider 走同一路径。
        tools：本次可用的工具，调用方必须已经过执行层危险分级包装。
        prompt：触发本轮的用户文本。
        system_prompt：角色的系统提示词。
        history：更早的对话历史，按框架消息类型传入。
        recursion_limit：react 循环步数上限。

    Yields:
        `TextDelta` / `ToolCallStarted` / `ToolCallFinished` / `MessageDone` / `ProviderError`。
        正常结束以 `MessageDone` 收尾，失败以 `ProviderError` 收尾，两者互斥。

    Raises:
        asyncio.CancelledError：调用方取消本次生成时原样向上传播，
            由调度层按 stopped 收尾，不在此处伪装成错误事件。
    """
    agent = create_react_agent(model, list(tools), prompt=system_prompt)
    messages: list[Any] = [*(history or []), ("user", prompt)]
    accumulated = ""
    usage: dict[str, Any] = {}
    started_at: dict[str, float] = {}
    # 最近一次模型回合中尚未拿到结果的工具调用数量。
    # 实测锁定版本的 LangGraph 在达到步数上限时**不会抛异常**，而是直接结束事件流，
    # 留下一条带未配对 tool_use 的助手消息；因此触顶只能靠这个计数自行识别。
    pending_tool_calls = 0

    try:
        async for event in agent.astream_events(
            {"messages": messages}, version="v2", config={"recursion_limit": recursion_limit}
        ):
            kind = event["event"]
            if kind == _EVENT_MODEL_STREAM:
                text = _chunk_text(event["data"].get("chunk"))
                if text:
                    accumulated += text
                    yield TextDelta(text=text)
            elif kind == _EVENT_MODEL_END:
                output = event["data"].get("output")
                pending_tool_calls = len(getattr(output, "tool_calls", None) or [])
                metadata = getattr(output, "usage_metadata", None)
                if metadata:
                    usage = dict(metadata)
            elif kind == _EVENT_TOOL_START:
                call_id = str(event.get("run_id"))
                started_at[call_id] = loop_time()
                yield ToolCallStarted(
                    call_id=call_id,
                    tool_name=event.get("name", ""),
                    args_summary=summarize_args(event["data"].get("input")),
                )
            elif kind == _EVENT_TOOL_END:
                call_id = str(event.get("run_id"))
                output = event["data"].get("output")
                pending_tool_calls = max(0, pending_tool_calls - 1)
                yield ToolCallFinished(
                    call_id=call_id,
                    tool_name=event.get("name", ""),
                    status=_tool_status(output),
                    duration_ms=int((loop_time() - started_at.pop(call_id, loop_time())) * 1000),
                    output_summary=summarize_args(getattr(output, "content", output)),
                )
    except GraphRecursionError:
        # 某些版本会抛异常而不是静默结束，两条路径都走同一个收尾流程。
        logger.info("agent.recursion_limit_reached", extra={"recursion_limit": recursion_limit})
        async for event in _wrap_up(model, messages, accumulated, usage):
            yield event
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = _error_code(exc)
        logger.warning("agent.provider_failed", extra={"error_code": code, "error_type": type(exc).__name__})
        yield ProviderError(code=code, message=str(exc))
        return

    if pending_tool_calls:
        logger.info(
            "agent.unresolved_tool_calls",
            extra={"recursion_limit": recursion_limit, "pending_tool_calls": pending_tool_calls},
        )
        async for event in _wrap_up(model, messages, accumulated, usage):
            yield event
        return

    yield MessageDone(text=accumulated, usage=usage)
