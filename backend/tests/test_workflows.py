"""工作流真实队列与 API 并发边界，使用隔离数据库与 fake Provider。"""
import asyncio
from uuid import uuid4
import pytest
from test_workspace_commands import command_root, isolated_command_database
from test_group_workspaces import group_workspace


def graph(writer, editor, approval=True):
    """写入→可选人工确认→读取编辑，同一链路共享预算。"""
    nodes = [{'id': 'write', 'kind': 'role', 'title': '开发', 'role_id': writer, 'task': '[GROUP_FILES_FAKE]', 'inputs': []}]
    if approval:
        nodes.append({'id': 'review', 'kind': 'approval', 'title': '人工核对', 'inputs': ['write']})
    nodes.append({'id': 'edit', 'kind': 'role', 'title': '审查', 'role_id': editor, 'task': '[GROUP_FILES_FAKE]', 'inputs': ['write']})
    return {'nodes': nodes, 'edges': [[a['id'], b['id']] for a, b in zip(nodes, nodes[1:])]}


async def wait_run(client, headers, cid, rid, statuses):
    for _ in range(500):
        response = await client.get(f'/api/conversations/{cid}/workflows', headers=headers)
        assert response.status_code == 200
        row = next(r for r in response.json()['runs'] if r['id'] == rid)
        if row['status'] in statuses:
            return row
        await asyncio.sleep(.03)
    raise AssertionError('工作流未达到预期状态')


@pytest.mark.anyio
async def test_workflow_serial_approval_and_idempotency(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        base = f'/api/conversations/{cid}/workflows'
        did = uuid4().hex
        body = {'name': '开发审查', 'graph': graph(writer, editor), 'expected_revision': 0}
        saved = await client.put(f'{base}/definitions/{did}', headers=headers, json=body)
        assert saved.status_code == 200
        assert (await client.put(f'{base}/definitions/{did}', headers=headers, json=body)).status_code == 409
        request = {'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex}
        starts = await asyncio.gather(*[client.post(f'{base}/runs', headers=headers, json=request) for _ in range(2)])
        assert [r.status_code for r in starts] == [202, 202]
        rid = starts[0].json()['id']
        assert starts[1].json()['id'] == rid
        waiting = await wait_run(client, headers, cid, rid, ['waiting', 'failed', 'blocked'])
        assert waiting['status'] == 'waiting'
        assert (command_root / 'group.txt').read_text() == 'first'
        denied = await client.put(f'/api/conversations/{cid}/workspace', headers=headers,
            json={'workspace_binding_id': None, 'expected_revision': 0})
        assert denied.status_code == 409
        a = next(a for a in waiting['attempts'] if a['status'] == 'waiting')
        response = await client.post(f'{base}/runs/{rid}/control', headers=headers,
            json={'action': 'confirm', 'attempt_id': a['id'], 'expected_revision': waiting['revision']})
        assert response.status_code == 200
        done = await wait_run(client, headers, cid, rid, ['completed', 'failed', 'blocked', 'stopped'])
        assert done['status'] == 'completed'
        assert (command_root / 'group.txt').read_text() == 'second'
        assert done['used_decisions'] == 5
        assert len(done['attempts']) == 3
        assert done['attempts'][-1]['upstream_ids'] == [done['attempts'][0]['id']]


async def launch(client, headers, cid, definition):
    base = f'/api/conversations/{cid}/workflows'
    did = uuid4().hex
    saved = await client.put(f'{base}/definitions/{did}', headers=headers,
        json={'name': '流程验证', 'graph': definition, 'expected_revision': 0})
    assert saved.status_code == 200
    started = await client.post(f'{base}/runs', headers=headers,
        json={'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex})
    assert started.status_code == 202
    return did, started.json()['id']


@pytest.mark.anyio
async def test_repeated_role_snapshot_budget_and_guest(command_root, isolated_command_database):
    from accounts import guest_username, TEST_PASSWORD
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        nodes = [{'id': str(i), 'kind': 'role', 'title': f'步骤{i}', 'role_id': writer, 'task': f'任务{i}', 'inputs': [str(i-1)] if i else []} for i in range(3)]
        definition = {'nodes': nodes, 'edges': [['0', '1'], ['1', '2']]}
        did, rid = await launch(client, headers, cid, definition)
        base = f'/api/conversations/{cid}/workflows'
        update = await client.put(f'{base}/definitions/{did}', headers=headers,
            json={'name': '新版', 'graph': {'nodes': nodes[:1], 'edges': []}, 'expected_revision': 1})
        assert update.status_code == 200
        done = await wait_run(client, headers, cid, rid, ['completed', 'failed'])
        assert done['status'] == 'completed' and len(done['graph']['nodes']) == 3
        assert len({a['execution_id'] for a in done['attempts']}) == 3
        assert done['used_decisions'] == 3
        guest = (await client.post('/api/auth/register', json={'username': guest_username('workflow'),
            'nickname': 'Guest', 'password': TEST_PASSWORD})).json()
        gh = {'Authorization': f"Bearer {guest['access_token']}"}
        assert (await client.get(base, headers=gh)).status_code == 403
        assert (await client.post(f'{base}/runs/{rid}/control', headers=gh,
            json={'action': 'stop', 'expected_revision': done['revision']})).status_code == 403
        other = (await client.post('/api/conversations', headers=headers, json={'type': 'single', 'title': '其他', 'role_ids': [writer]})).json()['id']
        assert (await client.post(f'/api/conversations/{other}/workflows/runs/{rid}/control', headers=headers,
            json={'action': 'stop', 'expected_revision': done['revision']})).status_code == 404


@pytest.mark.anyio
async def test_review_retry_keeps_attempts_and_checks_group_file_facts(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor, False))
        done = await wait_run(client, headers, cid, rid, ['completed', 'failed'])
        assert done['status'] == 'completed'
        aid = done['attempts'][0]['id']
        base = f'/api/conversations/{cid}/workflows/runs/{rid}'
        facts = await client.get(f'{base}/attempts/{aid}/facts', headers=headers)
        assert facts.status_code == 200
        assert 'confirmed' in facts.json()['text'] and 'changed' in facts.json()['text']
        body = {'action': 'retry', 'attempt_id': aid, 'expected_revision': done['revision'],
            'instruction': '只核对，不重放已提交操作', 'acknowledge_facts': False}
        assert (await client.post(f'{base}/control', headers=headers, json=body)).status_code == 409
        body['acknowledge_facts'] = True
        retried = await client.post(f'{base}/control', headers=headers, json=body)
        assert retried.status_code == 200
        again = await wait_run(client, headers, cid, rid, ['stopped', 'failed'])
        assert again['status'] == 'stopped'
        assert len(again['attempts']) == 3
        assert [a['node_id'] for a in again['attempts'] if a['current']] == ['write']
        assert again['attempts'][-1]['retry_source_id'] == aid
        assert again['used_decisions'] == 7
        assert (command_root / 'group.txt').read_text() == 'second'


@pytest.mark.anyio
async def test_confirm_stop_competition_and_restart_waiting(command_root, isolated_command_database):
    from app.workflows import service
    from app.db import SessionLocal
    from app.models import ExecutionWorkspace, AgentExecution
    from sqlalchemy import select
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor))
        waiting = await wait_run(client, headers, cid, rid, ['waiting'])
        async with SessionLocal() as session:
            leases = (await session.scalars(select(ExecutionWorkspace).join(AgentExecution,
                AgentExecution.execution_id == ExecutionWorkspace.execution_id).where(AgentExecution.conversation_id == cid))).all()
            assert all(a.status == 'retained' for a in leases)
        await service.shutdown()
        await service.initialize()
        restored = await wait_run(client, headers, cid, rid, ['waiting'])
        assert restored['revision'] == waiting['revision']
        aid = next(a['id'] for a in restored['attempts'] if a['status'] == 'waiting')
        async def action(action):
            return await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
                json={'action': action, 'attempt_id': aid, 'expected_revision': restored['revision']})
        responses = await asyncio.gather(action('confirm'), action('stop'))
        assert sorted(r.status_code for r in responses) == [200, 409]
        settled = await wait_run(client, headers, cid, rid, ['completed', 'stopped', 'failed'])
        assert len([a for a in settled['attempts'] if a['node_id'] == 'edit']) <= 1


@pytest.mark.anyio
async def test_stop_exact_run_and_restart_does_not_replay(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import GroupFileModel, fake_reply_model
    from app.services import chat
    from app.workflows import service
    from app.scheduling import conversation_scheduler
    reached, release = asyncio.Event(), asyncio.Event()
    class Paused(GroupFileModel):
        async def _astream(self, messages, **kwargs):
            if self.index == 1:
                reached.set()
                await release.wait()
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Paused(delay=0) if '[GROUP_FILES_FAKE]' in prompt else fake_reply_model(prompt, delay=0))
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor, False))
        try:
            await asyncio.wait_for(reached.wait(), 10)
            await service.shutdown()
            await conversation_scheduler.start(chat.run_scheduled_generation)
            await service.initialize()
            interrupted = await wait_run(client, headers, cid, rid, ['interrupted'])
            assert len(interrupted['attempts']) == 1
            assert (command_root / 'group.txt').read_text() == 'first'
            await asyncio.sleep(.3)
            assert (command_root / 'group.txt').read_text() == 'first'
        finally:
            release.set()


@pytest.mark.anyio
async def test_waiting_member_revocation_blocks_next_dispatch(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import ConversationMember
    from sqlalchemy import delete
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor))
        await wait_run(client, headers, cid, rid, ['waiting'])
        async with SessionLocal() as session:
            await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == cid,
                ConversationMember.member_type == 'role', ConversationMember.member_id == editor))
            await session.commit()
        blocked = await wait_run(client, headers, cid, rid, ['blocked'])
        assert blocked['error_code'] == 'WORKFLOW_ROLE_UNAVAILABLE'
        assert (command_root / 'group.txt').read_text() == 'first'
        assert not any(a['node_id'] == 'edit' for a in blocked['attempts'])


@pytest.mark.anyio
async def test_graph_rejects_unknown_endpoints_missing_roles_and_input_references(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        for bad in [
            {'nodes': graph(writer, editor, False)['nodes'], 'edges': [['missing', 'write']]},
            {'nodes': [{'id': 'x', 'kind': 'role', 'title': 'x', 'task': 'x', 'role_id': 999999}], 'edges': []},
            {'nodes': [{'id': 'x', 'kind': 'role', 'title': 'x', 'task': 'x', 'role_id': writer, 'inputs': ['missing']}], 'edges': []},
        ]:
            response = await client.put(f'/api/conversations/{cid}/workflows/definitions/{uuid4().hex}', headers=headers,
                json={'name': '无效', 'graph': bad, 'expected_revision': 0})
            assert response.status_code == 422


@pytest.mark.anyio
async def test_stop_run_does_not_stop_later_ordinary_chat(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import GroupFileModel, fake_reply_model
    from app.services import chat
    from test_group_workspaces import finished
    reached, release = asyncio.Event(), asyncio.Event()
    class Paused(GroupFileModel):
        async def _astream(self, messages, **kwargs):
            if self.index == 1:
                reached.set()
                await release.wait()
            async for chunk in super()._astream(messages, **kwargs):
                yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Paused(delay=0) if '[GROUP_FILES_FAKE]' in prompt else fake_reply_model(prompt, delay=0))
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor, False))
        try:
            await asyncio.wait_for(reached.wait(), 10)
            ordinary = await client.post(f'/api/conversations/{cid}/messages', headers=headers,
                json={'parts': [{'type': 'text', 'text': '独立聊天消息'}], 'mentions': [editor]})
            assert ordinary.status_code == 202
            for _ in range(100):
                state = await wait_run(client, headers, cid, rid, ['running'])
                if state['attempts'][0]['status'] == 'running':
                    break
                await asyncio.sleep(.03)
            assert state['attempts'][0]['status'] == 'running'
            stop = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
                json={'action': 'stop', 'expected_revision': state['revision']})
            assert stop.status_code == 200
            stopped = await wait_run(client, headers, cid, rid, ['stopped'])
            assert len(stopped['attempts']) == 1
            messages = await finished(client, headers, cid)
            assert any(m['sender_type'] == 'role' and m['sender_id'] == editor and m['status'] == 'done' for m in messages)
            assert (command_root / 'group.txt').read_text() == 'first'
        finally:
            release.set()


@pytest.mark.anyio
async def test_budget_exhaustion_does_not_reset_at_next_node(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import InstanceSettings
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        async with SessionLocal() as session:
            (await session.get(InstanceSettings, 1)).decision_limit = 1
            await session.commit()
        nodes = [{'id': str(i), 'kind': 'role', 'title': str(i), 'role_id': writer, 'task': '只回答一句话', 'inputs': []} for i in range(2)]
        _, rid = await launch(client, headers, cid, {'nodes': nodes, 'edges': [['0', '1']]})
        stopped = await wait_run(client, headers, cid, rid, ['stopped', 'failed'])
        assert stopped['status'] == 'stopped'
        assert stopped['used_decisions'] == stopped['decision_limit'] == 1
        assert [a['status'] for a in stopped['attempts']] == ['completed', 'stopped']


@pytest.mark.anyio
async def test_waiting_tool_revocation_blocks_dispatch(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        _, rid = await launch(client, headers, cid, graph(writer, editor))
        await wait_run(client, headers, cid, rid, ['waiting'])
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': False})
        blocked = await wait_run(client, headers, cid, rid, ['blocked'])
        assert blocked['error_code'] == 'WORKFLOW_CAPABILITY_CHANGED'
        assert (command_root / 'group.txt').read_text() == 'first'


@pytest.mark.anyio
async def test_facts_target_exact_attempt_and_expired_evidence_remains_unknown(command_root, isolated_command_database, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import FileEffect, ToolExecutionDetail
    from app.services import chat
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    def model(prompt):
        target = 'a.txt' if 'FIRST_NODE' in prompt else 'b.txt'
        return ScriptedChatModel(delay=0, turns=[ScriptedTurn(tool_calls=[{
            'name': 'workspace_write', 'args': {'path': target, 'content': 'controlled'}, 'id': 'write'}]), ScriptedTurn(text='完成')])
    monkeypatch.setattr(chat, 'fake_reply_model', model)
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        nodes = [{'id': str(i), 'kind': 'role', 'title': str(i), 'role_id': writer, 'task': task, 'inputs': []}
                 for i, task in enumerate(['FIRST_NODE', 'SECOND_NODE'])]
        _, rid = await launch(client, headers, cid, {'nodes': nodes, 'edges': [['0', '1']]})
        done = await wait_run(client, headers, cid, rid, ['completed'])
        attempt = done['attempts'][0]
        url = f"/api/conversations/{cid}/workflows/runs/{rid}/attempts/{attempt['id']}/facts"
        result = (await client.get(url, headers=headers)).json()['text']
        assert 'a.txt' in result and 'b.txt' not in result and 'confirmed' in result
        async with SessionLocal() as session:
            for model_type in (FileEffect, ToolExecutionDetail):
                for row in (await session.scalars(select(model_type).where(model_type.execution_id == attempt['execution_id']))).all():
                    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()
        expired = (await client.get(url, headers=headers)).json()['text']
        import json
        evidence = json.loads(expired.split('\n', 1)[1])
        assert evidence['incomplete'] is True
        assert any(fact.get('state') == 'unknown' for fact in evidence['facts'])
        assert not any(fact.get('state') == 'confirmed' or fact.get('path') == 'a.txt' for fact in evidence['facts'])


@pytest.mark.anyio
async def test_single_conversation_uses_same_workflow_entry(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        single = (await client.post('/api/conversations', headers=headers, json={
            'title': '单聊流程', 'type': 'single', 'role_ids': [writer], 'workspace_binding_id': wid})).json()['id']
        _, rid = await launch(client, headers, single, {'nodes': [
            {'id': 'single', 'kind': 'role', 'title': '单聊任务', 'role_id': writer, 'task': '简单回复', 'inputs': []}], 'edges': []})
        done = await wait_run(client, headers, single, rid, ['completed', 'failed'])
        assert done['status'] == 'completed' and done['used_decisions'] == 1


@pytest.mark.anyio
async def test_required_upstream_is_not_silently_trimmed(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: ScriptedChatModel(delay=0, chunk_size=100000,
        turns=[ScriptedTurn(text='A' * 100000 if 'LARGE_SOURCE' in prompt else '不应调用')]))
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == editor)
        updated = await client.put(f'/api/roles/{editor}', headers=headers, json={**role,
            'builtin_tools': [], 'context_window_tokens': 4096, 'params': {'max_tokens': 64}})
        assert updated.status_code == 200
        definition = {'nodes': [
            {'id': 'source', 'kind': 'role', 'title': '上游', 'role_id': writer, 'task': 'LARGE_SOURCE', 'inputs': []},
            {'id': 'target', 'kind': 'role', 'title': '下游', 'role_id': editor, 'task': '必须使用上游结果', 'inputs': ['source']},
        ], 'edges': [['source', 'target']]}
        _, rid = await launch(client, headers, cid, definition)
        done = await wait_run(client, headers, cid, rid, ['completed', 'failed'])
        assert done['status'] == 'failed'
        assert done['error_code'] == 'CONTEXT_BUDGET_EXCEEDED'
        assert done['used_decisions'] == 1


@pytest.mark.anyio
async def test_resume_downstream_after_isolated_retry_keeps_new_dependencies(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        nodes = [{'id': str(i), 'kind': 'role', 'title': str(i), 'role_id': writer, 'task': f'简短回复{i}', 'inputs': ['0'] if i else []} for i in range(2)]
        _, rid = await launch(client, headers, cid, {'nodes': nodes, 'edges': [['0', '1']]})
        done = await wait_run(client, headers, cid, rid, ['completed'])
        url = f'/api/conversations/{cid}/workflows/runs/{rid}/control'
        retry = await client.post(url, headers=headers, json={'action': 'retry', 'attempt_id': done['attempts'][0]['id'],
            'expected_revision': done['revision'], 'acknowledge_facts': True, 'rerun_downstream': False})
        assert retry.status_code == 200
        paused = await wait_run(client, headers, cid, rid, ['stopped'])
        new_source = next(a['id'] for a in paused['attempts'] if a['current'])
        responses = await asyncio.gather(*[client.post(url, headers=headers,
            json={'action': 'resume', 'expected_revision': paused['revision']}) for _ in range(2)])
        assert sorted(r.status_code for r in responses) == [200, 409]
        final = await wait_run(client, headers, cid, rid, ['completed'])
        assert len(final['attempts']) == 4 and final['used_decisions'] == 4
        last = next(a for a in final['attempts'] if a['node_id'] == '1' and a['current'])
        assert last['upstream_ids'] == [new_source]


@pytest.mark.anyio
@pytest.mark.parametrize('edges', [[['a', 'b'], ['a', 'c']], [['a', 'b'], ['b', 'c'], ['c', 'a']], [['a', 'a']], [['a', 'b']]])
async def test_general_graph_saves_but_unsupported_execution_has_no_side_effects(command_root, isolated_command_database, edges):
    """分支、环路和断路可保存，但启动前拒绝且不创建消息/预算/执行。"""
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.models import WorkflowRun, Message, Generation, AgentExecution, WorkflowBudget
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        base = f'/api/conversations/{cid}/workflows'
        nodes = [{'id': n, 'kind': 'role', 'title': n, 'role_id': writer, 'task': '任务',
                  'position': {'x': i * 180.5, 'y': i * -75.25}} for i, n in enumerate(['a', 'b', 'c'])]
        did = uuid4().hex
        saved = await client.put(f'{base}/definitions/{did}', headers=headers,
            json={'name': '通用图', 'expected_revision': 0, 'graph': {'nodes': nodes, 'edges': edges}})
        assert saved.status_code == 200
        restored = (await client.get(base, headers=headers)).json()['definitions'][0]
        assert restored['graph']['edges'] == edges
        assert restored['graph']['nodes'][1]['position'] == nodes[1]['position']
        start = await client.post(f'{base}/runs', headers=headers,
            json={'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex})
        assert start.status_code == 422 and start.json()['error']['code'] == 'WORKFLOW_EXECUTION_UNSUPPORTED'
        async with SessionLocal() as session:
            for model in [WorkflowRun, Message, Generation, AgentExecution, WorkflowBudget]:
                assert await session.scalar(select(func.count()).select_from(model).where(model.conversation_id == cid)) == 0


@pytest.mark.anyio
async def test_node_positions_and_serial_topology_are_independent(command_root, isolated_command_database):
    """布局和数组顺序不决定运行顺序，旧无坐标格式继续可用，新快照保留坐标。"""
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        definition = graph(writer, editor, False)
        definition['nodes'][0]['position'] = {'x': 800.5, 'y': -32}
        definition['nodes'][1]['position'] = {'x': 20, 'y': 300}
        definition['nodes'].reverse()
        did, rid = await launch(client, headers, cid, definition)
        done = await wait_run(client, headers, cid, rid, ['completed', 'failed'])
        assert done['status'] == 'completed'
        assert [node['id'] for node in done['graph']['nodes']] == ['write', 'edit']
        assert done['graph']['nodes'][0]['position'] == {'x': 800.5, 'y': -32}
        assert (command_root / 'group.txt').read_text() == 'second'
        definition['nodes'][1]['position'] = {'x': 10, 'y': 10}
        saved = await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
            json={'name': '新布局', 'expected_revision': 1, 'graph': definition})
        assert saved.status_code == 200
        snapshot = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()['runs'][0]
        assert snapshot['graph']['nodes'][0]['position']['x'] == 800.5


@pytest.mark.anyio
async def test_position_validation_rejects_nonfinite_or_invalid_values(command_root, isolated_command_database):
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        for position in [{'x': 'Infinity', 'y': 0}, {'x': True, 'y': 0}, {'x': None, 'y': 0}, {'x': 0, 'y': 0, 'z': 1}]:
            definition = graph(writer, editor, False)
            definition['nodes'][0]['position'] = position
            response = await client.put(f'/api/conversations/{cid}/workflows/definitions/{uuid4().hex}', headers=headers,
                json={'name': '无效坐标', 'expected_revision': 0, 'graph': definition})
            assert response.status_code == 422


@pytest.mark.anyio
async def test_nonfinite_position_returns_validation_response(command_root, isolated_command_database):
    """合法 JSON 指数溢出不能让校验错误序列化变成 500。"""
    import json
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        definition = graph(writer, editor, False)
        definition['nodes'][0]['position'] = {'x': 'OVERFLOW', 'y': 0}
        payload = json.dumps({'name': '溢出坐标', 'expected_revision': 0, 'graph': definition}).replace('"OVERFLOW"', '1e999')
        response = await client.put(f'/api/conversations/{cid}/workflows/definitions/{uuid4().hex}',
            headers={**headers, 'Content-Type': 'application/json'}, content=payload)
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'


@pytest.mark.anyio
async def test_node_color_and_type_save_without_changing_run_snapshot(command_root, isolated_command_database):
    """颜色只是已验证的显示元数据，类型修改只进入新定义。"""
    async with group_workspace(command_root) as (client, headers, cid, writer, editor, wid):
        base = f'/api/conversations/{cid}/workflows'
        did = uuid4().hex
        definition = {'nodes': [{'id': 'a', 'kind': 'approval', 'title': '节点', 'color': '#8844aa'}], 'edges': []}
        saved = await client.put(f'{base}/definitions/{did}', headers=headers, json={'name': '颜色快照', 'graph': definition, 'expected_revision': 0})
        assert saved.status_code == 200
        started = await client.post(f'{base}/runs', headers=headers, json={'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex})
        assert started.status_code == 202
        waiting = await wait_run(client, headers, cid, started.json()['id'], ['waiting'])
        for color in ['red', '#123', '#12345678', 'url(example)', '']:
            bad = {'nodes': [{**definition['nodes'][0], 'color': color}], 'edges': []}
            assert (await client.put(f'{base}/definitions/{did}', headers=headers,
                json={'name': '颜色快照', 'graph': bad, 'expected_revision': 1})).status_code == 422
        changed = {'nodes': [{'id': 'a', 'kind': 'role', 'title': '节点', 'role_id': writer,
            'task': '任务', 'color': '#336699'}], 'edges': []}
        assert (await client.put(f'{base}/definitions/{did}', headers=headers,
            json={'name': '新类型', 'graph': changed, 'expected_revision': 1})).status_code == 200
        result = (await client.get(base, headers=headers)).json()
        assert result['definitions'][0]['graph']['nodes'][0]['kind'] == 'role'
        assert result['definitions'][0]['graph']['nodes'][0]['color'] == '#336699'
        assert result['runs'][0]['graph']['nodes'][0]['kind'] == 'approval'
        assert result['runs'][0]['graph']['nodes'][0]['color'] == '#8844aa'
        assert (await client.post(f"{base}/runs/{waiting['id']}/control", headers=headers,
            json={'action': 'stop', 'expected_revision': waiting['revision']})).status_code == 200
