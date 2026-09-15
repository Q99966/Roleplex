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
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr

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
        """把一个回合展开为流式分片，同响应多工具保留各自索引。

        Args:
            turn：受控模型回合，不伪造厂商用量。
        """
        if turn.tool_calls:
            return [
                *[ChatGenerationChunk(message=AIMessageChunk(content=turn.text[i:i + self.chunk_size]))
                  for i in range(0, len(turn.text), self.chunk_size)],
                ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{
                            "name": call["name"],
                            "args": json.dumps(call.get("args", {}), ensure_ascii=False),
                            "id": call.get("id", f"call_{index + 1}"),
                            "index": index,
                        } for index, call in enumerate(turn.tool_calls)],
                    )
                )
            ]
        text = turn.text
        return [
            ChatGenerationChunk(message=AIMessageChunk(content=text[i:i + self.chunk_size]))
            for i in range(0, len(text), self.chunk_size)
        ]


class SearchReadModel(ScriptedChatModel):
    """T2 浏览器模型按真实搜索结果生成行读取请求，不预埋目标行号。"""
    _hit: dict = PrivateAttr(default_factory=dict)
    stale: bool = False

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """按实际 ToolMessage 定位，后续仍交给真实工具执行。

        Args:
            messages：框架传入的本轮消息与真实工具结果。
            stop：模型接口停止条件。
            run_manager：框架回调。
            kwargs：兼容附加参数，不转发到外部服务。
        """
        if self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_search', 'args': {'queries': ['TARGET_FUNCTION', 'SECOND_TARGET'], 'match': 'any'}, 'id': 't2-search'}])
        elif self.index == 1:
            outputs = [message for message in messages if isinstance(message, ToolMessage)]
            try:
                self._hit = json.loads(outputs[-1].content)['matches'][0]
                turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {
                    'path': self._hit['path'], 'start_line': self._hit['line_number'], 'end_line': self._hit['line_number'] + 1,
                    'expected_sha256': '0' * 64 if self.stale else self._hit['sha256']}, 'id': 't2-range'}])
            except (ValueError, KeyError, IndexError):
                turn = ScriptedTurn(text='没有获得可用搜索定位结果。')
        elif self.index == 2 and self._hit and not self.stale:
            turn = ScriptedTurn(tool_calls=[{'name': 'workspace_read', 'args': {'items': [
                {'path': self._hit['path'], 'start_line': self._hit['line_number'], 'end_line': self._hit['line_number'] + 1},
                {'path': self._hit['path'], 'start_line': self._hit['line_number'] + 2, 'end_line': self._hit['line_number'] + 2},
                {'path': 'missing.txt'}, {'path': 'small.txt', 'max_bytes': 32768},
            ]}, 'id': 't2-batch'}])
        else:
            turn = ScriptedTurn(text='版本冲突已确认，未修改文件。' if self.stale else '搜索与范围读取完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


class ReplacementsModel(ScriptedChatModel):
    """根据实际读取的版本发起多片段编辑，失败场景不重试。"""
    invalid: bool = False

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """Args:
            messages：包含实际读取结果的框架消息。
            stop：兼容模型接口。
            run_manager：框架回调。
            kwargs：附加接口参数。
        """
        if self.index == 0:
            turn = ScriptedTurn(tool_calls=[{'name':'workspace_read', 'args':{'path':'sample.txt'}, 'id':'replacement-read'}])
        elif self.index == 1:
            output = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
            digest = json.loads(output.content)['sha256']
            pairs = [{'old_text': f'{key}={old}', 'new_text': f'{key}={new}'} for key,old,new in [('alpha',1,10),('beta',2,20),('gamma',3,30)]]
            if self.invalid:
                pairs = [{'old_text':'alpha=10','new_text':'alpha=11'},{'old_text':'missing-marker','new_text':'invalid'}]
            args = {'path':'sample.txt','expected_sha256':digest,'replacements':pairs}
            calls = [{'name':'workspace_edit','args':args,'id':'replacement-edit'}]
            if self.invalid: calls.append({'name':'workspace_edit','args':{'items':[args]},'id':'replacement-batch-error'})
            turn = ScriptedTurn(tool_calls=calls)
        else:
            turn = ScriptedTurn(text='多片段拒绝已确认。' if self.invalid else '多片段编辑完成。')
        self.index += 1
        for chunk in self._chunks(turn):
            await asyncio.sleep(self.delay)
            yield chunk


def fake_reply_model(prompt: str, *, delay: float = 0.08) -> ScriptedChatModel:
    """构造只产出固定回复文案的 fake 模型。

    Args:
        prompt：用户当前消息文本，会被拼进回复以便断言输入确实到达了模型。
        delay：分片间隔秒数。
    """
    if '[REPLACEMENTS_FAKE]' in prompt or '[REPLACEMENTS_BAD_FAKE]' in prompt:
        return ReplacementsModel(delay=delay, invalid='[REPLACEMENTS_BAD_FAKE]' in prompt)
    if '[SEARCH_READ_FAKE]' in prompt or '[SEARCH_STALE_FAKE]' in prompt:
        return SearchReadModel(delay=delay, stale='[SEARCH_STALE_FAKE]' in prompt)
    if '[BUDGET_PROPOSALS_FAKE]' in prompt:
        # 默认预算的前七轮只创建不同的小文件；第八轮的两项只能记录为未派发。
        return ScriptedChatModel(turns=[*[ScriptedTurn(tool_calls=[{'name': 'workspace_write', 'id': f'budget-{index}',
            'args': {'path': f'created-{index}.txt', 'content': 'confirmed'}}]) for index in range(7)],
            ScriptedTurn(tool_calls=[{'name': 'workspace_write', 'id': f'blocked-{index}',
                'args': {'path': f'not-created-{index}.txt', 'content': 'must-not-write'}} for index in range(2)])], delay=delay)
    if '[EXECUTION_FACTS_FAKE]' in prompt:
        # 默认图预算内先真实写入，再持续只读以确定性触顶；不修改生产预算。
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{
            'name': 'workspace_write', 'args': {'path': 'facts-proof.txt', 'content': 'controlled-facts'}, 'id': 'facts-write',
        }]), *[ScriptedTurn(tool_calls=[{'name': 'workspace_list', 'args': {}, 'id': f'facts-list-{index}'}])
               for index in range(10)]], delay=delay)
    if '[WRITE_DIAGNOSTIC_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(text='尝试写入受控文件。', tool_calls=[{
            'name': 'workspace_write', 'args': {'path': 'blocked.txt', 'content': 'new'}, 'id': 'diagnostic_write'}]),
            ScriptedTurn(text='再检查批次编辑拒绝。', tool_calls=[{'name': 'workspace_edit', 'args': {'items': [
                {'path': name, 'old_text': 'old', 'new_text': 'new', 'expected_sha256': hashlib.sha256(b'old').hexdigest()}
                for name in ['existing.txt', 'unstarted.txt']]}, 'id': 'diagnostic_batch'}]),
            ScriptedTurn(text='写入拒绝诊断流程结束。')], delay=delay)
    if '[SERVICE_DISCOVERY_FAKE]' in prompt:
        # 固定脚本只请求列表，不含任何启动返回的 runtime_id；通过真实工具取登记事实。
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{
            'name': 'workspace_service_status', 'args': {}, 'id': 'service_discovery'}]),
            ScriptedTurn(text='当前会话服务查询完成。')], delay=delay)
    if '[SERVICE_FAKE:' in prompt:
        import re
        import shlex
        import sys
        match = re.search(r'\[SERVICE_FAKE:(\d+)\]', prompt)
        if match:
            port = int(match[1])
            script = f'{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1'
            return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workspace_start_service', 'args': {
                'script': script, 'port': port, 'lifetime_seconds': 60}, 'id': 'service_start'}]),
                ScriptedTurn(text='服务启动流程已结束，请使用 /ps 管理。')], delay=delay)
    if '[SHELL_WRITE_WAIT_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[
            {'name': 'workspace_run_shell', 'args': {'script': 'echo approval-placeholder'}, 'id': 'wait-shell'},
            {'name': 'workspace_write', 'args': {'items': [{'path': 'pending/proof.txt', 'content': 'written-before-approval'}]}, 'id': 'wait-write'},
        ]), ScriptedTurn(text='审批与写入验证完成。')], delay=delay)
    if any(marker in prompt for marker in ('[SHELL_APPROVAL_FAKE]', '[SHELL_EXPIRY_FAKE]', '[SHELL_DETAILS_FAKE]')):
        import os
        script = "[IO.File]::AppendAllText('shell-proof.txt', \"approved`n\")" if os.name == 'nt' else "printf 'approved\\n' >> shell-proof.txt"
        if '[SHELL_EXPIRY_FAKE]' in prompt:
            script = 'echo approval-expiry-placeholder'
        if '[SHELL_DETAILS_FAKE]' in prompt:
            script = ("[Console]::WriteLine('shell-stdout-placeholder'); [Console]::Error.WriteLine('shell-stderr-placeholder')"
                if os.name == 'nt' else "printf 'shell-stdout-placeholder\\n'; printf 'shell-stderr-placeholder\\n' >&2")
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workspace_run_shell', 'args': {'script': script}, 'id': 'shell_approval'}]),
            ScriptedTurn(text='审批流程已结束。')], delay=delay)
    if '[TOOL_TIMELINE_FAKE]' in prompt:
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先读取😀。', tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': 'read', 'args': {'path': 'hello.txt'}}, 'id': 'timeline_read',
            }]),
            ScriptedTurn(text='读取完成，再统计。', tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': 'count', 'args': {'path': 'hello.txt'}}, 'id': 'timeline_count',
            }]),
            ScriptedTurn(text='处理完成。'),
        ], delay=delay)
    if '[BATCH_MUTATION_FAKE]' in prompt:
        files = [('src/nested/batch-a.txt', 'alpha old'), ('test/nested/batch-b.txt', 'beta old')]
        return ScriptedChatModel(turns=[ScriptedTurn(text='先创建两个文件。', tool_calls=[{
            'name': 'workspace_write', 'args': {'items': [{'path': name, 'content': text} for name, text in files]}, 'id': 'batch_create'}]),
            ScriptedTurn(text='再分别局部修改。', tool_calls=[{'name': 'workspace_edit', 'args': {'items': [
                {'path': name, 'old_text': 'old', 'new_text': 'new', 'expected_sha256': hashlib.sha256(text.encode()).hexdigest()}
                for name, text in files]}, 'id': 'batch_edit'}]), ScriptedTurn(text='批量修改验收完成。')], delay=delay)
    if '[READ_MANY_FAKE]' in prompt:
        return ScriptedChatModel(turns=[ScriptedTurn(text='批量读取受控文件。', tool_calls=[{
            'name': 'workspace_read', 'args': {'items': [
                {'path': 'first.txt'}, {'path': 'missing.txt'}, {'path': 'second.txt'},
            ]}, 'id': 'read_many'}]), ScriptedTurn(text='再检查单文件兼容形式。', tool_calls=[{
                'name': 'workspace_read', 'args': {'path': 'second.txt'}, 'id': 'read_legacy'}]),
            ScriptedTurn(text='批量读取验收完成。')], delay=delay)
    if '[EXPLORE_FAKE]' in prompt:
        # 连续只读、缺失文件和正文断点，走真实工具与消息事件以验证纯展示归组。
        return ScriptedChatModel(turns=[
            ScriptedTurn(tool_calls=[{'name': name, 'args': {'path': path}, 'id': f'explore_{index}'}])
            for index, (name, path) in enumerate([
                ('workspace_read', 'explore.txt'), ('workspace_list', '.'),
                ('workspace_read', 'explore.txt'), ('workspace_read', 'missing.txt'),
            ])
        ] + [ScriptedTurn(text='第一段探索完成，下面单独核对目录。',
            tool_calls=[{'name': 'workspace_list', 'args': {'path': '.'}, 'id': 'explore_tail'}]),
            ScriptedTurn(text='探索验收完成。')], delay=max(delay, 0.4))
    if '[EDIT_FAKE' in prompt:
        first = "export const title = '第一版';\nexport const keep = '保持';\n"
        old = 'missing' if '[EDIT_FAKE_MISSING]' in prompt else '第一版'
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先读取当前文件。', tool_calls=[{'name': 'workspace_read', 'args': {'path': 'edit.ts'}, 'id': 'edit_read'}]),
            ScriptedTurn(text='只修改标题。', tool_calls=[{'name': 'workspace_edit', 'args': {'path': 'edit.ts', 'old_text': old,
                'new_text': '第二版🙂', 'expected_sha256': hashlib.sha256(first.encode()).hexdigest()}, 'id': 'edit_apply'}]),
            ScriptedTurn(text='编辑流程已结束。'),
        ], delay=delay)
    if '[WRITE_DIFF_FAKE]' in prompt:
        first = "export const title = '第一版';\nexport const keep = '保持';\n"
        after = "export const title = '第二版🙂';\nexport const keep = '保持';\n"
        return ScriptedChatModel(turns=[
            ScriptedTurn(text='先创建文件。', tool_calls=[{'name': 'workspace_write', 'args': {'path': 'demo.ts', 'content': first}, 'id': 'diff_create'}]),
            ScriptedTurn(text='现在修改标题。', tool_calls=[{'name': 'workspace_write', 'args': {
                'path': 'demo.ts', 'content': after, 'expected_sha256': hashlib.sha256(first.encode()).hexdigest()}, 'id': 'diff_update'}]),
            ScriptedTurn(text='修改完成。'),
        ], delay=delay)
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
    if '[W1B_FAKE_E2E]' in prompt:
        commands = [('pwd', {}), ('list', {}), ('read', {'path': 'hello.txt'}), ('count', {'path': 'hello.txt'})]
        return ScriptedChatModel(turns=[
            *[ScriptedTurn(tool_calls=[{
                'name': 'workspace_run_command', 'args': {'command': command, 'args': args}, 'id': f'w1b_{command}',
            }]) for command, args in commands],
            ScriptedTurn(text='W1b 结构化命令流程结束。'),
        ], delay=delay)
    for marker, path in [
        ('[W1B_DENIED]', '../outside'), ('[W1B_OUTPUT]', 'output.txt'),
        ('[W1B_TIMEOUT]', 'timeout.txt'), ('[W1B_CANCEL]', 'cancel.txt'),
        ('[W1B_EXIT]', 'exit.txt'),
    ]:
        if marker in prompt:
            return ScriptedChatModel(turns=[
                ScriptedTurn(tool_calls=[{
                    'name': 'workspace_run_command', 'args': {'command': 'read', 'args': {'path': path}}, 'id': 'w1b_read',
                }]), ScriptedTurn(text='W1b 命令场景结束。'),
            ], delay=delay)
    return ScriptedChatModel(turns=[ScriptedTurn(text=FAKE_REPLY_TEMPLATE.format(prompt=prompt))], delay=delay)
