"""真实模型厂商契约测试。

这些用例会真正联网并消耗额度，默认在未配置凭据时跳过。它们回答四个问题：
流式增量是否真的分片到达、工具调用能否往返、取消能否及时传播、凭据错误是否被映射为
项目自己的稳定错误码。断言不打印 Key，也不打印完整响应体。

运行方式（在 backend 目录）：
    pytest tests/contract -q
凭据放在环境变量或本地 `.env`：`ROLEPLEX_CONTRACT_*`，见同目录 conftest。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from langchain_core.tools import tool

from app.agent.domain import MessageDone, ProviderCallCompleted, ProviderError, TextDelta, ToolCallFinished, ToolCallStarted
from app.agent.loop import run_agent
from app.agent.tools import guard_tools

# 取消传播的观察窗口：超过这个时间说明取消没有传到上游请求。
_CANCEL_BUDGET_SECONDS = 5.0


@tool
def city_temperature(city: str) -> str:
    """返回给定城市的固定温度，用于验证工具调用往返。"""
    return f"{city} 当前 21 摄氏度"


@pytest.mark.anyio
async def test_streaming_text_arrives_in_chunks(vendor):
    """真实厂商应产出多段文本增量，并以 MessageDone 收尾。"""
    events = [
        event async for event in run_agent(
            model=vendor.build_model(), tools=[], prompt="用一句话介绍你自己。",
            system_prompt="你是契约测试助手，请用中文简短回答。",
        )
    ]

    assert not any(isinstance(event, ProviderError) for event in events), "不应出现厂商错误"
    deltas = [event for event in events if isinstance(event, TextDelta)]
    assert len(deltas) >= 2, "流式回复应分多次到达，而不是一次性返回"
    assert isinstance(events[-1], MessageDone)
    assert events[-1].text.strip(), "最终文本不应为空"
    calls = [event for event in events if isinstance(event, ProviderCallCompleted)]
    assert len(calls) == 1
    assert calls[0].ttft_ms is not None and calls[0].ttft_ms >= 0
    assert calls[0].input_tokens is not None and calls[0].input_tokens > 0
    assert calls[0].output_tokens is not None and calls[0].output_tokens > 0
    assert calls[0].total_tokens == calls[0].input_tokens + calls[0].output_tokens
    # 部分厂商不报告缓存明细；支持时必须是非负 token 数，不允许用估算值。
    assert calls[0].cache_hit_tokens is None or calls[0].cache_hit_tokens >= 0


@pytest.mark.anyio
async def test_tool_call_round_trip(vendor):
    """真实厂商应能发起工具调用、拿到结果并继续作答。"""
    events = [
        event async for event in run_agent(
            model=vendor.build_model(), tools=guard_tools([city_temperature], allow_dangerous=True),
            prompt="上海现在多少度？必须调用 city_temperature 工具查询后再回答。",
            system_prompt="你是契约测试助手，需要查温度时必须调用工具。",
        )
    ]

    started = [event for event in events if isinstance(event, ToolCallStarted)]
    finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert started, "厂商未发起工具调用，说明工具 schema 或绑定方式不兼容"
    assert len(started) == len(finished), "工具开始与结束事件必须成对"
    assert all(event.status == "ok" for event in finished)
    assert isinstance(events[-1], MessageDone)


@pytest.mark.anyio
async def test_cancel_propagates_quickly(vendor):
    """取消应在预算内生效，且以 CancelledError 传播而不是伪装成错误事件。"""
    async def consume() -> None:
        async for event in run_agent(
            model=vendor.build_model(params={"temperature": 0.0, "max_tokens": 1024}),
            tools=[], prompt="请写一篇 800 字的长文，尽量详细。",
        ):
            if isinstance(event, TextDelta):
                # 拿到第一段增量说明上游流已经建立，此时取消才有意义。
                await asyncio.sleep(0)

    task = asyncio.create_task(consume())
    await asyncio.sleep(2.0)
    started_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started_at < _CANCEL_BUDGET_SECONDS


@pytest.mark.anyio
async def test_invalid_credentials_map_to_stable_error_code(vendor):
    """凭据无效时必须转换为项目自己的错误码，且不回显 Key。"""
    events = [
        event async for event in run_agent(
            model=vendor.build_model(api_key="sk-invalid-placeholder"), tools=[], prompt="你好",
        )
    ]

    assert isinstance(events[-1], ProviderError)
    assert events[-1].code in {"PROVIDER_AUTH_FAILED", "PROVIDER_BAD_REQUEST", "PROVIDER_ERROR"}
    assert "sk-invalid-placeholder" not in events[-1].message
