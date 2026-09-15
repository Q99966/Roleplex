"""T4.3a World 配置与消息链共享额度，使用每例独立迁移数据库。"""
import asyncio
from datetime import datetime, timezone
import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation


@pytest.mark.anyio
async def test_configuration_owner_revision_and_ceiling(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：隔离目录。
        isolated_command_database：每例新数据库。
        monkeypatch：受控部署上限。
    """
    from accounts import guest_username
    from app.config import settings
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        before = (await client.get('/api/agent-budget/config', headers=headers)).json()
        assert before == {'decision_limit':8, 'effective_limit':8, 'ceiling':256, 'revision':0}
        results = await asyncio.gather(*[client.put('/api/agent-budget/config', headers=headers,
            json={'decision_limit':value, 'expected_revision':0}) for value in [32, 64]])
        assert sorted(result.status_code for result in results) == [200, 409]
        for value in [0, True, 1.5, 257]:
            assert (await client.put('/api/agent-budget/config', headers=headers,
                json={'decision_limit':value, 'expected_revision':1})).status_code == 422
        monkeypatch.setattr(settings, 'agent_decision_ceiling', 16)
        limited = (await client.get('/api/agent-budget/config', headers=headers)).json()
        assert limited['effective_limit'] == 16 and limited['revision'] == 1
        assert (await client.put('/api/agent-budget/config', headers=headers,
            json={'decision_limit':32, 'expected_revision':1})).status_code == 422
        guest = await client.post('/api/auth/register', json={'username':guest_username('budget'), 'nickname':'测试', 'password':'Roleplex-Test-1234'})
        guest_headers = {'Authorization': 'Bearer ' + guest.json()['access_token']}
        assert (await client.get('/api/agent-budget/config', headers=guest_headers)).status_code == 403
        assert (await client.put('/api/agent-budget/config', headers=guest_headers,
            json={'decision_limit':8, 'expected_revision':1})).status_code == 403


@pytest.mark.anyio
async def test_concurrent_decisions_share_frozen_budget_and_retry_is_idempotent(command_root, isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：并发测试独占全新数据库。
    """
    from app.db import SessionLocal
    from app.models import Message, Generation, AgentExecution, WorkflowBudget
    from app.services.agent_budget import consume, freeze
    from sqlalchemy import select
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        assert (await client.put('/api/agent-budget/config', headers=headers,
            json={'decision_limit':5,'expected_revision':0})).status_code == 200
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            message = Message(conversation_id=cid, sender_type='user', sender_id=1, status='done', chain_id='shared-budget', created_at=now)
            session.add(message); await session.flush(); await freeze(session, message)
            for index in range(20):
                generation = Generation(conversation_id=cid, stream_epoch='test', status='running', run_id='shared-budget')
                session.add(generation); await session.flush()
                session.add(AgentExecution(execution_id=f'budget-{index}', conversation_id=cid, generation_id=generation.id,
                    chain_id='shared-budget', role_id=rid, execution_kind='group_role', status='running', created_at=now))
            await session.commit()
        assert (await client.put('/api/agent-budget/config', headers=headers,
            json={'decision_limit':64,'expected_revision':1})).status_code == 200
        results = await asyncio.gather(*[consume(f'budget-{index}', 1) for index in range(20)])
        assert sum(results) == 5
        first = results.index(True)
        assert await consume(f'budget-{first}', 1) is True
        assert await consume(f'budget-{first}', 2) is False
        async with SessionLocal() as session:
            budget = await session.get(WorkflowBudget, 'shared-budget')
            assert (budget.decision_limit, budget.used_decisions, budget.configuration_revision) == (5,5,1)
            executions = list((await session.scalars(select(AgentExecution).where(AgentExecution.chain_id=='shared-budget'))).all())
            assert sum(row.decision_count for row in executions) == 5


@pytest.mark.anyio
async def test_group_roles_share_one_grant_and_idempotent_message_does_not_refill(command_root, isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：独立消息调度数据库。
    """
    from app.db import SessionLocal
    from app.models import WorkflowBudget, AgentExecution
    from sqlalchemy import select
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = (await client.get('/api/roles', headers=headers)).json()[0]
        second = await client.post('/api/roles', headers=headers, json={'name':'第二预算角色', 'model_config_id':role['model_config_id'],
            'model_name':'fake-model','system_prompt':'简单回答。'})
        group = await client.post('/api/conversations', headers=headers,
            json={'title':'共享预算群聊','type':'group','role_ids':[rid,second.json()['id']]})
        gid = group.json()['id']
        assert (await client.put('/api/agent-budget/config', headers=headers,json={'decision_limit':1,'expected_revision':0})).status_code == 200
        payload = {'parts':[{'type':'text','text':'请简短回答'}], 'mentions':['all'], 'client_message_id':'budget-idempotent'}
        sent = await client.post(f'/api/conversations/{gid}/messages', headers=headers, json=payload)
        assert sent.status_code == 202
        for _ in range(200):
            messages = (await client.get(f'/api/conversations/{gid}/messages', headers=headers)).json()['items']
            replies = [row for row in messages if row['sender_type']=='role']
            if len(replies)==2 and all(row['status'] in {'done','stopped','error'} for row in replies): break
            await asyncio.sleep(.02)
        assert sorted(row['status'] for row in replies) == ['done','stopped']
        assert [row['stop_reason'] for row in replies if row['status']=='stopped'] == ['decision_budget']
        assert (await client.put('/api/agent-budget/config', headers=headers,json={'decision_limit':64,'expected_revision':1})).status_code == 200
        again = await client.post(f'/api/conversations/{gid}/messages', headers=headers, json=payload)
        assert again.json()['message']['id'] == sent.json()['message']['id']
        async with SessionLocal() as session:
            budget = await session.scalar(select(WorkflowBudget).where(WorkflowBudget.conversation_id==gid))
            assert budget.decision_limit == budget.used_decisions == 1
            executions = list((await session.scalars(select(AgentExecution).where(AgentExecution.chain_id==budget.chain_id))).all())
            assert sorted(row.decision_count for row in executions) == [0,1]


@pytest.mark.anyio
async def test_stop_request_prevents_decision_charge(command_root, isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：每例独立数据库。
    """
    from app.db import SessionLocal
    from app.models import Message, Generation, AgentExecution, WorkflowBudget
    from app.services.agent_budget import consume, freeze
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            message = Message(conversation_id=cid,sender_type='user',sender_id=1,status='done',chain_id='cancel-budget',created_at=now)
            session.add(message); await session.flush(); await freeze(session,message)
            generation = Generation(conversation_id=cid,stream_epoch='test',status='running',run_id='cancel-budget',stop_requested_at=now)
            session.add(generation); await session.flush()
            session.add(AgentExecution(execution_id='cancel-budget-exec',conversation_id=cid,generation_id=generation.id,
                chain_id='cancel-budget',role_id=rid,execution_kind='single',status='running',created_at=now))
            await session.commit()
        with pytest.raises(asyncio.CancelledError):
            await consume('cancel-budget-exec',1)
        async with SessionLocal() as session:
            assert (await session.get(WorkflowBudget,'cancel-budget')).used_decisions == 0
