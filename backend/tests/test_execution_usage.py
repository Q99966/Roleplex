"""角色用量：业务记录幂等、缺失不估算、归属与采集降级。"""
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from test_workspace_commands import command_root,isolated_command_database,command_conversation


async def seed_execution(cid,rid,*,tracked=True,decisions=2):
    """Args:
        cid：受控会话。
        rid：受控角色。
        tracked：模拟旧记录缺失观测。
        decisions：已授权的决策计数，仅供统计边界验证。
    """
    from app.db import SessionLocal
    from app.models import Generation,AgentExecution
    from uuid import uuid4
    now=datetime.now(timezone.utc)
    execution_id=uuid4().hex
    async with SessionLocal() as session:
        gen=Generation(conversation_id=cid,stream_epoch='usage-test',status='completed',started_at=now,ended_at=now)
        session.add(gen);await session.flush()
        session.add(AgentExecution(execution_id=execution_id,generation_id=gen.id,conversation_id=cid,role_id=rid,
            chain_id=execution_id,execution_kind='single',status='completed',created_at=now,
            usage_tracked=tracked,decision_count=decisions))
        await session.commit()
        return execution_id,gen.id


@pytest.mark.anyio
async def test_usage_idempotent_and_partial_metrics(command_root,isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立迁移数据库。
    """
    from app.db import SessionLocal
    from app.services.execution_usage import record,summary,finish
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        execution,gid=await seed_execution(cid,rid)
        # 数字仅为直接服务层算术夹具，不由 fake Provider 生成，不作为真实用量报告。
        one=SimpleNamespace(call_index=1,input_tokens=10,output_tokens=3,cache_hit_tokens=0,cache_write_tokens=None,duration_ms=20)
        await record(execution,one,'real','fixture',completed=True)
        await record(execution,one,'real','fixture',completed=True)
        await record(execution,SimpleNamespace(call_index=1),'real','fixture')
        await record(execution,SimpleNamespace(call_index=2),'real','fixture')
        await finish(gid)
        async with SessionLocal() as session:
            value=await summary(session,cid,rid)
            assert value['recorded_calls']==2 and value['completed_calls']==1
            assert value['metrics']['input_tokens']=={'total':None,'known':10,'missing_calls':1}
        two=SimpleNamespace(call_index=2,input_tokens=20,output_tokens=4,cache_hit_tokens=5,cache_write_tokens=None,duration_ms=30)
        await record(execution,two,'real','fixture',completed=True)
        payload=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()
        assert payload['cumulative']['metrics']['input_tokens']['total']==30
        assert payload['cumulative']['metrics']['output_tokens']['total']==7
        assert payload['cumulative']['metrics']['cache_hit_tokens']['total']==5
        assert payload['cumulative']['metrics']['cache_write_tokens']['total'] is None
        assert payload['latest']['model_name']=='fixture'
        await seed_execution(cid,rid,tracked=False,decisions=0)
        async with SessionLocal() as session:
            value=await summary(session,cid,rid)
            assert value['untracked_executions']==1 and value['metrics']['input_tokens']['total'] is None
            assert value['metrics']['input_tokens']['known']==30


@pytest.mark.anyio
async def test_fake_usage_unknown_and_owner_scope(command_root,isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立数据库。
    """
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    from accounts import guest_username
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        sent=await send_command(client,headers,cid,'普通回复')
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        response=await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        value=response.json()
        assert value['latest']['message_id']==reply['id']
        assert value['latest']['summary']['recorded_calls']==1
        assert value['latest']['summary']['metrics']['input_tokens']['total'] is None
        guest=await client.post('/api/auth/register',json={'username':guest_username('usage'),'nickname':'测试','password':'Roleplex-Test-1234'})
        auth={'Authorization':'Bearer '+guest.json()['access_token']}
        assert (await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=auth)).status_code==403
        assert (await client.get(f'/api/conversations/{cid+10000}/roles/{rid}/usage',headers=headers)).status_code==404
        assert (await client.get(f'/api/conversations/{cid}/roles/{rid+10000}/usage',headers=headers)).status_code==404


@pytest.mark.anyio
async def test_usage_failure_does_not_fail_agent(command_root,isolated_command_database,monkeypatch):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立数据库。
        monkeypatch：仅阻断用量表 SQL，消息和工具正常工作。
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        original=AsyncSession.execute
        original_scalar=AsyncSession.scalar
        async def fail_usage(session,statement,*args,**kwargs):
            """Args:
                session：原会话。
                statement：受控 SQL 表级故障判断，不输出绑定数据。
                args：其余参数。
                kwargs：其余参数。
            """
            if 'model_call_usage' in str(statement):raise RuntimeError('controlled usage failure')
            return await original(session,statement,*args,**kwargs)
        async def fail_usage_scalar(session,statement,*args,**kwargs):
            """Args:
                session：原会话。
                statement：只阻断观测表读取。
                args：透传参数。
                kwargs：透传参数。
            """
            if 'model_call_usage' in str(statement):raise RuntimeError('controlled usage failure')
            return await original_scalar(session,statement,*args,**kwargs)
        monkeypatch.setattr(AsyncSession,'scalar',fail_usage_scalar)
        monkeypatch.setattr(AsyncSession,'execute',fail_usage)
        sent=await send_command(client,headers,cid,'仍需正常回答')
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        assert reply['status']=='done'
        monkeypatch.setattr(AsyncSession,'execute',original)
        monkeypatch.setattr(AsyncSession,'scalar',original_scalar)
        value=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()['cumulative']
        assert value['metrics']['input_tokens']['total'] is None
        assert value['missing_call_records']==1


@pytest.mark.anyio
async def test_restart_preserves_completed_usage_and_marks_pending_unknown(command_root,isolated_command_database):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立迁移数据库。
    """
    from app.db import SessionLocal,recover_interrupted_messages
    from app.services.execution_usage import record
    from app.models import ModelCallUsage
    from sqlalchemy import select
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        execution,_=await seed_execution(cid,rid)
        first=SimpleNamespace(call_index=1,input_tokens=12,output_tokens=5,cache_hit_tokens=0,cache_write_tokens=None,duration_ms=15)
        await record(execution,first,'real','fixture',completed=True)
        await record(execution,SimpleNamespace(call_index=2),'real','fixture')
        await recover_interrupted_messages()
        async with SessionLocal() as session:
            rows=list((await session.scalars(select(ModelCallUsage).where(ModelCallUsage.execution_id==execution).order_by(ModelCallUsage.call_index))).all())
            assert [row.status for row in rows]==['completed','unconfirmed']
            assert rows[0].input_tokens==12 and rows[1].input_tokens is None
        payload=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()
        assert payload['cumulative']['metrics']['input_tokens']=={'total':None,'known':12,'missing_calls':1}


@pytest.mark.anyio
async def test_fake_payload_does_not_create_real_token_usage(command_root,isolated_command_database):
    """Args:
        command_root：受控目录。
        isolated_command_database：隔离数据库。
    """
    from app.services.execution_usage import record
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        execution,_=await seed_execution(cid,rid,decisions=1)
        event=SimpleNamespace(call_index=1,input_tokens=999,output_tokens=99,cache_hit_tokens=9,cache_write_tokens=1,duration_ms=3)
        await record(execution,event,'fake','fake-model',completed=True)
        payload=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()
        assert payload['latest']['summary']['metrics']['input_tokens']['total'] is None
        assert payload['latest']['summary']['metrics']['model_duration_ms']['total']==3


@pytest.mark.anyio
async def test_cancelled_model_call_is_retained_as_unknown(command_root,isolated_command_database,monkeypatch):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立数据库。
        monkeypatch：受控阻塞模型，不伪造 Token。
    """
    import asyncio
    from app.services import chat
    from app.agent.fake_provider import ScriptedChatModel,ScriptedTurn
    from langchain_core.outputs import ChatGenerationChunk
    from langchain_core.messages import AIMessageChunk
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    from app.db import SessionLocal
    from app.models import ModelCallUsage
    from sqlalchemy import select
    entered=asyncio.Event()
    class WaitingModel(ScriptedChatModel):
        """保持模型请求打开，供真实停止接口取消。"""
        async def _astream(self,messages,stop=None,run_manager=None,**kwargs):
            """Args:
                messages：受控输入。
                stop：框架停止参数。
                run_manager：回调。
                kwargs：附加参数。
            """
            entered.set()
            yield ChatGenerationChunk(message=AIMessageChunk(content='等待'))
            await asyncio.Event().wait()
    monkeypatch.setattr(chat,'fake_reply_model',lambda prompt:WaitingModel(turns=[ScriptedTurn(text='等待')]))
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        sent=await send_command(client,headers,cid,'受控取消')
        await asyncio.wait_for(entered.wait(),3)
        for _ in range(100):
            async with SessionLocal() as session:
                if await session.scalar(select(ModelCallUsage.id).limit(1)):break
            await asyncio.sleep(.01)
        stopped=await client.post(f'/api/conversations/{cid}/stop',headers=headers)
        assert stopped.status_code==202
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        assert reply['stop_reason']=='user_cancelled'
        payload=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()
        assert payload['latest']['summary']['recorded_calls']==1
        assert payload['latest']['summary']['completed_calls']==0
        assert payload['latest']['summary']['metrics']['output_tokens']['total'] is None


@pytest.mark.anyio
async def test_cancel_during_completed_usage_preserves_record(command_root,isolated_command_database,monkeypatch):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立数据库。
        monkeypatch：在完成统计交接时设置同步屏障。
    """
    import asyncio
    from app.services import execution_usage
    from app.db import SessionLocal
    from app.models import Generation,ModelCallUsage
    from sqlalchemy import select
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    entered,released=asyncio.Event(),asyncio.Event()
    original=execution_usage.record
    async def blocked(*args,**kwargs):
        """Args:
            args：原观测身份与事件。
            kwargs：完成状态，不改统计数据。
        """
        if kwargs.get('completed'):
            entered.set();await released.wait()
        return await original(*args,**kwargs)
    monkeypatch.setattr(execution_usage,'record',blocked)
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        sent=await send_command(client,headers,cid,'交接后停止')
        await asyncio.wait_for(entered.wait(),3)
        stopping=asyncio.create_task(client.post(f'/api/conversations/{cid}/stop',headers=headers))
        try:
            for _ in range(100):
                async with SessionLocal() as session:
                    generation=await session.get(Generation,sent['generation_id'])
                    if generation.stop_requested_at is not None:break
                await asyncio.sleep(.01)
        finally:
            released.set()
        await asyncio.wait_for(stopping,3)
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        assert reply['stop_reason']=='user_cancelled'
        async with SessionLocal() as session:
            row=await session.scalar(select(ModelCallUsage).limit(1))
            assert row.status=='completed' and row.duration_ms is not None


@pytest.mark.anyio
@pytest.mark.parametrize('pause_stage',['tool_start','usage_complete'])
async def test_observation_delay_cannot_hide_committed_edit(command_root,isolated_command_database,monkeypatch,pause_stage):
    """Args:
        command_root：受控文件目录。
        isolated_command_database：独立迁移数据库。
        monkeypatch：控制观测交接和差异等待，不改变写入结果。
        pause_stage：工具开始落库前或模型用量落库前取消。
    """
    import asyncio
    from app.services import chat,execution_usage
    from app.workspaces.diffs import DiffTicket,current_pool
    from app.db import SessionLocal
    from app.models import Generation
    from test_workspace_edit import FIRST,enable_edit
    from test_workspace_commands import send_command
    from test_execution_summary import wait_reply
    committed,paused,released=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def held_diff(ticket):
        """Args:
            ticket：已提交文件对应的差异计算。
        """
        committed.set();await asyncio.Event().wait()
    original_update,original_record=chat._update_tool_part,execution_usage.record
    async def held_start(**kwargs):
        """Args:
            kwargs：真实开始事件，在落库前阻塞。
        """
        if pause_stage=='tool_start' and kwargs.get('tool_name')=='workspace_edit' and kwargs.get('status')=='running':
            paused.set();await asyncio.Event().wait()
        return await original_update(**kwargs)
    async def held_usage(*args,**kwargs):
        """Args:
            args：真实用量事件。
            kwargs：完成标记。
        """
        if pause_stage=='usage_complete' and kwargs.get('completed') and args[1].call_index==2:
            paused.set();await released.wait()
        return await original_record(*args,**kwargs)
    monkeypatch.setattr(DiffTicket,'result',held_diff)
    monkeypatch.setattr(chat,'_update_tool_part',held_start)
    monkeypatch.setattr(execution_usage,'record',held_usage)
    (command_root/'edit.ts').write_text(FIRST)
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        await enable_edit(client,headers,rid,wid)
        sent=await send_command(client,headers,cid,'[EDIT_FAKE]')
        await asyncio.wait_for(asyncio.gather(committed.wait(),paused.wait()),5)
        stopping=asyncio.create_task(client.post(f'/api/conversations/{cid}/stop',headers=headers))
        try:
            for _ in range(100):
                async with SessionLocal() as session:
                    generation=await session.get(Generation,sent['generation_id'])
                    if generation.stop_requested_at is not None:break
                await asyncio.sleep(.01)
        finally:released.set()
        await asyncio.wait_for(stopping,5)
        reply=await wait_reply(client,headers,cid,sent['message']['id'])
        edit=next(part for part in reply['parts_json'] if part.get('tool_name')=='workspace_edit')
        detail=(await client.get(f"/api/conversations/{cid}/messages/{reply['id']}/tools/{edit['call_id']}",headers=headers)).json()
        assert edit['confirmed_applied_items']==1 and detail['write']['files'][0]['applied'] is True
        assert detail['write']['reason']=='cancelled' and current_pool().retained_bytes==0
        assert (command_root/'edit.ts').read_text()==FIRST.replace('第一版','第二版🙂')


@pytest.mark.anyio
async def test_legacy_role_reply_without_execution_is_not_zero_usage(command_root,isolated_command_database):
    """Args:
        command_root：受控目录。
        isolated_command_database：独立数据库。
    """
    from app.db import SessionLocal
    from app.models import Message
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        async with SessionLocal() as session:
            session.add(Message(conversation_id=cid,sender_type='role',sender_id=rid,status='done',
                parts_json=[{'type':'text','text':'旧回复'}],created_at=datetime.now(timezone.utc)))
            await session.commit()
        value=(await client.get(f'/api/conversations/{cid}/roles/{rid}/usage',headers=headers)).json()
        assert value['latest'] is None and value['cumulative']['untracked_messages']==1
        assert value['cumulative']['metrics']['input_tokens']['total'] is None
        assert value['cumulative']['metrics']['input_tokens']['known'] is None


@pytest.mark.anyio
@pytest.mark.parametrize('pairs,miss,ratio,missing',[
    ([(100,90),(900,90)],820,.18,0),
    ([(100,80),(900,None),(None,50)],None,None,2),
    ([(0,0)],0,None,0),
    ([(100,101)],None,None,1),
])
async def test_cache_metrics_pair_per_call(command_root,isolated_command_database,pairs,miss,ratio,missing):
    """按调用配对且按输入加权；缺失或无效数据不产生虚假的完整比例。

    Args:
        command_root：隔离工作区。
        isolated_command_database：每例独立迁移数据库。
        pairs：直接服务层算术夹具，不作为厂商用量。
        miss：完整未命中期望值。
        ratio：完整命中率期望值。
        missing：缺失或无效配对数。
    """
    from app.db import SessionLocal
    from app.services.execution_usage import record,summary
    async with command_conversation(command_root) as (_,_,cid,rid,_):
        execution,_=await seed_execution(cid,rid,decisions=len(pairs))
        for index,(input_tokens,hit) in enumerate(pairs,1):
            await record(execution,SimpleNamespace(call_index=index,input_tokens=input_tokens,
                cache_hit_tokens=hit,duration_ms=1),'real','arithmetic-fixture',completed=True)
        async with SessionLocal() as session:
            metrics=(await summary(session,cid,rid))['metrics']
        assert metrics['cache_miss_tokens']['total']==miss
        assert metrics['input_cache_hit_ratio']['total']==ratio
        assert metrics['input_cache_hit_ratio']['missing_calls']==missing
        if pairs==[(100,80),(900,None),(None,50)]:
            assert metrics['cache_miss_tokens']['known']==20
            assert metrics['input_cache_hit_ratio']['known']==.8
