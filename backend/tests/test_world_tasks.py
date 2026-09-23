"""世界委派使用真实群协调、独立子 chain、共享根预算与可查询结果。"""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select

from test_orchestrator import setup_group, command_root, isolated_command_database


@pytest.mark.anyio
async def test_world_task_delegates_two_groups_and_counts_each_call_once(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal
    from app.models import AgentExecution, WorkflowBudget
    async with setup_group(command_root) as (client, headers, cid, ids, wid):
        other = (await client.post('/api/conversations', headers=headers,
            json={'type': 'group', 'title': '另一个受控任务群', 'role_ids': ids, 'workspace_binding_id': wid})).json()['id']
        for target in [cid, other]:
            assert (await client.put(f'/api/conversations/{target}/orchestrator', headers=headers,
                json={'role_id': ids[3], 'expected_revision': 0})).status_code == 200
            graph = {'runtime_version': 2, 'nodes': [{'id': 'work', 'kind': 'role', 'title': '受控执行',
                'role_id': ids[0], 'task': '完成受控回复', 'tools': []}], 'edges': []}
            assert (await client.put(f'/api/conversations/{target}/workflows/definitions/{uuid4().hex}', headers=headers,
                json={'name': '受控任务模板', 'expected_revision': 0, 'graph': graph})).status_code == 200
        appointed = (await client.post('/api/world-orchestrator/import-role', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})).json()
        coordinator = appointed['conversation']['id']
        response = await client.post(f'/api/conversations/{coordinator}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '[WORLD_TASK_PROBE] 安排两个群完成各自任务并汇总'}], 'world_task_mode': 'execute'})
        assert response.status_code == 202
        for _ in range(600):
            listing = await client.get('/api/world-orchestrator/tasks', headers=headers)
            assert listing.status_code == 200
            tasks = listing.json()['items']
            if tasks and tasks[0]['status'] in {'completed', 'failed', 'stopped'}:
                break
            await asyncio.sleep(.03)
        task = tasks[0]
        assert task['status'] == 'completed', (task['status'], task['error_code'])
        assert len(task['children']) == 2 and all(c['status'] == 'completed' for c in task['children'])
        assert {c['conversation_id'] for c in task['children']} == {cid, other}
        assert len({c['chain_id'] for c in task['children']}) == 2
        async with SessionLocal() as session:
            root = await session.get(WorkflowBudget, task['chain_id'])
            chains = [task['chain_id'], *[c['chain_id'] for c in task['children']]]
            executions = (await session.scalars(select(AgentExecution).where(AgentExecution.chain_id.in_(chains)))).all()
            from sqlalchemy import func
            total = select(func.sum(AgentExecution.decision_count)).where(AgentExecution.chain_id.in_(chains)).scalar_subquery()
            charged, observed = (await session.execute(select(WorkflowBudget.used_decisions, total).where(WorkflowBudget.chain_id == task['chain_id']))).one()
            assert charged == observed
            for child in task['children']:
                assert (await session.get(WorkflowBudget, child['chain_id'])).root_chain_id == root.chain_id
        # 后续只读回合确实拿到子群结果；群成员撤销后旧工具材料也不得继续进入模型。
        from app.scheduling import conversation_scheduler
        async def parked(*args): pass
        monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
        read_request = await client.post(f'/api/conversations/{coordinator}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '核对之前的世界结果'}]})
        from app.world_orchestrator.tools import invoke
        from app.models import ConversationMember, Message
        from app.memory.service import revalidate_execution
        from app.context.domain import ContextBuildError
        from sqlalchemy import delete
        async with SessionLocal() as session:
            reader = await session.scalar(select(AgentExecution).where(AgentExecution.generation_id == read_request.json()['generation_id']))
            uid = (await session.get(Message, read_request.json()['message']['id'])).sender_id
        await invoke(reader.execution_id, 'world_read_task', {'task_id': task['id']})
        async with SessionLocal() as session:
            await revalidate_execution(session, reader.execution_id, coordinator, appointed['role_id'], uid)
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                ConversationMember.member_type == 'user', ConversationMember.member_id == uid))
            await session.commit()
            with pytest.raises(ContextBuildError, match='CONTEXT_SOURCE_CHANGED'):
                await revalidate_execution(session, reader.execution_id, coordinator, appointed['role_id'], uid)


@pytest.mark.anyio
async def test_type_activity_uses_registered_schema_actual_tools_and_original_budget(command_root, isolated_command_database):
    from app.world_types import service, registry
    from world_types_fixture import install
    from app.db import SessionLocal
    from app.models import WorldTypeState, WorkflowBudget
    install()
    try:
        async with setup_group(command_root) as (client, headers, _, ids, _):
            service.configure('controlled', 'fixture', 1)
            role = (await client.get(f'/api/roles/{ids[0]}', headers=headers)).json()
            view = (await client.get('/api/world-type', headers=headers)).json()
            configured = await client.put('/api/world-type/config', headers=headers, json={'expected_revision': view['revision'],
                'configuration': {'label': '受控类型活动', 'model_config_id': role['model_config_id']}})
            assert configured.status_code == 200 and configured.json()['initialization']['status'] == 'ready'
            state = (await client.post('/api/world-orchestrator/import-role', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})).json()
            sent = await client.post(f'/api/conversations/{state["conversation"]["id"]}/messages', headers=headers,
                json={'parts': [{'type': 'text', 'text': '[WORLD_ACTIVITY_PROBE] 运行本类型的活动'}], 'world_task_mode': 'execute'})
            assert sent.status_code == 202
            for _ in range(700):
                tasks = (await client.get('/api/world-orchestrator/tasks', headers=headers)).json()['items']
                if tasks and tasks[0]['status'] in {'completed', 'failed', 'stopped'}:
                    break
                await asyncio.sleep(.03)
            task = tasks[0]
            assert task['status'] == 'completed', (task['error_code'], [(c['status'], c['error_code']) for c in task['children']])
            assert len(task['children']) == 1 and task['children'][0]['kind'] == 'activity'
            async with SessionLocal() as session:
                saved = await session.get(WorldTypeState, 1)
                assert saved.resources_json['recorded'] is True
                assert saved.resources_json['tool_task_id'] == task['id']
                assert saved.resources_json['tool_execution_id']
                assert (await session.get(WorkflowBudget, task['children'][0]['chain_id'])).root_chain_id == task['chain_id']
    finally:
        registry.unregister('fixture', 1)
        service.configure('default')


@pytest.mark.anyio
async def test_parallel_children_share_atomic_last_decision(command_root, isolated_command_database, monkeypatch):
    from app.db import SessionLocal, now_utc
    from app.models import AgentExecution, Generation, WorkflowBudget, WorldTask, WorldTaskChild
    from app.services.agent_budget import consume
    from app.scheduling import conversation_scheduler
    async def parked(*args): pass
    monkeypatch.setattr(conversation_scheduler, 'enqueue', parked)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        state = (await client.post('/api/world-orchestrator/import-role', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})).json()
        await client.post(f'/api/conversations/{state["conversation"]["id"]}/messages', headers=headers,
            json={'parts': [{'type': 'text', 'text': '并行预算受控验证'}], 'world_task_mode': 'execute'})
        executions = []
        async with SessionLocal() as session:
            task = await session.scalar(select(WorldTask))
            root = await session.get(WorkflowBudget, task.chain_id)
            root.decision_limit = 1
            for i in range(2):
                chain, eid = uuid4().hex, uuid4().hex
                session.add(WorkflowBudget(chain_id=chain, root_chain_id=root.chain_id, conversation_id=cid,
                    decision_limit=8, configuration_revision=0, used_decisions=0, created_at=now_utc()))
                generation = Generation(conversation_id=cid, stream_epoch='controlled', status='running', run_id=chain)
                session.add(generation); await session.flush()
                session.add(AgentExecution(execution_id=eid, parent_execution_id=task.root_execution_id,
                    conversation_id=cid, generation_id=generation.id, chain_id=chain, role_id=ids[i],
                    execution_kind='group_role', status='running', created_at=now_utc()))
                await session.flush()
                session.add(WorldTaskChild(id=uuid4().hex, task_id=task.id, parent_execution_id=task.root_execution_id,
                    request_key=str(i), request_digest=str(i), kind='group', conversation_id=cid, chain_id=chain,
                    status='running', reference_json={}, created_at=now_utc()))
                executions.append(eid)
            await session.commit()
        results = await asyncio.gather(*(consume(eid, 1) for eid in executions))
        assert sorted(results) == [False, True]
        async with SessionLocal() as session:
            assert (await session.get(WorkflowBudget, root.chain_id)).used_decisions == 1
            rows = (await session.scalars(select(AgentExecution).where(AgentExecution.execution_id.in_(executions)))).all()
            assert sum(row.decision_count for row in rows) == 1
