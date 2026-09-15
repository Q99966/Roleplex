"""T4.2 决策预算应在下次请求前停止，最后工具结果完整交接。"""
import asyncio
import pytest
from langchain_core.tools import StructuredTool


@pytest.mark.anyio
@pytest.mark.parametrize('limit,width', [(1, 1), (2, 3), (8, 2), (32, 1)])
async def test_last_decision_tools_complete_before_budget_stop(limit, width):
    """Args:
        limit：实际模型决策上限。
        width：同一响应的并行工具数。
    """
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    applied = []
    async def record(value: int) -> str:
        """Args:
            value：受控副作用标识，避免用模型文本推断执行。
        """
        applied.append(value)
        return 'ok'
    tool = StructuredTool.from_function(coroutine=record, name='record', description='受控提交')
    model = ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[
        {'name': 'record', 'args': {'value': turn * width + index}, 'id': f'{turn}-{index}'} for index in range(width)
    ]) for turn in range(limit + 1)])
    events = [e async for e in loop.run_agent(model=model, tools=[tool], prompt='受控预算', decision_limit=limit)]
    assert model.index == limit and len(applied) == limit * width
    assert len([e for e in events if isinstance(e, domain.ToolCallFinished)]) == limit * width
    assert not any(isinstance(e, domain.ToolCallsNotDispatched) for e in events)
    assert events[-1].stop_reason == 'decision_budget'
    assert events[-1].undispatched_proposals == 0


@pytest.mark.anyio
@pytest.mark.parametrize('direct', [False, True])
async def test_final_completion_wins_over_decision_count(direct):
    """Args:
        direct：工具直接返回也应正常结束，不被预算数覆盖。
    """
    from app.agent import loop
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    def done() -> str:
        """返回受控结果。"""
        return 'ok'
    tool = StructuredTool.from_function(done, name='done', description='完成', return_direct=True)
    turn = ScriptedTurn(tool_calls=[{'name':'done','args':{},'id':'direct'}]) if direct else ScriptedTurn(text='普通最终回答')
    model = ScriptedChatModel(delay=0, turns=[turn])
    events = [e async for e in loop.run_agent(model=model, tools=[tool], prompt='最后决策', decision_limit=1)]
    assert events[-1].stop_reason == 'completed' and model.index == 1


@pytest.mark.anyio
@pytest.mark.parametrize('reason,with_tool', [('length', False), ('length', True), ('max_tokens', True), ('content_filter', True)])
async def test_incomplete_response_never_dispatches_tools(reason, with_tool):
    """Args:
        reason：厂商明确不完整终止元数据。
        with_tool：参数即使完整可解析也不得执行。
    """
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    class IncompleteModel(ScriptedChatModel):
        """保留真实流式路径，只补受控终止元数据。"""
        def _chunks(self, turn):
            """Args:
                turn：本轮受控输出。
            """
            chunks = super()._chunks(turn)
            chunks[-1].message.response_metadata = {'finish_reason': reason}
            return chunks
    applied = []
    def record() -> str:
        """受控副作用必须不发生。"""
        applied.append(True)
        return 'ok'
    tool = StructuredTool.from_function(record, name='record', description='受控提交')
    turn = ScriptedTurn(tool_calls=[{'name':'record','args':{},'id':'truncated'}]) if with_tool else ScriptedTurn(text='完成')
    model = IncompleteModel(delay=0, turns=[turn])
    events = [e async for e in loop.run_agent(model=model, tools=[tool], prompt='受控截断', decision_limit=1)]
    assert not applied and not any(isinstance(e, domain.ToolCallStarted) for e in events)
    assert events[-1].code == 'PROVIDER_RESPONSE_INCOMPLETE'
    assert events[-1].stop_reason == 'provider_failed'


@pytest.mark.anyio
async def test_cancellation_during_last_tool_does_not_become_budget_stop():
    """用户取消最后一轮等待，不得被决策预算终态覆盖或开始后续模型。"""
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    entered, exited = asyncio.Event(), asyncio.Event()
    async def wait() -> str:
        """受控阻塞点，无文件副作用。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()
        return 'unreachable'
    tool = StructuredTool.from_function(coroutine=wait, name='wait', description='等待')
    model = ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name':'wait','args':{},'id':'wait'}])])
    events = []
    async def consume():
        """收集领域事件供取消边界验证。"""
        async for event in loop.run_agent(model=model, tools=[tool], prompt='等待取消', decision_limit=1):
            events.append(event)
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(exited.wait(), 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert model.index == 1 and not any(isinstance(e, domain.MessageDone) for e in events)


@pytest.mark.anyio
@pytest.mark.parametrize('limit', [0, -1, True, 257, 1.5])
async def test_invalid_decision_budget_is_rejected_before_model(limit):
    """Args:
        limit：不得被当成无限额度或隐式取整的内部输入。
    """
    from app.agent import loop
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    model = ScriptedChatModel(delay=0, turns=[ScriptedTurn(text='完成')])
    with pytest.raises(ValueError):
        _ = [e async for e in loop.run_agent(model=model, tools=[], prompt='输入边界', decision_limit=limit)]
    assert model.index == 0


@pytest.mark.anyio
async def test_rejected_tool_still_consumes_decision():
    """权限拒绝不能退还决策额度，避免失败后无限重试。"""
    from app.agent import loop, domain
    from app.agent.tools import guard_tools
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    called = []
    def dangerous() -> str:
        """未授权调用不得进入此函数。"""
        called.append(True)
        return 'ok'
    tool = StructuredTool.from_function(dangerous, name='dangerous', description='受控危险操作')
    model = ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name':'dangerous','args':{},'id':'denied'}])])
    events = [e async for e in loop.run_agent(model=model, tools=guard_tools([tool], allow_dangerous=False), prompt='权限', decision_limit=1)]
    assert not called and model.index == 1
    assert [e.status for e in events if isinstance(e, domain.ToolCallFinished)] == ['rejected']
    assert events[-1].stop_reason == 'decision_budget'


@pytest.mark.anyio
async def test_duplicate_tool_ids_are_rejected_before_side_effects():
    """同响应重复身份属于协议错误，不能先执行两次再由事件消费者发现。"""
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    applied = []
    def record() -> str:
        """此副作用在重复身份输入下必须不发生。"""
        applied.append(True)
        return 'ok'
    tool = StructuredTool.from_function(record, name='record', description='受控提交')
    model = ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{'name':'record','args':{},'id':'duplicate'}] * 2)])
    events = [e async for e in loop.run_agent(model=model, tools=[tool], prompt='重复身份', decision_limit=1)]
    assert not applied and not any(isinstance(e, domain.ToolCallStarted) for e in events)
    assert events[-1].code == 'AGENT_TOOL_CALL_ID_INVALID' and events[-1].stop_reason == 'protocol_error'
