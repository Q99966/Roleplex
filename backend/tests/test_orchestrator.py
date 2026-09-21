"""群协调模型、并行循环与精确任务授权的产品路径。"""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_workspace_commands import command_root, isolated_command_database, command_conversation


@asynccontextmanager
async def setup_group(root):
    async with command_conversation(root) as (client, headers, _, writer, wid):
        role = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id'] == writer)
        updated = await client.put(f'/api/roles/{writer}', headers=headers, json={**role, 'builtin_tools': ['workspace_read', 'workspace_write']})
        assert updated.status_code == 200
        ids = [writer]
        for label, tools in [('审查A', ['workspace_read']), ('审查B', ['workspace_read']), ('协调者', [])]:
            response = await client.post('/api/roles', headers=headers, json={'name': label, 'model_config_id': role['model_config_id'],
                'model_name': 'fake-model', 'system_prompt': '受控协作角色', 'builtin_tools': tools})
            assert response.status_code == 201
            ids.append(response.json()['id'])
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        group = (await client.post('/api/conversations', headers=headers, json={'type': 'group', 'title': '群协调验收',
            'role_ids': ids, 'workspace_binding_id': wid})).json()
        from app.db import SessionLocal
        from app.models import InstanceSettings
        async with SessionLocal() as session:
            (await session.get(InstanceSettings, 1)).decision_limit = 64
            await session.commit()
        yield client, headers, group['id'], ids, wid


def loop_graph(ids):
    writer, first, second, coord = ids
    return {'runtime_version': 2, 'concurrency': 3, 'nodes': [
        {'id': 'build', 'kind': 'role', 'title': '开发', 'role_id': writer, 'task': '[WF_BUILD]', 'tools': ['workspace_read', 'workspace_write'], 'result_keys': ['round']},
        {'id': 'a', 'kind': 'role', 'title': '功能审查', 'role_id': first, 'task': '[WF_REVIEW]', 'tools': ['workspace_read'], 'inputs': ['build'], 'result_keys': ['approved']},
        {'id': 'b', 'kind': 'role', 'title': '代码审查', 'role_id': second, 'task': '[WF_REVIEW]', 'tools': ['workspace_read'], 'inputs': ['build'], 'result_keys': ['approved']},
        {'id': 'join', 'kind': 'join', 'title': '汇合', 'inputs': ['a', 'b']},
        {'id': 'judge', 'kind': 'judge', 'title': '协调判断', 'role_id': coord, 'task': '[WF_JUDGE]', 'tools': [], 'inputs': ['join'],
         'condition': {'sources': ['$self'], 'key': 'approved', 'value': True}},
        {'id': 'end', 'kind': 'join', 'title': '结束', 'inputs': ['judge']},
    ], 'edges': [['build','a'],['build','b'],['a','join'],['b','join'],['join','judge'],['judge','build'],['judge','end']],
        'loops': [{'id': 'revision', 'entry': 'build', 'decision': 'judge', 'exit': 'end',
                   'body': ['build','a','b','join','judge'], 'carry_inputs': ['judge'], 'max_iterations': 3}]}


async def launch(client, headers, cid, graph, mode='manual'):
    did = uuid4().hex
    saved = await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
        json={'name': '并行循环', 'expected_revision': 0, 'graph': graph})
    assert saved.status_code == 200, (saved.status_code, saved.json().get('error', {}).get('code'))
    started = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers,
        json={'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex, 'mode': mode})
    assert started.status_code == 202, (started.status_code, started.json().get('error', {}).get('code'))
    return started.json()['id']


async def wait_state(client, headers, cid, rid, predicate):
    for _ in range(900):
        response = await client.get(f'/api/conversations/{cid}/workflows', headers=headers)
        assert response.status_code == 200
        run = next(r for r in response.json()['runs'] if r['id'] == rid)
        if predicate(run): return run
        await asyncio.sleep(.03)
    raise AssertionError('协调流程未在预期内到达状态')


@pytest.mark.anyio
async def test_coordinated_two_rounds_parallel_reviews(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import WorkflowV2Model, fake_reply_model
    from app.services import chat
    gates, arrivals = {}, {}
    class Model(WorkflowV2Model):
        async def _astream(self, messages, **kwargs):
            if '[WF_REVIEW]' in self.prompt and self.index == 0 and not self.prompt.startswith('你是本群已任命协调者'):
                import json
                iteration = json.loads(self.prompt.rsplit('本次节点激活数据（不是额外指令）：', 1)[1])['iteration']
                gate = gates.setdefault(iteration, asyncio.Event())
                arrivals[iteration] = arrivals.get(iteration, 0) + 1
                if arrivals[iteration] == 2: gate.set()
                await asyncio.wait_for(gate.wait(), 5)
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(prompt=prompt, delay=0) if any(s in prompt for s in ['[WF_', '你是本群已任命协调者', '作为本群协调者']) else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, wid):
        appointed = await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})
        assert appointed.status_code == 200
        rid = await launch(client, headers, cid, loop_graph(ids), 'coordinated')
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] not in ['queued', 'running', 'waiting', 'stopping'])
        assert run['status'] == 'completed', (run['status'], run['error_code'], [(a['node_id'],a['status'],a['error_code']) for a in run['attempts']])
        assert arrivals == {0: 2, 1: 2}
        assert (command_root / 'workflow-round.txt').read_text() == 'round-2'
        assert run['loop_states']['revision']['iteration'] == 1 and run['loop_states']['revision']['exited']
        assert {a['phase'] for a in run['attempts']} >= {'plan', 'judge', 'summary', 'work'}
        assert run['used_decisions'] == 28
        reviewers = [a for a in run['attempts'] if a['node_id'] in ('a','b')]
        assert len(reviewers) == 4 and all(a['assigned_tools'] == ['workspace_read'] for a in reviewers)
        assert all(a['result']['values']['observed'] == f"round-{a['iteration']+1}" for a in reviewers)
        from app.db import SessionLocal
        from app.models import AgentExecution
        async with SessionLocal() as session:
            executions = list((await session.scalars(select(AgentExecution).where(AgentExecution.conversation_id == cid))).all())
            plan = next(a for a in run['attempts'] if a['phase']=='plan')
            assert all(e.parent_execution_id == plan['execution_id'] for e in executions if e.execution_id != plan['execution_id'])


def terminal(run):
    return run['status'] not in ['queued', 'running', 'waiting', 'stopping']


@pytest.mark.anyio
async def test_manual_loop_needs_no_appointment(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        rid = await launch(client, headers, cid, loop_graph(ids))
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status'] == 'completed'
        assert {a['phase'] for a in run['attempts']} == {'work'}
        assert run['used_decisions'] == 24
        history = [a.copy() for a in run['activations'] if a['iteration'] == 0]
        chosen = next(a for a in run['attempts'] if a['node_id']=='a' and a['iteration']==1)
        response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
            json={'action':'retry', 'attempt_id':chosen['id'], 'expected_revision':run['revision'],
                  'acknowledge_facts':True, 'rerun_downstream':False})
        assert response.status_code == 200
        retried = await wait_state(client, headers, cid, rid, terminal)
        assert retried['status'] == 'stopped'
        assert [a for a in retried['activations'] if a['iteration']==0 and a['loop_id']] == [a for a in history if a['loop_id']]
        assert len([a for a in retried['attempts'] if a['node_id']=='b']) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['stop', 'revoke'])
async def test_stop_all_parallel_executions(command_root, isolated_command_database, monkeypatch, action):
    from app.agent.fake_provider import WorkflowV2Model, fake_reply_model
    from app.services import chat
    both = asyncio.Event()
    entered, cancelled = [], []
    class Model(WorkflowV2Model):
        async def _astream(self, messages, **kwargs):
            if '[WF_REVIEW]' in self.prompt and self.index == 0 and not self.prompt.startswith('你是本群已任命协调者'):
                entered.append(True)
                if len(entered) == 2: both.set()
                try: await asyncio.Event().wait()
                finally: cancelled.append(True)
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(prompt=prompt, delay=0) if any(s in prompt for s in ['[WF_', '你是本群已任命协调者', '作为本群协调者']) else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        response = await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        assert response.status_code == 200
        rid = await launch(client, headers, cid, loop_graph(ids), 'coordinated')
        await asyncio.wait_for(both.wait(), 10)
        run = await wait_state(client, headers, cid, rid, lambda r: sum(a['status'] == 'running' for a in r['attempts']) == 2)
        if action == 'stop':
            response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
                json={'action': 'stop', 'expected_revision': run['revision']})
        else:
            response = await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
                json={'role_id': None, 'expected_revision': 1})
        assert response.status_code == 200
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status'] == ('stopped' if action == 'stop' else 'blocked')
        assert len(cancelled) == 2
        assert all(a['node_id'] not in ['judge', '__summary'] for a in run['attempts'])
        assert (command_root / 'workflow-round.txt').read_text() == 'round-1'


@pytest.mark.anyio
async def test_independent_approval_retry_preserves_branch(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        graph = {'runtime_version': 2, 'entries': ['a', 'b'], 'nodes': [
            {'id': n, 'kind': 'approval' if n != 'join' else 'join', 'title': n} for n in ['a','b','join']],
            'edges': [['a','join'],['b','join']]}
        rid = await launch(client, headers, cid, graph)
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting')
        async def control(action, attempt=None, **kw):
            current = await wait_state(client, headers, cid, rid, lambda r: True)
            r = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
                json={'action': action, 'expected_revision': current['revision'], 'attempt_id': attempt, **kw})
            assert r.status_code == 200, r.text
        a = next(a for a in run['attempts'] if a['node_id']=='a')
        b = next(a for a in run['attempts'] if a['node_id']=='b')
        await control('confirm', a['id'])
        await control('confirm', b['id'])
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status'] == 'completed'
        await control('retry', a['id'], acknowledge_facts=True, rerun_downstream=True)
        run = await wait_state(client, headers, cid, rid, lambda r: r['status']=='waiting')
        assert next(x for x in run['activations'] if x['node_id']=='b')['attempt_id'] == b['id']
        fresh = next(x for x in run['attempts'] if x['node_id']=='a' and x['selected_in_activation'])
        assert fresh['id'] != a['id']
        await control('confirm', fresh['id'])
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status']=='completed'
        assert len([x for x in run['attempts'] if x['node_id']=='b']) == 1


@pytest.mark.parametrize('value', [None, 'true', 1, {}, []])
def test_unknown_condition_never_selects_loop(value):
    from fastapi import HTTPException
    from app.workflows.graph import evaluate
    with pytest.raises(HTTPException):
        evaluate({'sources': ['a'], 'key': 'approved', 'value': True, 'operator': 'eq', 'aggregate': 'all'}, {'a': {'approved': value}})


@pytest.mark.anyio
async def test_appointment_scope_revision_and_legacy_mode(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        seed = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id']==ids[0])
        foreign = (await client.post('/api/roles', headers=headers, json={'model_config_id':seed['model_config_id'], 'name':'外部角色', 'model_name':'fake-model', 'system_prompt':'外部角色'})).json()['id']
        from accounts import guest_username, TEST_PASSWORD
        guest = (await client.post('/api/auth/register', json={'username':guest_username('coord'), 'nickname':'Guest', 'password':TEST_PASSWORD})).json()
        url = f'/api/conversations/{cid}/orchestrator'
        assert (await client.put(url, headers={'Authorization':f"Bearer {guest['access_token']}"}, json={'role_id':ids[3], 'expected_revision':0})).status_code == 403
        assert (await client.put(url, headers=headers, json={'role_id': foreign, 'expected_revision':0})).status_code == 422
        assert (await client.put(url, headers=headers, json={'role_id': ids[3], 'expected_revision':0})).status_code == 200
        assert (await client.put(url, headers=headers, json={'role_id': None, 'expected_revision':0})).status_code == 409
        assert (await client.put(url, headers=headers, json={'role_id': None, 'expected_revision':1})).status_code == 200
        did = uuid4().hex
        await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
            json={'name':'未任命', 'expected_revision':0, 'graph':loop_graph(ids)})
        response = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers,
            json={'definition_id':did, 'expected_revision':1, 'request_key':uuid4().hex, 'mode':'coordinated'})
        assert response.status_code == 422


@pytest.mark.anyio
async def test_conditional_skip_join_and_restart_waiting(command_root, isolated_command_database):
    from app.workflows.engine import recover
    from app.db import SessionLocal
    async with setup_group(command_root) as (client, headers, cid, _, _):
        graph = {'runtime_version':2, 'nodes': [
            {'id':'approval', 'kind':'approval', 'title':'人工决定'},
            {'id':'condition', 'kind':'condition', 'title':'判断', 'condition':{'sources':['approval'], 'key':'approved', 'value':True}},
            *[{'id':n, 'kind':'join', 'title':n} for n in ['yes','no','end']]],
            'edges':[['approval','condition'],['condition','yes'],['condition','no'],['yes','end'],['no','end']],
            'edge_rules':[{'source':'condition','target':'yes','when':'true'},{'source':'condition','target':'no','when':'false'}]}
        rid = await launch(client, headers, cid, graph)
        run = await wait_state(client, headers, cid, rid, lambda r:r['status']=='waiting')
        async with SessionLocal() as session:
            await recover(session); await session.commit()
        run = await wait_state(client, headers, cid, rid, lambda r:r['status']=='waiting')
        a = next(a for a in run['attempts'] if a['node_id']=='approval')
        response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
            json={'action':'confirm', 'expected_revision':run['revision'], 'attempt_id':a['id'], 'decision':False})
        assert response.status_code == 200
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status']=='completed'
        statuses = {a['node_id']:a['status'] for a in run['activations']}
        assert statuses == {'approval':'completed','condition':'completed','yes':'skipped','no':'completed','end':'completed'}
        assert run['used_decisions']==0


@pytest.mark.anyio
async def test_invalid_coordinator_judgment_closes_loop(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import WorkflowV2Model, ScriptedChatModel, ScriptedTurn, fake_reply_model
    from app.services import chat
    def factory(prompt):
        if '[WF_JUDGE]' in prompt and not prompt.startswith('你是本群已任命协调者'):
            return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name':'workflow_result', 'args':{'values':{'approved':'true'}}, 'id':'invalid-value'}]), ScriptedTurn(text='判断完成')], delay=0)
        return WorkflowV2Model(prompt=prompt, delay=0) if any(s in prompt for s in ['[WF_', '你是本群已任命协调者', '作为本群协调者']) else fake_reply_model(prompt, delay=0)
    monkeypatch.setattr(chat, 'fake_reply_model', factory)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id':ids[3], 'expected_revision':0})
        rid = await launch(client, headers, cid, loop_graph(ids), 'coordinated')
        run = await wait_state(client, headers, cid, rid, terminal)
        assert run['status']=='blocked' and run['error_code']=='ORCHESTRATOR_EXECUTION_FAILED'
        assert run['loop_states']['revision']['iteration']==0
        assert all(a['node_id']!='__summary' for a in run['attempts'])
        assert next(a for a in run['attempts'] if a['node_id']=='judge')['error_code']=='WORKFLOW_RESULT_REQUIRED'


@pytest.mark.anyio
async def test_same_role_parallel_allocations_and_execution_recheck(command_root, isolated_command_database, monkeypatch):
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.services import chat
    from app.workflows.allocations import allowed
    from app.db import SessionLocal
    bound, entered, released = {}, asyncio.Event(), asyncio.Event()
    class Model(ScriptedChatModel):
        tag: str
        def bind_tools(self, tools, **kwargs):
            bound[self.tag] = {t.name if hasattr(t, 'name') else t.get('function', t)['name'] for t in tools}
            return self
        async def _astream(self, messages, **kwargs):
            bound.setdefault(self.tag,set())
            if len(bound)==2: entered.set()
            await released.wait()
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Model(tag='read' if 'allocation-read' in prompt else 'none', turns=[ScriptedTurn(text='已完成')], delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id':ids[0], 'expected_revision':0})
        graph={'runtime_version':2, 'entries':['read','none'], 'nodes':[
            {'id':n, 'title':n, 'kind':'role', 'role_id':ids[0], 'task':f'allocation-{n}', 'tools':['workspace_read'] if n=='read' else []} for n in ['read','none']], 'edges':[]}
        rid = await launch(client, headers, cid, graph)
        await asyncio.wait_for(entered.wait(), 10)
        assert bound == {'read':{'workspace_read'}, 'none':set()}
        run = await wait_state(client, headers, cid, rid, lambda r:len(r['attempts'])==2)
        read = next(a for a in run['attempts'] if a['node_id']=='read')
        none = next(a for a in run['attempts'] if a['node_id']=='none')
        role = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id']==ids[0])
        assert role['builtin_tools'] == ['workspace_read','workspace_write']
        await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role, 'builtin_tools':['workspace_read']})
        async with SessionLocal() as session:
            assert await allowed(session, read['execution_id'], 'workspace_read')
            assert not await allowed(session, read['execution_id'], 'workspace_write')
            assert not await allowed(session, none['execution_id'], 'workspace_read')
        await client.put(f'/api/roles/{ids[0]}', headers=headers, json={**role, 'builtin_tools':[]})
        async with SessionLocal() as session:
            assert not await allowed(session, read['execution_id'], 'workspace_read')
            assert await allowed(session, none['execution_id'])
        released.set()
        run = await wait_state(client, headers, cid, rid, terminal)
        assert next(a for a in run['attempts'] if a['node_id']=='none')['status']=='completed'


@pytest.mark.anyio
async def test_role_duplicate_returns_safe_conflict(command_root, isolated_command_database, caplog):
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        role = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id']==ids[0])
        secret_prompt = 'fixture-private-role-prompt'
        response = await client.post('/api/roles', headers=headers, json={**role, 'system_prompt':secret_prompt})
        assert response.status_code == 409
        assert secret_prompt not in response.text and secret_prompt not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize('case', ['loop_limit', 'budget_limit', 'unlimited'])
async def test_loop_and_shared_budget_limits(command_root, isolated_command_database, case):
    from app.db import SessionLocal
    from app.models import InstanceSettings
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        graph=loop_graph(ids)
        if case=='loop_limit': graph['loops'][0]['max_iterations']=1
        else:
            async with SessionLocal() as session:
                (await session.get(InstanceSettings,1)).decision_limit=1 if case=='budget_limit' else None
                await session.commit()
        rid=await launch(client,headers,cid,graph)
        run=await wait_state(client,headers,cid,rid,terminal)
        if case=='loop_limit':
            assert run['status']=='blocked' and run['error_code']=='WORKFLOW_LOOP_LIMIT'
            assert run['loop_states']['revision']['iteration']==0
        elif case=='budget_limit':
            assert run['used_decisions']==1 and run['status']=='failed'
            assert all(a['iteration']==0 for a in run['attempts'])
        else:
            assert run['decision_limit'] is None and run['used_decisions']==24
            assert run['status']=='completed'


@pytest.mark.anyio
async def test_completed_plan_recovery_and_disable_latch(command_root, isolated_command_database):
    from app.db import SessionLocal
    from app.models import WorkflowRun, WorkflowActivation, WorkflowAttempt
    from app.workflows import service
    from app.workflows.engine import recover
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id':ids[3], 'expected_revision':0})
        graph=loop_graph(ids)
        graph['nodes'].insert(0,{'id':'gate','kind':'approval','title':'确认'})
        graph['edges'].insert(0,['gate','build'])
        rid=await launch(client,headers,cid,graph,'coordinated')
        run=await wait_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        # 模拟进程在规划 execution 已提交、调度器还未采纳结果时退出。
        async with service.control_lock:
            async with SessionLocal() as session:
                stored=await session.get(WorkflowRun,rid)
                plan=await session.scalar(select(WorkflowActivation).where(WorkflowActivation.run_id==rid, WorkflowActivation.node_id=='__plan'))
                plan.status='active'
                (await session.get(WorkflowAttempt,plan.selected_attempt_id)).status='running'
                stored.status='running'
                stored.state_json={**stored.state_json,'phase':'plan','assignments':{}}
                await session.flush()
                await recover(session); await session.commit()
        run=await wait_state(client,headers,cid,rid,terminal)
        assert run['status']=='interrupted' and run['phase']=='work'
        response=await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control',headers=headers,
            json={'action':'resume','expected_revision':run['revision']})
        assert response.status_code==200
        run=await wait_state(client,headers,cid,rid,lambda r:r['status']=='waiting')
        assert run['used_decisions']==2 and len([a for a in run['attempts'] if a['phase']=='plan'])==1
        role=next(r for r in (await client.get('/api/roles',headers=headers)).json() if r['id']==ids[3])
        for active in [False,True]:
            changed=await client.put(f'/api/roles/{ids[3]}',headers=headers,json={**role,'active':active})
            assert changed.status_code==200
        run=await wait_state(client,headers,cid,rid,terminal)
        assert run['status']=='blocked'
        assert all(a['node_id']!='build' for a in run['attempts'])
