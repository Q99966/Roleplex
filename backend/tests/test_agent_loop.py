"""Agent 循环与防腐层的离线确定性测试。

全部使用脚本化模型，不联网、不消耗真实模型额度：验证框架事件被完整映射为领域事件、
工具轮的开始与结束成对出现、递归上限触顶后仍能正常收尾，以及框架私有事件名没有
泄漏到防腐层之外。
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

from app.agent.domain import MessageDone, ProviderCallCompleted, ProviderCallStarted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn, fake_reply_model
from app.agent.loop import normalize_provider_usage, run_agent
from app.agent.tools import guard_tools, summarize_tool_args, summarize_tool_output

# 只有防腐层可以出现这些框架私有名称，业务层必须只认领域事件。
_FRAMEWORK_EVENT_TOKENS = ("astream_events", "on_chat_model_stream", "on_chat_model_end", "on_tool_start", "on_tool_end")


@tool
def read_artifact(artifact_id: int) -> str:
    """读取产物内容，属于 safe 白名单工具。"""
    return f"artifact-{artifact_id}-内容"


@tool
def wipe_disk(path: str) -> str:
    """危险示例工具，用于验证执行层拦截。"""
    return "已清空"


class AlwaysToolCallModel(BaseChatModel):
    """流式路径永远发起工具调用，非流式路径给出文本。

    用于制造"工具轮没有收尾"的局面：锁定版本的 LangGraph 达到步数上限时不会抛异常，
    而是直接结束事件流并留下未配对的工具调用，因此防腐层必须自己识别并补一次收尾调用；
    收尾调用走非流式路径，所以这里可以稳定地拿到一段文本结尾。
    """

    counter: int = 0

    @property
    def _llm_type(self) -> str:
        return "always-tool-call"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "AlwaysToolCallModel":
        return self

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已达到上限，这是收尾答复"))])

    def _stream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # 每轮使用不同的调用标识，避免框架把重复调用当成同一次工具执行。
        self.counter += 1
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[{
                    "name": "read_artifact", "args": '{"artifact_id": 1}',
                    "id": f"loop_{self.counter}", "index": 0,
                }],
            )
        )

    async def _astream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages):
            yield chunk


class BrokenModel(BaseChatModel):
    """模拟厂商侧限流错误，用于验证错误码映射。"""

    @property
    def _llm_type(self) -> str:
        return "broken"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "BrokenModel":
        return self

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        raise self._error()

    def _stream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        raise self._error()

    def _error(self) -> Exception:
        error = RuntimeError("too many requests")
        error.status_code = 429  # type: ignore[attr-defined]
        return error


async def _collect(**kwargs: Any) -> list[Any]:
    """跑完一次 Agent 循环并收集全部领域事件。"""
    return [event async for event in run_agent(**kwargs)]


@pytest.mark.anyio
async def test_text_only_run_maps_to_deltas_and_done():
    """纯文本回合应产出连续增量并以 MessageDone 收尾。"""
    events = await _collect(model=fake_reply_model("你好", delay=0.0), tools=[], prompt="你好")

    assert isinstance(events[-1], MessageDone)
    deltas = [event for event in events if isinstance(event, TextDelta)]
    assert deltas, "至少应产出一条文本增量"
    assert "".join(delta.text for delta in deltas) == events[-1].text
    assert events[-1].text.startswith("已收到你的消息：你好")
    assert not any(isinstance(event, ProviderError) for event in events)


@pytest.mark.anyio
async def test_provider_call_reports_deterministic_ttft_and_duration():
    """每次模型 API 调用都报告首分片耗时与总耗时，fake 不伪造 token 数。"""
    ticks = iter([10.0, 10.125, 10.5])
    events = await _collect(
        model=fake_reply_model("计时", delay=0.0),
        tools=[],
        prompt="计时",
        time_source=lambda: next(ticks),
    )

    calls = [event for event in events if isinstance(event, ProviderCallCompleted)]
    started = [event for event in events if isinstance(event, ProviderCallStarted)]
    assert [event.call_index for event in started] == [1]
    assert len(calls) == 1
    assert calls[0].call_index == 1
    assert calls[0].ttft_ms == 125
    assert calls[0].duration_ms == 500
    assert calls[0].input_tokens is None
    assert calls[0].output_tokens is None
    assert calls[0].total_tokens is None
    assert calls[0].cache_hit_tokens is None
    assert calls[0].cache_write_tokens is None
    assert calls[0].cache_hit_ratio is None
    assert calls[0].total_tokens_derived is None


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            SimpleNamespace(
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "input_token_details": {"cache_read": 80, "cache_creation": 16},
                },
                response_metadata={},
            ),
            {
                "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                "cache_hit_tokens": 80, "cache_write_tokens": 16, "cache_hit_ratio": 80 / 120,
            },
        ),
        (
            SimpleNamespace(
                usage_metadata=None,
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 90,
                        "completion_tokens": 10,
                        "total_tokens": 100,
                        "prompt_cache_hit_tokens": 60,
                    }
                },
            ),
            {
                "input_tokens": 90, "output_tokens": 10, "total_tokens": 100,
                "cache_hit_tokens": 60, "cache_hit_ratio": 60 / 90,
            },
        ),
        (
            SimpleNamespace(
                usage_metadata=None,
                response_metadata={
                    "usage": {
                        "input_tokens": 70,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 40,
                        "cache_creation_input_tokens": 10,
                    }
                },
            ),
            {
                "input_tokens": 120, "output_tokens": 20, "total_tokens": 140,
                "cache_hit_tokens": 40, "cache_write_tokens": 10,
                "cache_hit_ratio": 40 / 120, "total_tokens_derived": True,
            },
        ),
        (
            SimpleNamespace(
                usage_metadata=None,
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 25,
                        "prompt_tokens_details": {"cached_tokens": 32},
                    }
                },
            ),
            {
                "input_tokens": 50, "output_tokens": 25, "total_tokens": 75,
                "cache_hit_tokens": 32, "cache_hit_ratio": 32 / 50,
                "total_tokens_derived": True,
            },
        ),
    ],
)
def test_provider_usage_is_normalized_across_vendor_shapes(output: Any, expected: dict[str, int | float]):
    """LangChain、DeepSeek、Anthropic 与 OpenAI usage 字段归一为稳定日志字段。"""
    assert normalize_provider_usage(output) == expected


def test_deepseek_cache_miss_is_not_reported_as_cache_write():
    """DeepSeek miss 只表示未命中，不能冒充厂商没有报告的缓存写入。"""
    output = SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
        }},
    )

    usage = normalize_provider_usage(output)

    assert usage["cache_hit_ratio"] == 0.4
    assert "cache_write_tokens" not in usage


@pytest.mark.anyio
async def test_tool_round_emits_paired_tool_events():
    """工具轮应产出成对的开始/结束事件，并继续产出后续文本。"""
    model = ScriptedChatModel(
        turns=[
            ScriptedTurn(tool_calls=[{"name": "read_artifact", "args": {"artifact_id": 7}, "id": "c1"}]),
            ScriptedTurn(text="产物已读取"),
        ],
        delay=0.0,
    )
    events = await _collect(
        model=model, tools=guard_tools([read_artifact], allow_dangerous=False), prompt="读一下", system_prompt="你是助手"
    )

    started = [event for event in events if isinstance(event, ToolCallStarted)]
    finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert len(started) == len(finished) == 1
    assert started[0].call_id == finished[0].call_id
    assert started[0].tool_name == "read_artifact"
    assert "artifact_id" in started[0].args_summary
    assert finished[0].status == "ok"
    assert isinstance(events[-1], MessageDone) and events[-1].text == "产物已读取"


def test_tool_summaries_use_allowlists_instead_of_raw_values():
    """已知工具只留批准字段，未知工具和输出都不能保存敏感原文。"""
    known = summarize_tool_args(
        "update_artifact",
        {"artifact_id": 7, "expected_version": 2, "content": "password=must-not-persist"},
    )
    unknown = summarize_tool_args("mcp_unknown", {"query": "api_key=must-not-persist"})
    output = summarize_tool_output("Bearer must-not-persist")

    assert "artifact_id" in known and "expected_version" in known
    assert "content" not in known and "must-not-persist" not in known
    assert "must-not-persist" not in unknown
    assert "must-not-persist" not in output


@pytest.mark.anyio
async def test_dangerous_tool_is_rejected_at_execution_layer():
    """危险工具必须在执行层被拒绝，且本轮仍能正常收尾。"""
    model = ScriptedChatModel(
        turns=[
            ScriptedTurn(tool_calls=[{"name": "wipe_disk", "args": {"path": "C:/"}, "id": "c1"}]),
            ScriptedTurn(text="该操作已被拒绝"),
        ],
        delay=0.0,
    )
    events = await _collect(model=model, tools=guard_tools([wipe_disk], allow_dangerous=False), prompt="清空磁盘")

    finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert [event.status for event in finished] == ["rejected"]
    assert isinstance(events[-1], MessageDone)


@pytest.mark.anyio
async def test_step_limit_falls_back_to_wrap_up():
    """步数上限用尽后不能留下未配对的工具调用：应追加禁用工具的收尾调用并给出文本。"""
    events = await _collect(
        model=AlwaysToolCallModel(),
        tools=guard_tools([read_artifact], allow_dangerous=True),
        prompt="一直调用工具",
        recursion_limit=4,
    )

    assert not any(isinstance(event, ProviderError) for event in events)
    assert isinstance(events[-1], MessageDone)
    assert events[-1].text.endswith("这是收尾答复")
    # 收尾后不应再有悬空的工具开始事件。
    started = [event for event in events if isinstance(event, ToolCallStarted)]
    finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert len(started) == len(finished)


@pytest.mark.anyio
async def test_provider_failure_maps_to_stable_error_code():
    """厂商错误应转换为稳定错误码，而不是把原始异常抛给业务层。"""
    events = await _collect(model=BrokenModel(), tools=[], prompt="你好")

    assert isinstance(events[-1], ProviderError)
    assert events[-1].code == "PROVIDER_RATE_LIMITED"


def test_framework_event_names_stay_inside_the_loop_module():
    """框架私有事件名只允许出现在防腐层模块内。"""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name == "loop.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in _FRAMEWORK_EVENT_TOKENS):
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == [], f"这些业务模块直接引用了框架事件名：{offenders}"
