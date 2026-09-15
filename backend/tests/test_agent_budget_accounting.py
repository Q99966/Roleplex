"""T4.1 锁定版本计数与预算边界取证，不访问外部 Provider。"""
import json

import httpx
import pytest
from langchain_core.tools import StructuredTool


@pytest.mark.anyio
@pytest.mark.parametrize('limit,width,nodes,rounds,executed', [
    (2, 1, 1, 1, 0), (4, 1, 1, 2, 1),
    (15, 1, 1, 8, 7), (15, 3, 1, 8, 21), (15, 1, 8, 8, 7),
    (16, 1, 1, 8, 7),
])
async def test_graph_limit_counts_decisions_tools_and_nodes_separately(monkeypatch, limit, width, nodes, rounds, executed):
    """同响应多工具、单工具多节点不等价于多个模型决策。

    Args:
        monkeypatch：在真实图构造处观察模型前置回调，不替换图执行。
        limit：底层图上限。
        width：每次模型响应提出的工具数量。
        nodes：每个工具包含的受控逻辑节点数，不实际写文件。
        rounds：预期模型决策数。
        executed：预期完成工具数。
    """
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    decisions, completed_nodes = [], []
    original_create = loop.create_react_agent

    def create(*args, prompt, **kwargs):
        """Args:
            args：真实框架构造位置参数。
            prompt：产品模型前置回调。
            kwargs：其余框架构造参数。
        """
        async def observe(state):
            """Args:
                state：仅采集剩余步数，不保存历史正文。
            """
            decisions.append(state['remaining_steps'])
            return await prompt(state)
        return original_create(*args, prompt=observe, **kwargs)

    monkeypatch.setattr(loop, 'create_react_agent', create)

    async def operate(items: list[int]) -> str:
        """Args:
            items：受控节点，用计数代替文件副作用。
        """
        completed_nodes.extend(items)
        return 'ok'

    tool = StructuredTool.from_function(coroutine=operate, name='count_nodes', description='受控节点计数')
    turns = [ScriptedTurn(tool_calls=[{'name': 'count_nodes', 'args': {'items': list(range(nodes))},
        'id': f'call-{index}-{item}'} for item in range(width)]) for index in range(20)]
    model = ScriptedChatModel(turns=turns, delay=0)
    events = [event async for event in loop.run_agent(model=model, tools=[tool], prompt='受控预算', recursion_limit=limit)]
    assert len(decisions) == model.index == rounds
    assert decisions == list(range(limit - 1, limit - 1 - rounds * 2, -2))
    assert len([e for e in events if isinstance(e, domain.ProviderCallStarted)]) == rounds
    starts = [e.call_id for e in events if isinstance(e, domain.ToolCallStarted)]
    ends = [e.call_id for e in events if isinstance(e, domain.ToolCallFinished)]
    assert len(starts) == len(ends) == executed and set(starts) == set(ends)
    assert len(completed_nodes) == executed * nodes
    assert events[-1].stop_reason == 'graph_budget'
    blocked = [e for e in events if isinstance(e, domain.ToolCallsNotDispatched)]
    assert len(blocked) == 1 and len(blocked[0].calls) == width
    assert all(value is None for key, value in events[-1].usage.items())


@pytest.mark.anyio
@pytest.mark.parametrize('exhausted', [False, True])
async def test_sdk_http_retry_is_not_an_extra_model_decision(exhausted):
    """离线验证一次模型回调可包含两次 HTTP 尝试，无论最终成功或失败。

    Args:
        exhausted：重试也失败时，必须保留一次开始而没有成功完成。
    """
    from langchain_openai import ChatOpenAI
    from app.agent import loop, domain
    attempts = []

    def transport(request):
        """Args:
            request：仅由 MockTransport 接收，不保存头或正文。
        """
        attempts.append(True)
        if len(attempts) == 1 or exhausted:
            return httpx.Response(500, json={'error': {'message': 'controlled', 'type': 'server_error'}}, headers={'retry-after-ms': '1'})
        chunk = {'id': 'controlled', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'controlled',
            'choices': [{'index': 0, 'delta': {'content': '完成'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = ChatOpenAI(model='controlled', api_key='sk-placeholder', base_url='https://provider.invalid/v1',
            http_async_client=client, max_retries=1)
        events = [event async for event in loop.run_agent(model=model, tools=[], prompt='受控重试')]
    assert len(attempts) == 2
    assert len([e for e in events if isinstance(e, domain.ProviderCallStarted)]) == 1
    assert len([e for e in events if isinstance(e, domain.ProviderCallCompleted)]) == (0 if exhausted else 1)
    assert events[-1].stop_reason == ('provider_failed' if exhausted else 'completed')
    if not exhausted:
        assert events[-1].usage['output_tokens'] is None


@pytest.mark.anyio
async def test_final_text_at_last_decision_completes_normally():
    """最后可用模型决策仍允许直接完成，不因剩余步数少而误报预算停止。"""
    from app.agent import loop
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    model = ScriptedChatModel(turns=[ScriptedTurn(text='完成')], delay=0)
    events = [event async for event in loop.run_agent(model=model, tools=[], prompt='完成', recursion_limit=2)]
    assert events[-1].stop_reason == 'completed' and model.index == 1


@pytest.mark.anyio
@pytest.mark.parametrize('limit,reason', [(15, 'completed'), (16, 'completed')])
async def test_last_text_after_seven_tools_is_no_longer_misclassified(limit, reason):
    """T4.2 修复：15/16 步最后纯文本都正常结束。

    Args:
        limit：只改变底层预算，保持八次模型决策完全相同。
        reason：修复后的正常终态。
    """
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn

    async def observe() -> str:
        """返回受控结果，不产生磁盘副作用。"""
        return 'ok'

    tool = StructuredTool.from_function(coroutine=observe, name='observe', description='受控读取')
    turns = [ScriptedTurn(tool_calls=[{'name': 'observe', 'args': {}, 'id': f'last-{i}'}]) for i in range(7)]
    turns.append(ScriptedTurn(text='完成'))
    model = ScriptedChatModel(turns=turns, delay=0)
    events = [event async for event in loop.run_agent(model=model, tools=[tool], prompt='受控结束', recursion_limit=limit)]
    assert model.index == 8
    assert len([e for e in events if isinstance(e, domain.ToolCallFinished)]) == 7
    assert events[-1].text == '完成' and events[-1].stop_reason == reason
    assert not any(isinstance(e, domain.ToolCallsNotDispatched) for e in events)
