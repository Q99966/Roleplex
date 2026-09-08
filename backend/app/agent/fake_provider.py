"""确定性 fake provider。

普通回归测试和端到端测试都不允许依赖真实模型计费或不稳定输出，因此这里提供一个
脚本化的模型：它实现与真实 provider 相同的 `BaseChatModel` 接口（含 `bind_tools`），
因此可以走完全相同的 Agent 循环与防腐层路径，而不是绕开它们另开一条捷径。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

# M2 起沿用的回复文案，端到端测试依赖其前缀，修改会导致浏览器断言失效。
FAKE_REPLY_TEMPLATE = "已收到你的消息：{prompt}\n\n这是 M2 fake provider 的确定性回复。"


@dataclass
class ScriptedTurn:
    """一次模型回合：产出文本，或发起一组工具调用。"""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ScriptedChatModel(BaseChatModel):
    """按脚本逐回合产出结果的确定性模型。

    回合用完后重复最后一回合，避免测试因多算一次模型调用而卡死。
    """

    turns: list[ScriptedTurn] = Field(default_factory=list)
    chunk_size: int = 6
    delay: float = 0.08
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "roleplex-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """脚本模型不依赖工具 schema，直接返回自身以保持接口一致。"""
        return self

    def _next_turn(self) -> ScriptedTurn:
        """取出当前回合并推进游标。"""
        turn = self.turns[min(self.index, len(self.turns) - 1)]
        self.index += 1
        return turn

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        turn = self._next_turn()
        message = AIMessage(content=turn.text, tool_calls=list(turn.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        turn = self._next_turn()
        for chunk in self._chunks(turn):
            yield chunk

    async def _astream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """异步流式产出，用固定间隔模拟真实厂商的分片节奏。

        间隔让停止生成、断线恢复这类时序相关的测试有稳定的观察窗口。
        """
        turn = self._next_turn()
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk

    def _chunks(self, turn: ScriptedTurn) -> list[ChatGenerationChunk]:
        """把一个回合展开为流式分片。"""
        if turn.tool_calls:
            call = turn.tool_calls[0]
            return [
                ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{
                            "name": call["name"],
                            "args": json.dumps(call.get("args", {}), ensure_ascii=False),
                            "id": call.get("id", "call_1"),
                            "index": 0,
                        }],
                    )
                )
            ]
        text = turn.text
        return [
            ChatGenerationChunk(message=AIMessageChunk(content=text[i:i + self.chunk_size]))
            for i in range(0, len(text), self.chunk_size)
        ]


def fake_reply_model(prompt: str, *, delay: float = 0.08) -> ScriptedChatModel:
    """构造只产出固定回复文案的 fake 模型。

    Args:
        prompt：用户当前消息文本，会被拼进回复以便断言输入确实到达了模型。
        delay：分片间隔秒数。
    """
    if "[W1A_FAKE_E2E]" in prompt:
        first = "W1a 第一版"
        first_hash = hashlib.sha256(first.encode("utf-8")).hexdigest()
        return ScriptedChatModel(
            turns=[
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_list", "args": {"path": ".", "limit": 200}, "id": "w1a_list",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_write",
                    "args": {"path": "hello.txt", "content": first, "expected_sha256": None},
                    "id": "w1a_create",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_read", "args": {"path": "hello.txt"}, "id": "w1a_read_first",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_write",
                    "args": {"path": "hello.txt", "content": "W1a 第二版", "expected_sha256": first_hash},
                    "id": "w1a_update",
                }]),
                ScriptedTurn(tool_calls=[{
                    "name": "workspace_read", "args": {"path": "hello.txt"}, "id": "w1a_read_final",
                }]),
                ScriptedTurn(text="W1a 工作区工具闭环完成。"),
            ],
            delay=delay,
        )
    return ScriptedChatModel(turns=[ScriptedTurn(text=FAKE_REPLY_TEMPLATE.format(prompt=prompt))], delay=delay)
