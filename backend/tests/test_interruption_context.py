"""中断交接不依赖关键词；原生提交、崩溃窗口及权限均可核对。"""
import json
from datetime import datetime,timedelta,timezone
import pytest
from test_workspace_commands import command_root,isolated_command_database,command_conversation,send_command
from test_workspace_edit import wait_reply


async def stopped_file(client,headers,cid,rid,wid):
    """Args:
        client：隔离真实API客户端。
        headers：Owner认证。
        cid：会话。
        rid：角色。
        wid：工作区。
    """
    await client.patch(f'/api/workspaces/{wid}',headers=headers,json={'file_tools_enabled':True})
    role=next(row for row in (await client.get('/api/roles',headers=headers)).json() if row['id']==rid)
    assert (await client.put(f'/api/roles/{rid}',headers=headers,json={**role,'builtin_tools':['workspace_write','workspace_edit','workspace_read']})).status_code==200
    config=(await client.get('/api/agent-budget/config',headers=headers)).json()
    await client.put('/api/agent-budget/config',headers=headers,json={'decision_limit':1,'expected_revision':config['revision']})
    sent=await send_command(client,headers,cid,'[INTERRUPTION_SETUP_FAKE]')
    reply=await wait_reply(client,headers,cid,sent['message']['id'])
    assert reply['status']=='stopped'
    return reply


async def context_for(cid,rid,text='把按钮改为蓝色，其他保持不变'):
    """Args:
        cid：已停止会话。
        rid：目标角色。
        text：普通新要求，无继续关键词。
    """
    from app.db import SessionLocal
    from app.models import Message,Role
    from app.context import build_context,ContextBuildRequest
    async with SessionLocal() as session:
        role=await session.get(Role,rid)
        message=Message(conversation_id=cid,sender_type='user',sender_id=role.created_by,
            parts_json=[{'type':'text','text':text}],status='done',created_at=datetime.now(timezone.utc))
        session.add(message)
        await session.commit()
        return await build_context(session,ContextBuildRequest(role_id=rid,conversation_id=cid,
            current_message_id=message.id,triggered_by_user_id=role.created_by))


@pytest.mark.anyio
@pytest.mark.parametrize('scenario',['match','changed','prepared','expired','revoked','normal','root_changed','provider_error','restart'])
async def test_interruption_facts_follow_current_state_and_permissions(command_root,isolated_command_database,scenario):
    """Args:
        command_root：每例独立文件目录。
        isolated_command_database：每例迁移数据库。
        scenario：证据与当前状态边界。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import FileEffect,ToolExecutionDetail,Role,Message,WorkspaceBinding
    from app.services import file_effects
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        reply=await stopped_file(client,headers,cid,rid,wid)
        assert (command_root/'interruption.txt').read_text()=='alpha=1\n'
        async with SessionLocal() as session:
            row=await session.scalar(select(FileEffect))
            assert row and file_effects.decode(row)['state']=='confirmed'
            assert 'interruption.txt' not in row.payload_encrypted and 'alpha=1' not in row.payload_encrypted
            identity=(row.execution_id,row.call_id,row.item_index)
            payload=file_effects.decode(row)
            detail=await session.scalar(select(ToolExecutionDetail).where(ToolExecutionDetail.message_id==reply['id']))
            if scenario=='prepared':
                detail.output_encrypted=None
            elif scenario=='expired':
                row.expires_at=detail.expires_at=datetime.now(timezone.utc)-timedelta(days=1)
            elif scenario=='revoked':
                (await session.get(Role,rid)).builtin_tools_json=[]
            elif scenario in {'provider_error','restart'}:
                (await session.get(Message,reply['id'])).status='error' if scenario=='provider_error' else 'interrupted'
            elif scenario=='normal':
                (await session.get(Message,reply['id'])).status='done'
            elif scenario=='root_changed':
                other=command_root/'other';other.mkdir()
                (await session.get(WorkspaceBinding,wid)).root_path=str(other)
            await session.commit()
        if scenario=='prepared':
            payload['state']='prepared'
            await file_effects.record(execution_id=identity[0],call_id=identity[1],item_index=identity[2],payload=payload)
        if scenario=='changed':(command_root/'interruption.txt').write_text('external change')
        result=await context_for(cid,rid)
        assert result.current_message=='把按钮改为蓝色，其他保持不变'
        assert '最近中断执行数据' not in result.system_prompt
        notes=[message.content for message in result.history if '最近中断执行数据' in str(message.content)]
        if scenario=='normal':
            assert not notes and result.fingerprints.interruption_hash is None
        else:
            assert len(notes)==1 and result.fingerprints.interruption_hash
            data=json.loads(notes[0].split('\n',1)[1])
            if scenario in {'expired','revoked','root_changed'}:
                assert data['incomplete'] is True
                assert 'interruption.txt' not in notes[0]
            else:
                fact=next(f for f in data['facts'] if f.get('path')=='interruption.txt')
                assert fact['current']=={'match':'matches_recorded_version','changed':'changed','prepared':'matches_expected_content_only','provider_error':'matches_recorded_version','restart':'matches_recorded_version'}[scenario]
                assert fact['state']==('prepared' if scenario=='prepared' else 'confirmed')


@pytest.mark.anyio
async def test_real_process_crash_after_file_commit_keeps_prepared_evidence(command_root,isolated_command_database):
    """Args:
        command_root：子进程只操作本轮测试文件。
        isolated_command_database：子进程共享本例独立数据库，不启动服务。
    """
    import asyncio,os,sys
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import FileEffect,Message
    from app.services.file_effects import decode
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        reply=await stopped_file(client,headers,cid,rid,wid)
        async with SessionLocal() as session:
            execution=(await session.scalar(select(FileEffect))).execution_id
        script="""
import asyncio,os,sys
from pathlib import Path
from app.agent.write_capture import WriteCaptureScope,write_capture_scope
from app.agent.tool_context import tool_call_id
from app.workspaces.files import WorkspaceFileService
async def run():
    write_capture_scope.set(WriteCaptureScope(execution_id=sys.argv[2]))
    tool_call_id.set('crash-proof')
    service=WorkspaceFileService(root=Path(sys.argv[1]),execution_id=sys.argv[2])
    await service.write('crash.txt','confirmed-on-disk',capture_applied=lambda before,after:os._exit(23))
asyncio.run(run())
"""
        process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,str(command_root),execution,
            env=dict(os.environ),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:
            assert await asyncio.wait_for(process.wait(),15)==23
        finally:
            if process.returncode is None:
                process.kill();await process.wait()
        assert (command_root/'crash.txt').read_text()=='confirmed-on-disk'
        async with SessionLocal() as session:
            row=await session.scalar(select(FileEffect).where(FileEffect.call_id=='crash-proof'))
            assert decode(row)['state']=='prepared'
            (await session.get(Message,reply['id'])).status='interrupted'
            await session.commit()
        result=await context_for(cid,rid,'请检查另一个问题')
        note=next(message.content for message in result.history if '最近中断执行数据' in str(message.content))
        facts=json.loads(note.split('\n',1)[1])['facts']
        assert next(f for f in facts if f.get('path')=='crash.txt')['current']=='matches_expected_content_only'


@pytest.mark.anyio
async def test_guest_and_group_role_do_not_receive_other_private_facts(command_root,isolated_command_database):
    """Args:
        command_root：隔离工作区。
        isolated_command_database：每例独立身份与会话。
    """
    from accounts import guest_username
    from app.db import SessionLocal
    from app.models import Conversation,ConversationMember,Message,Role
    from app.context import build_context,ContextBuildRequest
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        reply=await stopped_file(client,headers,cid,rid,wid)
        guest=(await client.post('/api/auth/register',json={'username':guest_username('handoff'),'nickname':'测试','password':'Roleplex-Test-1234'})).json()
        response=await client.get('/api/auth/me',headers={'Authorization':'Bearer '+guest['access_token']})
        guest_id=response.json()['id']
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid,member_type='user',member_id=guest_id,joined_at=datetime.now(timezone.utc)))
            current=Message(conversation_id=cid,sender_type='user',sender_id=guest_id,status='done',
                parts_json=[{'type':'text','text':'请修复剩余问题'}],created_at=datetime.now(timezone.utc))
            session.add(current);await session.commit()
            context=await build_context(session,ContextBuildRequest(role_id=rid,conversation_id=cid,current_message_id=current.id,triggered_by_user_id=guest_id))
            assert context.fingerprints.interruption_hash is None
            assert not any('最近中断执行数据' in str(message.content) for message in context.history)
            owner=(await session.get(Role,rid)).created_by
            other=Role(created_by=owner,name='另一角色',system_prompt='测试',model_name='fake-model',created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
            session.add(other);await session.flush()
            session.add(ConversationMember(conversation_id=cid,member_type='role',member_id=other.id,joined_at=datetime.now(timezone.utc)))
            session.add(Message(conversation_id=cid,sender_type='role',sender_id=other.id,status='interrupted',
                parts_json=[{'type':'text','text':'另一角色回复'}],created_at=datetime.now(timezone.utc)))
            (await session.get(Conversation,cid)).type='group'
            await session.commit()
        context=await context_for(cid,rid)
        note=next(message.content for message in context.history if '最近中断执行数据' in str(message.content))
        data=json.loads(note.split('\n',1)[1])
        assert data['source_message_id']==reply['id']
        assert 'interruption.txt' not in note  # 当前群聊未授权原生文件工具，不借恢复扩权。


@pytest.mark.anyio
async def test_recovery_context_budget_degrades_without_blocking_new_request(command_root,isolated_command_database,monkeypatch):
    """Args:
        command_root：隔离工作区。
        isolated_command_database：独立数据库。
        monkeypatch：模拟大交接，不扩大实际文件读取或厂商消耗。
    """
    from app.context import interruption
    async def large(*args,**kwargs):
        """Args:
            args：原构建器参数。
            kwargs：原构建器身份边界。
        """
        return '事实'*200000
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        monkeypatch.setattr(interruption,'interruption_context',large)
        result=await context_for(cid,rid,'请处理一个新问题')
        assert result.current_message=='请处理一个新问题'
        assert '无法完整装入上下文' in result.history[-1].content
        assert result.budget.estimate.estimated_tokens+result.budget.estimate.safety_margin_tokens<=result.budget.input_budget_tokens


@pytest.mark.anyio
async def test_authorization_rechecked_after_evidence_wait(command_root,monkeypatch):
    """Args:
        command_root：只允许操作本例目录。
        monkeypatch：写前证据等待中撤权，不依赖真实睡眠时序。
    """
    from app.services import file_effects
    from app.workspaces.files import WorkspaceFileService,WorkspaceFileError
    allowed=True
    async def saved(*args,**kwargs):
        """Args:
            args：宿主执行。
            kwargs：最小证据。
        """
        nonlocal allowed
        allowed=False
        return True
    async def authorize():
        """模拟等待结束后的当前权限。"""
        return service if allowed else None
    monkeypatch.setattr(file_effects,'checkpoint',saved)
    service=WorkspaceFileService(root=command_root,execution_id='recheck')
    with pytest.raises(WorkspaceFileError) as error:
        await service.write('blocked.txt','never',_recheck=authorize)
    assert error.value.code=='WORKSPACE_TOOL_NOT_AVAILABLE'
    assert not (command_root/'blocked.txt').exists()


@pytest.mark.anyio
async def test_newer_unknown_effect_is_not_hidden_by_older_success(command_root,isolated_command_database):
    """Args:
        command_root：独立文件目录。
        isolated_command_database：本例数据库。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import FileEffect
    from app.services import file_effects
    import hashlib
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        await stopped_file(client,headers,cid,rid,wid)
        async with SessionLocal() as session:
            row=await session.scalar(select(FileEffect))
            payload=file_effects.decode(row)
            execution=row.execution_id
        payload.update(state='prepared',after_sha256=hashlib.sha256(b'alpha=2\n').hexdigest(),bytes=8)
        await file_effects.record(execution_id=execution,call_id='later-unconfirmed',item_index=0,payload=payload)
        (command_root/'interruption.txt').write_text('alpha=2\n')
        context=await context_for(cid,rid)
        note=next(message.content for message in context.history if '最近中断执行数据' in str(message.content))
        facts=[fact for fact in json.loads(note.split('\n',1)[1])['facts'] if fact.get('path')=='interruption.txt']
        assert len(facts)==1 and facts[0]['state']=='prepared'
        assert facts[0]['current']=='matches_expected_content_only'


@pytest.mark.anyio
async def test_evidence_storage_failure_does_not_reverse_file_commit(command_root,isolated_command_database,monkeypatch):
    """Args:
        command_root：隔离文件。
        isolated_command_database：独立数据库。
        monkeypatch：仅阻断新增证据加密，不影响原详情或真实写入。
    """
    from app.services import file_effects
    def unavailable():
        """模拟证据存储故障，不携带私有参数。"""
        raise RuntimeError('controlled')
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        monkeypatch.setattr(file_effects,'_cipher',unavailable)
        await stopped_file(client,headers,cid,rid,wid)
        assert (command_root/'interruption.txt').read_text()=='alpha=1\n'
        context=await context_for(cid,rid)
        note=next(message.content for message in context.history if '最近中断执行数据' in str(message.content))
        assert next(f for f in json.loads(note.split('\n',1)[1])['facts'] if f.get('path')=='interruption.txt')['state']=='confirmed'


@pytest.mark.anyio
async def test_unknown_external_tool_keeps_public_warning_without_private_payload(command_root,isolated_command_database):
    """Args:
        command_root：隔离文件目录。
        isolated_command_database：本例独立数据库。
    """
    from app.db import SessionLocal
    from app.models import Message
    async with command_conversation(command_root) as (client,headers,cid,rid,wid):
        reply=await stopped_file(client,headers,cid,rid,wid)
        async with SessionLocal() as session:
            message=await session.get(Message,reply['id'])
            message.parts_json=[*message.parts_json,{'type':'tool_call','call_id':'external','tool_name':'mcp_fixture_action','status':'interrupted'}]
            message.status='interrupted'
            await session.commit()
        context=await context_for(cid,rid)
        note=next(message.content for message in context.history if '最近中断执行数据' in str(message.content))
        fact=next(f for f in json.loads(note.split('\n',1)[1])['facts'] if f.get('call_id')=='external')
        assert fact['status']=='interrupted' and '不自动重放' in fact['note']
        assert not any(key in fact for key in ('script','input','output','arguments'))


def test_generic_external_facts_also_obey_handoff_budget():
    """无工作区等提前返回路径，也必须明确截断而非无限扩张上下文。"""
    from app.context.interruption import _bounded_text,MAX_TEXT_BYTES
    value={'facts':[{'tool':'外部工具'*100,'status':'interrupted'} for _ in range(64)],'incomplete':False}
    text=_bounded_text(value)
    result=json.loads(text.split('\n',1)[1])
    assert len(text.encode())<=MAX_TEXT_BYTES
    assert result['incomplete'] is True and 0<len(result['facts'])<64
