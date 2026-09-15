"""T0 锁定版本探针与现状复现；缺陷断言不是 T1 的目标行为。

仅使用占位数据与离线模型。框架原始消息只在内存检查，不写入报告或日志。
T1 实施时应把相应现状断言替换为修复后的验收断言。
"""
from __future__ import annotations

import asyncio
from importlib.metadata import version
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import PrivateAttr

from app.agent import loop
from app.agent.domain import MessageDone, ProviderError, ToolCallFinished, ToolCallStarted
from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
from app.agent.tool_context import tool_call_id
from app.agent.tools import guard_tools
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command
from test_workspace_edit import wait_reply


class ProbeModel(ScriptedChatModel):
    """流式路径走真实图，单独记录非流式收尾是否遗漏已知事实。"""

    _wrap_inputs: list = PrivateAttr(default_factory=list)
    fail_wrap: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """记录仅含测试占位数据的收尾输入，不伪造厂商 usage。

        Args:
            messages：框架转换后的本次输入，仅保留在测试对象内。
            stop：模型接口的停止条件。
            run_manager：框架回调管理器。
            kwargs：模型接口附加参数。
        """
        self._wrap_inputs.append(messages)
        if self.fail_wrap:
            raise TimeoutError('controlled probe failure')
        has_result = any(isinstance(message, ToolMessage) for message in messages)
        text = '已确认写入' if has_result else '无法确认本轮写入'
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def probe_model(name: str = 'probe_action', *, fail_wrap: bool = False) -> ProbeModel:
    """构造足够覆盖默认图上限、每轮调用身份不同的离线模型。

    Args:
        name：受控工具名称。
        fail_wrap：是否在额外收尾调用注入超时。
    """
    return ProbeModel(turns=[ScriptedTurn(text='准备执行。', tool_calls=[{
        'name': name, 'args': {}, 'id': f'provider-{index}',
    }]) for index in range(16)], delay=0, fail_wrap=fail_wrap)


def test_probe_matches_pinned_dependencies():
    """升级框架必须重做探针，不能沿用旧版结论。"""
    assert version('langgraph') == '0.2.74'
    assert version('langchain-core') == '0.3.36'


@pytest.mark.anyio
@pytest.mark.parametrize('limit,executed,raises', [
    (1, 0, True), (2, 0, False), (3, 1, True), (4, 1, False),
    (5, 2, True), (6, 2, False), (15, 7, True),
])
async def test_locked_graph_budget_and_call_identity(limit, executed, raises):
    """区分模型原始回调与图提交状态，并验证宿主/厂商身份映射。

    Args:
        limit：本次图步数上限；不是工具次数。
        executed：固定重复模型下实际执行次数。
        raises：此脚本和步数下是否抛图递归异常。
    """
    host_ids = []
    remaining = []

    def observe_state(state):
        """只在探针内观察图预算，生产业务不能直接消费框架状态。

        Args:
            state：模型调用前的锁定版本图状态。
        """
        remaining.append(state['remaining_steps'])
        return state['messages']

    @tool
    async def probe_action() -> str:
        """记录执行层宿主身份，返回无副作用的占位结果。"""
        host_ids.append(tool_call_id.get())
        return 'probe-result'

    graph = create_react_agent(probe_model(), guard_tools([probe_action], allow_dangerous=True), prompt=observe_state)
    events = []
    failure = None
    try:
        async for event in graph.astream_events(
            {'messages': [('user', 'probe')]}, version='v2', config={'recursion_limit': limit},
        ):
            events.append(event)
    except GraphRecursionError as exc:
        failure = type(exc)
    assert (failure is GraphRecursionError) == raises
    starts = [event for event in events if event['event'] == 'on_tool_start']
    ends = [event for event in events if event['event'] == 'on_tool_end']
    assert len(starts) == len(ends) == len(host_ids) == executed
    assert host_ids == [str(event['run_id']) for event in starts]
    assert host_ids == [str(event['run_id']) for event in ends]
    assert [event['data']['output'].tool_call_id for event in ends] == [
        f'provider-{index}' for index in range(executed)]
    assert not set(host_ids) & {f'provider-{index}' for index in range(16)}
    model_ends = [event for event in events if event['event'] == 'on_chat_model_end']
    assert len(model_ends) == executed + 1
    assert remaining == list(range(limit - 1, -1, -2))
    assert len(model_ends[-1]['data']['output'].tool_calls) == 1
    if not raises:
        # 原始模型回调含调用；图最终状态已经替换成无调用的兜底消息。
        final = next(event for event in reversed(events)
                     if event['event'] == 'on_chain_end' and not event.get('parent_ids'))
        messages = final['data']['output']['messages']
        assert not messages[-1].tool_calls
        assert messages[-1].content == 'Sorry, need more steps to process this request.'
        assert len([message for message in messages if isinstance(message, ToolMessage)]) == executed


@pytest.mark.anyio
@pytest.mark.parametrize('limit', [3, 4])
async def test_budget_keeps_file_without_uninformed_wrap(command_root, limit):
    """文件确已写入，触顶不追加会丢失当前事实的解释调用。

    Args:
        command_root：沿用测试类别/轮次/用例目录的受控工作区。
        limit：分别覆盖异常和静默触顶。
    """
    target = command_root / 't0-proof.txt'

    @tool
    async def probe_action() -> str:
        """仅写本轮固定占位文件，提供独立于模型回答的磁盘证据。"""
        target.write_text('committed-placeholder', encoding='utf-8')
        return 'probe-result: committed-placeholder'

    model = probe_model()
    events = [event async for event in loop.run_agent(
        model=model, tools=guard_tools([probe_action], allow_dangerous=True),
        prompt='probe', system_prompt='role-prefix-placeholder', recursion_limit=limit,
    )]
    assert target.read_text(encoding='utf-8') == 'committed-placeholder'
    assert len([event for event in events if isinstance(event, ToolCallFinished)]) == 1
    assert not model._wrap_inputs
    assert isinstance(events[-1], MessageDone)
    assert events[-1].stop_reason == 'graph_budget'
    assert '无法确认本轮写入' not in events[-1].text
    assert all(value is None for value in events[-1].usage.values())


@pytest.mark.anyio
@pytest.mark.parametrize('dispatched', [False, True])
async def test_unpaired_stream_is_protocol_error(monkeypatch, dispatched):
    """无触顶证据的缺失结果明确标为协议异常，不追加模型请求。

    Args:
        monkeypatch：仅替换防腐层的图事件来源。
        dispatched：是否注入工具开始和已发生的部分副作用。
    """
    applied = []

    async def stream(*args, **kwargs):
        """注入静默截断，模拟无终态凭据的事件源。

        Args:
            args：图输入占位参数。
            kwargs：图版本与配置参数。
        """
        yield {'event': 'on_chat_model_end', 'run_id': 'model-run', 'data': {'output': AIMessage(
            content='', tool_calls=[{'name': 'probe_action', 'args': {}, 'id': 'provider-call'}])}}
        if dispatched:
            yield {'event': 'on_tool_start', 'run_id': 'host-call', 'name': 'probe_action', 'data': {'input': {}}}
            applied.append('confirmed-partial-effect')

    monkeypatch.setattr(loop, 'create_react_agent', lambda *args, **kwargs: SimpleNamespace(astream_events=stream))
    model = probe_model()
    events = [event async for event in loop.run_agent(model=model, tools=[], prompt='probe')]
    assert bool(applied) == dispatched
    assert len([event for event in events if isinstance(event, ToolCallStarted)]) == int(dispatched)
    assert not any(isinstance(event, ToolCallFinished) for event in events)
    assert not model._wrap_inputs
    assert isinstance(events[-1], ProviderError)
    assert events[-1].code == 'AGENT_TOOL_RESULT_MISSING'
    assert events[-1].stop_reason == 'protocol_error'


@pytest.mark.anyio
async def test_disabled_wrap_cannot_fail_generation():
    """解释模型分支已关闭，即使其会超时也不能影响预算收口。"""
    model = probe_model(fail_wrap=True)

    @tool
    async def probe_action() -> str:
        """仅返回占位结果，强制走图的工具预算分支。"""
        return 'probe-result'

    events = [event async for event in loop.run_agent(
        model=model, tools=guard_tools([probe_action], allow_dangerous=True), prompt='probe', recursion_limit=4,
    )]
    assert not model._wrap_inputs
    assert isinstance(events[-1], MessageDone)
    assert events[-1].stop_reason == 'graph_budget'


@pytest.mark.anyio
async def test_cancel_after_effect_drains_tool_without_extra_model_request():
    """取消等待中的工具会回收任务，不抹去已有副作用，也不追加收尾请求。"""
    entered, released = asyncio.Event(), asyncio.Event()
    effects = []

    @tool
    async def probe_action() -> str:
        """产生受控内存副作用后阻塞，用于验证取消和清理边界。"""
        effects.append('applied')
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    model = probe_model()
    events = []

    async def consume():
        """持有并消费整个生成任务，取消通过实际框架传播。"""
        async for event in loop.run_agent(
            model=model, tools=guard_tools([probe_action], allow_dangerous=True), prompt='probe',
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(released.wait(), 3)
        assert effects == ['applied']
        assert model.index == 1 and not model._wrap_inputs
        assert not any(isinstance(event, (MessageDone, ProviderError, ToolCallFinished)) for event in events)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_locked_graph_rejects_unpaired_provider_history():
    """缺失 Provider 调用结果的历史在图入口被拒绝，不能盲目传给收尾模型。"""
    model = probe_model()
    graph = create_react_agent(model, [])
    messages = [AIMessage(content='', tool_calls=[{'name': 'probe_action', 'args': {}, 'id': 'missing'}])]
    with pytest.raises(ValueError, match='INVALID_CHAT_HISTORY'):
        await graph.ainvoke({'messages': messages})
    assert model.index == 0 and not model._wrap_inputs


@pytest.mark.anyio
async def test_small_files_large_allowance_reaches_real_reads(command_root, isolated_command_database, monkeypatch):
    """两个小文件的大额度申请应完成读取，保留各项真实结果。

    Args:
        command_root：本轮两个不同的小文件目录。
        isolated_command_database：由迁移创建的本用例独立数据库。
        monkeypatch：注入确定性模型并计量实际读取次数。
    """
    from app.services import chat
    from app.workspaces.files import WorkspaceFileService

    for name in ['first.txt', 'second.txt']:
        (command_root / name).write_text('tiny', encoding='utf-8')
    reads = []

    original_read = WorkspaceFileService.read
    async def observed_read(*args, **kwargs):
        """记录实际文件调用，仍使用真实执行层。

        Args:
            args：实例和相对路径。
            kwargs：读取预算及游标。
        """
        reads.append(True)
        return await original_read(*args, **kwargs)

    model = ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{
        'name': 'workspace_read', 'args': {'items': [
            {'path': name, 'max_bytes': 32768} for name in ['first.txt', 'second.txt']]}, 'id': 'read-budget',
    }]), ScriptedTurn(text='probe-completed')], delay=0)
    monkeypatch.setattr(chat, 'fake_reply_model', lambda *args, **kwargs: model)
    monkeypatch.setattr(WorkspaceFileService, 'read', observed_read)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        assert (await client.put(f'/api/roles/{rid}', headers=headers,
                                json={**role, 'builtin_tools': ['workspace_read']})).status_code == 200
        assert (await client.patch(f'/api/workspaces/{wid}', headers=headers,
                                  json={'file_tools_enabled': True})).status_code == 200
        sent = await send_command(client, headers, cid, 'probe')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_read')
        assert call['status'] == 'success'
        response = await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)
        assert response.status_code == 200
        assert [node['result']['text'] for node in response.json()['read_batch']['items']] == ['tiny', 'tiny']
        assert len(reads) == 2

@pytest.mark.anyio
async def test_graph_limit_does_not_mask_already_broken_pairing(monkeypatch):
    """Args:
        monkeypatch：同时制造重复提议 ID 和图预算异常。
    """
    from langgraph.errors import GraphRecursionError
    async def stream(*args, **kwargs):
        """Args:
            args：框架调用参数。
            kwargs：框架配置。
        """
        yield {'event': 'on_chat_model_end', 'run_id': 'model-run', 'data': {'output': AIMessage(content='', tool_calls=[
            {'name': 'probe_action', 'args': {}, 'id': 'duplicate'}, {'name': 'probe_action', 'args': {}, 'id': 'duplicate'}])}}
        raise GraphRecursionError('controlled budget')
    monkeypatch.setattr(loop, 'create_react_agent', lambda *args, **kwargs: SimpleNamespace(astream_events=stream))
    events = [event async for event in loop.run_agent(model=probe_model(), tools=[], prompt='probe')]
    assert isinstance(events[-1], ProviderError) and events[-1].stop_reason == 'protocol_error'
    assert not any(type(event).__name__ == 'ToolCallsNotDispatched' for event in events)
