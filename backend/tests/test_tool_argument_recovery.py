"""工具参数错误作为未执行反馈返回模型，修正仍受原预算约束。"""
import pytest
from langchain_core.tools import StructuredTool
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['schema', 'json', 'partial_json', 'unknown', 'mixed'])
async def test_tool_argument_rejection_then_model_correction(mode):
    """Args:
        mode：字段错误、坏 JSON、未知工具或同响应混合调用。
    """
    from app.agent import loop, domain
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    applied = []
    def record(value: int) -> str:
        """Args:
            value：受控数值，无真实文件副作用。
        """
        applied.append(value)
        return 'ok'
    tool = StructuredTool.from_function(record, name='record', description='受控记录')
    class RecoveryModel(ScriptedChatModel):
        """使用真实分片解析路径制造一次坏 JSON。"""
        def _chunks(self, turn):
            """Args:
                turn：受控模型回合。
            """
            if mode == 'partial_json' and self.index == 1:
                raw = '{"value":1'
                return [ChatGenerationChunk(message=AIMessageChunk(content='', additional_kwargs={'tool_calls':[
                    {'id':'bad','type':'function','function':{'name':'record','arguments':raw}}]},
                    tool_call_chunks=[{'name':'record','args':raw,'id':'bad','index':0}]))]
            if mode == 'json' and self.index == 1:
                return [ChatGenerationChunk(message=AIMessageChunk(content='',tool_call_chunks=[{
                    'name':'record','args':'{"value": not-json}','id':'bad','index':0}]))]
            return super()._chunks(turn)
    invalid = {'name':'unknown_operation' if mode == 'unknown' else 'record','args':{'value':'private-invalid-input'},'id':'bad'}
    first = [invalid]
    if mode == 'mixed': first.append({'name':'record','args':{'value':2},'id':'good'})
    model = RecoveryModel(delay=0, turns=[ScriptedTurn(tool_calls=first), ScriptedTurn(tool_calls=[
        {'name':'record','args':{'value':3},'id':'corrected'}]), ScriptedTurn(text='完成')])
    events = [e async for e in loop.run_agent(model=model, tools=[tool], prompt='参数修正', decision_limit=3)]
    assert events[-1].stop_reason == 'completed' and model.index == 3
    assert sorted(applied) == ([2,3] if mode=='mixed' else [3])
    refused = [e for e in events if isinstance(e,domain.ToolCallsNotDispatched)]
    assert len(refused) == 1
    assert refused[0].calls[0].argument_error['error_code'] == {
        'schema':'TOOL_ARGUMENT_INVALID','json':'TOOL_ARGUMENT_JSON_INVALID','partial_json':'TOOL_ARGUMENT_JSON_INVALID','unknown':'TOOL_NOT_AVAILABLE','mixed':'TOOL_ARGUMENT_INVALID'}[mode]
    assert 'private-invalid-input' not in str(refused)
    assert len([e for e in events if isinstance(e,domain.ToolCallStarted)]) == len(applied)


@pytest.mark.anyio
async def test_bad_arguments_still_stop_at_budget_without_execution():
    """模型不修正参数也不会无限自重试；坏 JSON 不能执行空参数工具。"""
    from app.agent import loop,domain
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    calls=[]
    def record(value:int)->str:
        """Args:
            value：必须为整数的受控参数。
        """
        calls.append(value)
        return 'ok'
    tool=StructuredTool.from_function(record,name='record',description='受控')
    model=ScriptedChatModel(delay=0,turns=[ScriptedTurn(tool_calls=[{'name':'record','args':{'value':'bad'},'id':'bad'}])])
    events=[e async for e in loop.run_agent(model=model,tools=[tool],prompt='错误参数',decision_limit=1)]
    assert model.index==1 and not calls and events[-1].stop_reason=='decision_budget'
    assert any(isinstance(e,domain.ToolCallsNotDispatched) for e in events)


@pytest.mark.anyio
async def test_invalid_direct_tool_does_not_prevent_correction():
    """直接返回型工具参数错误时仍应让模型修正，而不是提前正常结束。"""
    from app.agent import loop,domain
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    calls=[]
    def record(value:int)->str:
        """Args:
            value：受控整数。
        """
        calls.append(value)
        return 'ok'
    tool=StructuredTool.from_function(record,name='record',description='受控',return_direct=True)
    model=ScriptedChatModel(delay=0,turns=[ScriptedTurn(tool_calls=[{'name':'record','args':{'value':'bad'},'id':'bad'}]),
        ScriptedTurn(tool_calls=[{'name':'record','args':{'value':1},'id':'fixed'}])])
    events=[e async for e in loop.run_agent(model=model,tools=[tool],prompt='修正',decision_limit=2)]
    assert calls==[1] and model.index==2 and events[-1].stop_reason=='completed'


def test_argument_error_never_includes_values_or_unknown_field_names():
    """恶意字段名、字段值和自定义异常正文不得进入诊断。"""
    from app.agent.argument_errors import argument_error
    from pydantic import BaseModel,ConfigDict,ValidationError
    class Args(BaseModel):
        """声明字段用于验证安全路径提取。"""
        model_config=ConfigDict(extra='forbid')
        count:int
    try:
        Args.model_validate({'count':'private-value', 'secret-extra-field':'private-extra-value'})
    except ValidationError as exc:
        detail=argument_error('TOOL_ARGUMENT_INVALID',exc,Args)
    assert detail['issues']==[{'path':['count'],'reason':'wrong_type'},{'path':['<field>'],'reason':'unexpected_field'}]
    assert 'private' not in str(detail) and 'secret-extra-field' not in str(detail)


from test_workspace_commands import command_root, isolated_command_database, command_conversation


@pytest.mark.anyio
@pytest.mark.parametrize('marker,code', [('[ARGUMENT_RECOVERY_FAKE]','TOOL_ARGUMENT_INVALID'),('[JSON_RECOVERY_FAKE]','TOOL_ARGUMENT_JSON_INVALID')])
async def test_argument_recovery_persists_private_reason_and_actual_file(command_root,isolated_command_database,marker,code):
    """Args:
        command_root：真实文件服务的受控目录。
        isolated_command_database：逐例迁移的数据库。
        marker：确定性模型路径。
        code：预期可恢复错误码。
    """
    from test_write_diff import enable_write
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        await enable_write(client,headers,rid,wid)
        sent=await send_command(client,headers,cid,marker)
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        assert reply['status']=='done' and reply['stop_reason'] is None
        calls=[part for part in reply['parts_json'] if part['type']=='tool_call']
        assert len(calls)==2 and calls[0]['status']=='not_executed' and calls[0]['error_code']==code
        assert calls[0]['effect_state']=='not_applied' and calls[1]['confirmed_applied_items']==1
        detail=(await client.get(f"/api/conversations/{cid}/messages/{reply['id']}/tools/{calls[0]['call_id']}",headers=headers)).json()
        assert detail['argument_error']['error_code']==code and detail['not_dispatched']['reason']=='arguments_invalid'
        assert (command_root/'recovered.txt').read_text()=='confirmed'
        assert detail['input'] is None and detail['output'] is None
        again=(await client.get(f'/api/conversations/{cid}/messages',headers=headers)).json()['items']
        assert next(item for item in again if item['id']==reply['id'])==reply


@pytest.mark.anyio
async def test_tool_reported_failure_returns_safe_feedback_with_result_pairing():
    """工具主动失败仍完整配对并允许分析下一步，但不把副作用当作未执行。"""
    from langchain_core.tools import ToolException
    from app.agent import loop,domain
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    applied=[]
    def operation()->str:
        """先发生受控副作用再主动报告失败，不能自动重试此调用。"""
        applied.append(True)
        raise ToolException('private-exception-content')
    tool=StructuredTool.from_function(operation,name='operation',description='受控失败')
    model=ScriptedChatModel(delay=0,turns=[ScriptedTurn(tool_calls=[{'name':'operation','args':{},'id':'failed'}]),ScriptedTurn(text='需核对结果。')])
    events=[e async for e in loop.run_agent(model=model,tools=[tool],prompt='受控失败',decision_limit=2)]
    finished=[e for e in events if isinstance(e,domain.ToolCallFinished)]
    assert len(applied)==1 and len(finished)==1 and finished[0].status=='error'
    assert finished[0].command_summary['error_code']=='TOOL_EXECUTION_FAILED'
    assert events[-1].stop_reason=='completed'
    assert not any(isinstance(e,domain.ToolCallsNotDispatched) for e in events)
    assert 'private-exception-content' not in str(events)
