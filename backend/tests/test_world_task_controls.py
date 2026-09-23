"""续办、精确停止、检索撤权与来源失效的确定性边界验证。"""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, delete

from test_orchestrator import setup_group, command_root, isolated_command_database


async def park(monkeypatch):
    from app.scheduling import conversation_scheduler
    async def parked(*args): pass
    monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
    monkeypatch.setattr(conversation_scheduler, 'enqueue_parallel', parked)


async def root(client, headers, role_id):
    state = (await client.post('/api/world-orchestrator/import-role', headers=headers,
        json={'role_id': role_id, 'expected_revision': 0})).json()
    sent = await client.post(f'/api/conversations/{state["conversation"]["id"]}/messages', headers=headers,
        json={'parts': [{'type': 'text', 'text': '受控任务目标'}], 'world_task_mode': 'execute'})
    assert sent.status_code == 202
    return state, (await client.get('/api/world-orchestrator/tasks', headers=headers)).json()['items'][0]


@pytest.mark.anyio
async def test_continue_reuses_budget_and_duplicate_returns_only_its_generation(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, WorkflowBudget, WorldTask
    from app.world_orchestrator import tasks
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, _, ids, _):
        state, task = await root(client, headers, ids[3])
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == task['root_execution_id']))
            execution.status = 'completed'
            (await session.get(Generation, execution.generation_id)).status = 'completed'
            budget = await session.get(WorkflowBudget, task['chain_id'])
            budget.used_decisions = 3; budget.decision_limit = 9
            await session.commit()
        await tasks.tick()
        task = (await client.get('/api/world-orchestrator/tasks', headers=headers)).json()['items'][0]
        payload = {'parts': [{'type': 'text', 'text': '补充受控要求，沿用原额度'}], 'client_message_id': uuid4().hex,
            'world_task_mode': 'execute', 'world_task_id': task['id'], 'expected_task_revision': task['revision'],
            'expected_appointment_revision': state['revision']}
        url = f'/api/conversations/{state["conversation"]["id"]}/messages'
        response = await client.post(url, headers=headers, json=payload)
        assert response.status_code == 202
        duplicate = await client.post(url, headers=headers, json=payload)
        assert duplicate.status_code == 202 and duplicate.json()['duplicate']
        assert duplicate.json()['generation_ids'] == response.json()['generation_ids']
        async with SessionLocal() as session:
            assert len((await session.scalars(select(WorldTask))).all()) == 1
            updated = await session.get(WorkflowBudget, task['chain_id'])
            assert (updated.used_decisions, updated.decision_limit) == (3, 9)
            followup = await session.scalar(select(AgentExecution).where(AgentExecution.generation_id == response.json()['generation_id']))
            assert followup.parent_execution_id == task['root_execution_id'] and followup.chain_id == task['chain_id']


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['stop', 'unbind'])
async def test_exact_child_stop_and_root_stop_preserve_other_work(command_root, isolated_command_database, monkeypatch, action):
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation
    from app.world_orchestrator import tasks
    from app.world_orchestrator.tools import Delegate
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, cid, ids, wid):
        other = (await client.post('/api/conversations', headers=headers,
            json={'type': 'group', 'title': '第二个待执行群', 'role_ids': ids, 'workspace_binding_id': wid})).json()['id']
        for target in (cid, other):
            assert (await client.put(f'/api/conversations/{target}/orchestrator', headers=headers,
                json={'role_id': ids[3], 'expected_revision': 0})).status_code == 200
        _, task = await root(client, headers, ids[3])
        first = await tasks.delegate(task['root_execution_id'], Delegate(conversation_id=cid, goal='准备甲群计划', mode='design', request_key='a'))
        second = await tasks.delegate(task['root_execution_id'], Delegate(conversation_id=other, goal='准备乙群计划', mode='design', request_key='b'))
        duplicate = await tasks.delegate(task['root_execution_id'], Delegate(conversation_id=cid, goal='准备甲群计划', mode='design', request_key='a'))
        assert duplicate['id'] == first['id']
        independent = (await client.post(f'/api/conversations/{cid}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '独立群消息'}], 'mentions': [ids[0]]})).json()['generation_id']
        task = (await client.get('/api/world-orchestrator/tasks', headers=headers)).json()['items'][0]
        response = await client.post(f'/api/world-orchestrator/tasks/{task["id"]}/children/{first["id"]}/stop', headers=headers,
            json={'expected_revision': first['revision']})
        assert response.status_code == 200
        async with SessionLocal() as session:
            first_exec = await session.scalar(select(AgentExecution).where(AgentExecution.chain_id == first['chain_id']))
            second_exec = await session.scalar(select(AgentExecution).where(AgentExecution.chain_id == second['chain_id']))
            assert (await session.get(Generation, first_exec.generation_id)).status == 'stopped'
            assert (await session.get(Generation, second_exec.generation_id)).status == 'queued'
            assert (await session.get(Generation, independent)).status == 'queued'
        if action == 'stop':
            response = await client.post(f'/api/world-orchestrator/tasks/{task["id"]}/stop', headers=headers,
                json={'expected_revision': task['revision']})
        else:
            state = (await client.get('/api/world-orchestrator', headers=headers)).json()
            response = await client.post('/api/world-orchestrator/enabled', headers=headers,
                json={'enabled': False, 'expected_revision': state['revision']})
        assert response.status_code == 200
        await tasks.tick()
        async with SessionLocal() as session:
            assert (await session.get(Generation, second_exec.generation_id)).status == 'stopped'
            assert (await session.get(Generation, independent)).status == 'queued'


@pytest.mark.anyio
async def test_restart_marks_world_task_interrupted_without_replay(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AgentExecution, WorldTask
    from app.world_orchestrator import tasks
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, _, ids, _):
        _, task = await root(client, headers, ids[3])
        async with SessionLocal() as session:
            before = set((await session.scalars(select(AgentExecution.execution_id))).all())
        await tasks.shutdown(); await tasks.initialize(); await tasks.tick()
        async with SessionLocal() as session:
            saved = await session.get(WorldTask, task['id'])
            assert saved.status == 'interrupted'
            assert set((await session.scalars(select(AgentExecution.execution_id))).all()) == before


@pytest.mark.anyio
async def test_loaded_world_note_invalidates_next_call_and_preserves_origin(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, Message
    from app.world_orchestrator import memory
    from app.memory.service import revalidate_execution
    from app.context.domain import ContextBuildError
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, _, ids, _):
        state, task = await root(client, headers, ids[3])
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == task['root_execution_id']))
            execution.status = 'running'; (await session.get(Generation, execution.generation_id)).status = 'running'
            source = await session.scalar(select(Message).where(Message.chain_id == task['chain_id'], Message.sender_type == 'user'))
            uid = source.sender_id; await session.commit()
        entry = await memory.save(uid, memory.Save(request_key='controlled-note', text='模型整理的约定'), execution_id=execution.execution_id)
        assert entry['origin'] == 'agent' and entry['source_role_id'] == state['role_id']
        await memory.read(uid, entry['id'], 1, execution_id=execution.execution_id)
        async with SessionLocal() as session:
            await revalidate_execution(session, execution.execution_id, task['conversation_id'], state['role_id'], uid)
        disabled = await memory.edit(uid, entry['id'], memory.Edit(expected_revision=1, text=entry['text'], status='disabled'))
        assert disabled['origin'] == 'agent' and disabled['source_message_id'] == entry['source_message_id']
        async with SessionLocal() as session:
            with pytest.raises(ContextBuildError, match='CONTEXT_SOURCE_CHANGED'):
                await revalidate_execution(session, execution.execution_id, task['conversation_id'], state['role_id'], uid)
            original = await session.get(Message, entry['source_message_id']); original.revision += 1; await session.commit()
        history = (await client.get(f'/api/world-orchestrator/memories/{entry["id"]}/versions', headers=headers)).json()['items']
        assert all(item['text'] is None for item in history)


@pytest.mark.anyio
async def test_world_memory_only_reads_frozen_groups_and_revocation_closes_access(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, Message, Role, ConversationMember
    from app.memory.access import scope_for, readable_conversations
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        state, task = await root(client, headers, ids[3])
        later = (await client.post('/api/conversations', headers=headers,
            json={'type': 'group', 'title': '授权后新增群', 'role_ids': ids})).json()['id']
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == task['root_execution_id']))
            execution.status = 'running'; (await session.get(Generation, execution.generation_id)).status = 'running'
            uid = (await session.scalar(select(Message).where(Message.chain_id == task['chain_id']))).sender_id
            (await session.get(Role, state['role_id'])).builtin_tools_json = ['memory_search']
            await session.commit()
        async with SessionLocal() as session:
            scope = await scope_for(session, conversation_id=task['conversation_id'], role_id=state['role_id'], user_id=uid,
                execution_id=execution.execution_id, tool_name='memory_search', material={'current_message_id': 100, 'visible_through_message_id': 99})
            assert set((await session.scalars(readable_conversations(scope))).all()) == {cid, task['conversation_id']}
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                ConversationMember.member_type == 'user', ConversationMember.member_id == uid))
            await session.commit()
            assert set((await session.scalars(readable_conversations(scope))).all()) == {task['conversation_id']}
        assert later not in scope.world_group_ids


@pytest.mark.anyio
async def test_replan_uses_original_child_chain_without_duplicate_usage_scope(command_root, isolated_command_database, monkeypatch):
    import json
    from app.db import SessionLocal
    from app.models import CoordinationSession, AgentExecution, WorkflowBudget
    from app.world_orchestrator import tasks
    from app.world_orchestrator.tools import Delegate, Replan
    from app.workflows.graph_tools import invoke
    await park(monkeypatch)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})
        graph = {'runtime_version': 2, 'nodes': [{'id': 'approval', 'kind': 'approval', 'title': '等待 Owner 确认'}], 'edges': []}
        did = uuid4().hex
        created = await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
            json={'name': '受控确认流程', 'expected_revision': 0, 'graph': graph})
        assert created.status_code == 200
        _, task = await root(client, headers, ids[3])
        child = await tasks.delegate(task['root_execution_id'], Delegate(conversation_id=cid, goal='等待确认', request_key='dispatch',
            definition_id=did, expected_graph_revision=1))
        async with SessionLocal() as session:
            coordination = await session.get(CoordinationSession, child['coordination_id'])
        started = json.loads(await invoke(coordination.execution_id, 'workflow_start', {'expected_graph_revision': 1}))
        assert started['chain_id'] == child['chain_id']
        revised = await tasks.replan(task['root_execution_id'], Replan(child_id=child['id'], expected_graph_revision=started['graph_revision'],
            goal='保持人工确认，检查计划', request_key='replan'))
        assert revised['run_id'] == started['id'] and revised['chain_id'] is None
        again = await tasks.replan(task['root_execution_id'], Replan(child_id=child['id'], expected_graph_revision=started['graph_revision'],
            goal='保持人工确认，检查计划', request_key='replan'))
        assert again['id'] == revised['id']
        async with SessionLocal() as session:
            new_coord = await session.get(CoordinationSession, revised['coordination_id'])
            assert new_coord.chain_id == child['chain_id']
            assert (await session.get(WorkflowBudget, new_coord.chain_id)).root_chain_id == task['chain_id']
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == new_coord.execution_id))
            assert execution.parent_execution_id == task['root_execution_id']
        response = await client.post(f'/api/world-orchestrator/tasks/{task["id"]}/children/{revised["id"]}/stop', headers=headers,
            json={'expected_revision': revised['revision']})
        assert response.status_code == 200
