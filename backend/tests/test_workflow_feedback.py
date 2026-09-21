"""节点反馈的来源、幂等、处置与授权；全部使用隔离库和确定性 Provider。"""
import asyncio
from uuid import uuid4

import pytest

from test_workspace_commands import command_root, isolated_command_database
from test_orchestrator import setup_group, launch, wait_state


async def finished_source(client, headers, cid, role_id):
    """通过真实工作流创建一条已完成的来源尝试。

    Args:
        client：隔离应用客户端。
        headers：本轮 Owner 凭据。
        cid：来源群。
        role_id：本群执行角色。
    """
    rid = await launch(client, headers, cid, {
        'runtime_version': 2,
        'nodes': [{'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': role_id,
                   'task': '说明受控检查结果', 'tools': []}],
        'edges': [],
    })
    run = await wait_state(client, headers, cid, rid, lambda row: row['status'] == 'completed')
    return run, next(a for a in run['attempts'] if a['node_id'] == 'review')


def report(attempt_id, **changes):
    """构造无真实数据的契约反馈。

    Args:
        attempt_id：后台生成的来源尝试。
        changes：当前用例覆盖的参数。
    """
    return {'attempt_id': attempt_id, 'request_key': uuid4().hex, 'category': 'contract',
            'summary': '示例验收说明相互冲突', 'details': '需要架构角色裁定后再继续。',
            'blocking': True, **changes}


@pytest.mark.anyio
async def test_feedback_preserves_source_and_deduplicates_concurrent_submission(command_root, isolated_command_database):
    """重复请求只建立一条反馈，来源与业务结论不改写执行终态。"""
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        run, source = await finished_source(client, headers, cid, ids[1])
        url = f'/api/conversations/{cid}/workflows/runs/{run["id"]}/feedback'
        payload = report(source['id'])
        responses = await asyncio.gather(*(client.post(url, headers=headers, json=payload) for _ in range(2)))
        assert all(r.status_code in (200, 201) for r in responses)
        first, second = [r.json() for r in responses]
        assert first['id'] == second['id']
        assert first['attempt_id'] == source['id']
        assert first['node_id'] == 'review' and first['graph_revision'] == source['graph_revision']
        assert first['category'] == 'contract' and first['status'] == 'open'
        listing = (await client.get(url, headers=headers)).json()
        assert len(listing['items']) == 1
        conflict = await client.post(url, headers=headers, json={**payload, 'summary': '同键不同内容'})
        assert conflict.status_code == 409
        after = await wait_state(client, headers, cid, run['id'], lambda row: bool(row.get('feedback')))
        assert after['status'] == 'completed'
        assert next(a for a in after['attempts'] if a['id'] == source['id'])['status'] == 'completed'


@pytest.mark.anyio
async def test_feedback_rejects_guest_cross_run_and_stale_resolution(command_root, isolated_command_database):
    """没有归属的来源、Guest 与过期处置版本都不能改变反馈。"""
    from accounts import guest_username, TEST_PASSWORD
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        run, source = await finished_source(client, headers, cid, ids[1])
        another, _ = await finished_source(client, headers, cid, ids[1])
        url = f'/api/conversations/{cid}/workflows/runs/{run["id"]}/feedback'
        wrong = await client.post(f'/api/conversations/{cid}/workflows/runs/{another["id"]}/feedback',
                                  headers=headers, json=report(source['id']))
        assert wrong.status_code == 404
        guest = (await client.post('/api/auth/register', json={
            'username': guest_username('feedback'), 'nickname': '反馈访客', 'password': TEST_PASSWORD,
        })).json()
        guest_headers = {'Authorization': 'Bearer ' + guest['access_token']}
        assert (await client.get(url, headers=guest_headers)).status_code == 403
        assert (await client.post(url, headers=guest_headers, json=report(source['id']))).status_code == 403
        created = await client.post(url, headers=headers, json=report(source['id']))
        assert created.status_code == 201
        item = created.json()
        action_url = f'{url}/{item["id"]}/actions'
        attempted = await client.post(action_url, headers=headers, json={
            'action': 'resolve', 'expected_revision': item['revision'], 'request_key': uuid4().hex,
            'reason': '仅声明已解决，没有核对证据',
        })
        assert attempted.status_code == 422
        resolved = await client.post(action_url, headers=headers, json={
            'action': 'resolve', 'expected_revision': item['revision'], 'request_key': uuid4().hex,
            'reason': 'Owner 已核对示例契约并完成裁定', 'manual_verification': True,
        })
        assert resolved.status_code == 200
        assert resolved.json()['status'] == 'resolved'
        stale = await client.post(action_url, headers=headers, json={
            'action': 'wait', 'expected_revision': item['revision'], 'request_key': uuid4().hex,
            'reason': '过期窗口的操作',
        })
        assert stale.status_code == 409
        history = resolved.json()['history']
        assert [event['action'] for event in history] == ['created', 'resolve']
        reopened = await client.post(action_url, headers=headers, json={
            'action': 'reopen', 'expected_revision': resolved.json()['revision'], 'request_key': uuid4().hex,
            'reason': '发现新依据，需要重新验证',
        })
        assert reopened.status_code == 200
        assert reopened.json()['verification_attempt_id'] is None and reopened.json()['handler_node_ids'] == []
        repeated = await client.post(action_url, headers=headers, json={
            'action': 'reopen', 'expected_revision': reopened.json()['revision'], 'request_key': uuid4().hex,
            'reason': '不能把重新打开当成重置活跃处置的捷径',
        })
        assert repeated.status_code == 409


@pytest.mark.anyio
async def test_capability_feedback_reports_actual_grant_without_expanding_it(command_root, isolated_command_database):
    """能力缺口显示真实分配；反馈本身不能给群角色开启 Shell。"""
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        run, source = await finished_source(client, headers, cid, ids[1])
        url = f'/api/conversations/{cid}/workflows/runs/{run["id"]}/feedback'
        response = await client.post(url, headers=headers, json=report(
            source['id'], category='capability', requested_tools=['workspace_run_shell'],
            summary='本节点缺少运行验证能力',
        ))
        assert response.status_code == 201
        observed = response.json()['capability_check']
        assert observed['assigned_tools'] == []
        assert observed['unavailable_tools'] == ['workspace_run_shell']
        role = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id'] == ids[1])
        assert role['builtin_tools'] == ['workspace_read']


@pytest.mark.anyio
async def test_node_report_gates_only_dependants_until_owner_verifies(command_root, isolated_command_database, monkeypatch):
    """来源模型上报与结果原子提交；无关分支完成，受影响节点等待人工裁定。"""
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn, fake_reply_model
    from app.services import chat
    def model(prompt):
        if '[FEEDBACK_REPORT]' not in prompt:
            return fake_reply_model(prompt, delay=0)
        return ScriptedChatModel(delay=0, turns=[
            ScriptedTurn(tool_calls=[{'name': 'workflow_result', 'id': 'report-conflict', 'args': {
                'values': {'checked': True}, 'summary': '已检查，契约需裁定',
                'feedback': [{'request_key': 'contract-one', 'category': 'contract',
                              'summary': '两个验收数字冲突', 'blocking': True}],
            }}]), ScriptedTurn(text='原检查结束，等待契约裁定。'),
        ])
    monkeypatch.setattr(chat, 'fake_reply_model', model)
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        rid = await launch(client, headers, cid, {
            'runtime_version': 2, 'concurrency': 2, 'entries': ['review', 'independent'],
            'nodes': [
                {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1], 'task': '[FEEDBACK_REPORT]', 'tools': []},
                {'id': 'independent', 'kind': 'role', 'title': '独立工作', 'role_id': ids[2], 'task': '独立完成', 'tools': []},
                {'id': 'deliver', 'kind': 'role', 'title': '交付', 'role_id': ids[0], 'task': '完成交付', 'tools': []},
            ], 'edges': [['review', 'deliver']],
        })
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] in ('waiting', 'completed', 'failed'))
        assert run['status'] == 'waiting'
        assert len(run['feedback']) == 1
        item = run['feedback'][0]
        assert item['actor_execution_id'] == next(a['execution_id'] for a in run['attempts'] if a['node_id'] == 'review')
        assert next(a for a in run['attempts'] if a['node_id'] == 'independent')['status'] == 'completed'
        assert not any(a['node_id'] == 'deliver' for a in run['attempts'])
        assert next(a for a in run['activations'] if a['node_id'] == 'deliver')['status'] == 'waiting_feedback'
        response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/feedback/{item["id"]}/actions', headers=headers, json={
            'action': 'resolve', 'expected_revision': item['revision'], 'request_key': uuid4().hex,
            'reason': 'Owner 已核对原说明并裁定统一验收数字', 'manual_verification': True,
        })
        assert response.status_code == 200
        completed = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'completed')
        assert len([a for a in completed['attempts'] if a['node_id'] == 'review']) == 1
        assert next(a for a in completed['attempts'] if a['node_id'] == 'review')['result']['values'] == {'checked': True}


@pytest.mark.anyio
@pytest.mark.parametrize('inside_loop,category', [(False, 'contract'), (True, 'contract'), (False, 'implementation')])
async def test_automatic_feedback_replans_locally_and_requires_completed_verification(command_root, isolated_command_database, inside_loop, category):
    """自动协调使用两次短授权完成局部补图和复核，原审查不重跑。"""
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        assert (await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
            json={'role_id': ids[3], 'expected_revision': 0})).status_code == 200
        did = uuid4().hex
        graph = {'runtime_version': 2, 'nodes': [
            {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1],
             'task': '[FEEDBACK_IMPLEMENTATION]' if category == 'implementation' else '[FEEDBACK_REPORT]', 'tools': []},
            {'id': 'deliver', 'kind': 'role', 'title': '交付', 'role_id': ids[0], 'task': '交付完成', 'tools': []},
        ], 'edges': [['review', 'deliver']]}
        if inside_loop:
            graph['nodes'].append({'id': 'decision', 'kind': 'condition', 'title': '验收判断',
                'condition': {'sources': ['review'], 'key': 'checked', 'value': True}})
            graph['edges'] = [['review', 'decision'], ['decision', 'review'], ['decision', 'deliver']]
            graph['loops'] = [{'id': 'check', 'entry': 'review', 'decision': 'decision', 'exit': 'deliver',
                               'body': ['review', 'decision'], 'max_iterations': 2}]
        assert (await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers,
            json={'name': '自动处置', 'expected_revision': 0, 'graph': graph})).status_code == 200
        started = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers, json={
            'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex,
            'mode': 'coordinated', 'feedback_mode': 'automatic',
        })
        assert started.status_code == 202
        rid = started.json()['id']
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] in ('completed', 'failed', 'blocked'))
        assert run['status'] == 'completed', [(a['node_id'], a['status'], a['error_code']) for a in run['attempts']]
        assert len(run['feedback']) == 1
        item = run['feedback'][0]
        assert item['category'] == category
        assert item['status'] == 'resolved'
        assert len(item['graph_changes']) == 1
        assert run['graph_revision'] == 2
        for node in run['graph']['nodes']:
            assert sum(a['current'] for a in run['activations'] if a['node_id'] == node['id']) == 1
        assert all(not a['current'] for a in run['activations'] if a['status'] == 'superseded')
        assert len([a for a in run['attempts'] if a['node_id'] == 'review']) == 1
        verification = next(a for a in run['attempts'] if a['id'] == item['verification_attempt_id'])
        assert verification['status'] == 'completed'
        assert verification['result']['values']['feedback_resolved'] is True
        actions = [e['action'] for e in item['history']]
        assert actions == ['created', 'coordinate', 'assign', 'verification', 'coordinate', 'resolve']
        snapshot = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        grants = [g for g in snapshot['coordinations'] if g['run_id'] == rid]
        assert len(grants) == 2 and all(g['chain_id'] == run['chain_id'] for g in grants)
        assert all(g['feedback_ids'] == [item['id']] for g in grants)
        from app.db import SessionLocal
        from app.models import ExecutionAllocation
        async with SessionLocal() as session:
            worker = next(a for a in run['attempts'] if a['node_id'] == 'review')
            allocation = await session.get(ExecutionAllocation, worker['execution_id'])
            assert allocation.control_tools_json == ['workflow_result']
            manager = await session.get(ExecutionAllocation, grants[0]['execution_id'])
            assert 'workflow_feedback_update' in manager.control_tools_json
            assert manager.tools_json == []


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['stop', 'revoke'])
async def test_feedback_coordination_cannot_mutate_after_stop_or_revocation(command_root, isolated_command_database, monkeypatch, action):
    """停止与撤权回收自动协调执行；旧工具不能提交处置或改图。"""
    from app.agent.fake_provider import FeedbackCoordinationModel, fake_reply_model
    from app.services import chat
    from app.workflows.graph_tools import invoke
    entered, cancelled = asyncio.Event(), asyncio.Event()
    class WaitingModel(FeedbackCoordinationModel):
        async def _astream(self, messages, **kwargs):
            entered.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: WaitingModel(prompt=prompt, delay=0)
        if '本次包含节点反馈' in prompt else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        rid = await launch(client, headers, cid, {'runtime_version': 2, 'nodes': [
            {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1], 'task': '[FEEDBACK_REPORT]', 'tools': []},
            {'id': 'end', 'kind': 'join', 'title': '交付'},
        ], 'edges': [['review', 'end']]})
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting')
        item = run['feedback'][0]
        response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/feedback/{item["id"]}/actions', headers=headers, json={
            'action': 'coordinate', 'expected_revision': item['revision'], 'request_key': uuid4().hex, 'reason': '委托协调者处理本条意见',
        })
        assert response.status_code == 200
        await asyncio.wait_for(entered.wait(), 10)
        data = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        grant = data['coordinations'][0]
        from app.workflows.feedback_schemas import FeedbackToolUpdate
        schema = FeedbackToolUpdate.model_json_schema()
        assert 'manual_verification' not in schema['properties']
        assert set(schema['properties']['action']['enum']) == {'assign', 'wait', 'review', 'resolve'}
        for manual, expected_code in [(True, 'WORKFLOW_GRAPH_INVALID'), (False, 'WORKFLOW_FEEDBACK_EVIDENCE_REQUIRED')]:
            denied = await invoke(grant['execution_id'], 'workflow_feedback_update', {
                'feedback_id': item['id'], 'expected_revision': data['runs'][0]['feedback'][0]['revision'],
                'request_key': uuid4().hex, 'action': 'resolve', 'reason': '不能无依据解决或代签人工核验',
                **({'manual_verification': True} if manual else {}),
            })
            assert expected_code in denied
        if action == 'stop':
            run = data['runs'][0]
            response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
                json={'action': 'stop', 'expected_revision': run['revision']})
        else:
            response = await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers,
                json={'role_id': None, 'expected_revision': 1})
        assert response.status_code == 200
        await asyncio.wait_for(cancelled.wait(), 10)
        rejected = await invoke(grant['execution_id'], 'workflow_feedback_update', {
            'feedback_id': item['id'], 'expected_revision': item['revision'], 'request_key': uuid4().hex,
            'action': 'wait', 'reason': '旧执行尝试提交',
        })
        assert 'WORKFLOW_COORDINATION_REVOKED' in rejected
        after = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()['runs'][0]
        assert after['graph_revision'] == 1
        assert all(e['reason'] != '旧执行尝试提交' for e in after['feedback'][0]['history'])


@pytest.mark.anyio
@pytest.mark.parametrize('category', ['capability', 'unverified'])
async def test_unavailable_verification_waits_without_repeated_coordination(command_root, isolated_command_database, category):
    """无执行能力保持待验证；调度重复扫描不会自批工具或反复计费。"""
    from app.workflows.feedback import dispatch_pending
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        did = uuid4().hex
        await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers, json={
            'name': '等待真实验证能力', 'expected_revision': 0, 'graph': {'runtime_version': 2, 'nodes': [
                {'id': 'review', 'kind': 'role', 'title': '验证', 'role_id': ids[1], 'tools': [],
                 'task': '[FEEDBACK_CAPABILITY]' if category == 'capability' else '[FEEDBACK_UNVERIFIED]'},
                {'id': 'deliver', 'kind': 'join', 'title': '交付'},
            ], 'edges': [['review', 'deliver']]},
        })
        response = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers, json={
            'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex,
            'mode': 'coordinated', 'feedback_mode': 'automatic',
        })
        assert response.status_code == 202
        rid = response.json()['id']
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting' and any(f['status'] == 'waiting' for f in r['feedback']))
        assert run['feedback'][0]['category'] == category
        assert run['feedback'][0]['verification_attempt_id'] is None
        assert not any(a['node_id'] == 'deliver' for a in run['attempts'])
        await asyncio.gather(*(dispatch_pending() for _ in range(3)))
        snapshot = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        assert len(snapshot['coordinations']) == 1
        assert snapshot['runs'][0]['graph_revision'] == 1
        role = next(r for r in (await client.get('/api/roles', headers=headers)).json() if r['id'] == ids[1])
        assert role['builtin_tools'] == ['workspace_read']


@pytest.mark.anyio
async def test_feedback_waiting_survives_restart_without_replaying_source(command_root, isolated_command_database):
    """重启不自动恢复含问题的流程，Owner 继续后仍保留原意见和来源。"""
    from app.db import SessionLocal
    from app.workflows import service
    from app.workflows.engine import recover
    from app.workflows.feedback import dispatch_pending
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        rid = await launch(client, headers, cid, {'runtime_version': 2, 'nodes': [
            {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1], 'task': '[FEEDBACK_REPORT]', 'tools': []},
            {'id': 'end', 'kind': 'join', 'title': '交付'},
        ], 'edges': [['review', 'end']]})
        before = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting')
        async with service.control_lock, SessionLocal() as session:
            await recover(session)
            await session.commit()
        await dispatch_pending()
        after = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'interrupted')
        assert after['feedback'][0]['id'] == before['feedback'][0]['id']
        assert after['attempts'][0]['execution_id'] == before['attempts'][0]['execution_id']
        assert after['attempts'][0]['status'] == 'completed'
        resumed = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/control', headers=headers,
            json={'action': 'resume', 'expected_revision': after['revision']})
        assert resumed.status_code == 200
        waiting = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting')
        assert len(waiting['attempts']) == 1
        item = waiting['feedback'][0]
        response = await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/feedback/{item["id"]}/actions', headers=headers,
            json={'action': 'accept', 'expected_revision': item['revision'], 'request_key': uuid4().hex,
                  'reason': 'Owner 已审阅，作为本次演示的已知遗留继续。'})
        assert response.status_code == 200
        completed = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'completed')
        assert completed['feedback'][0]['status'] == 'accepted'


@pytest.mark.anyio
async def test_cancelled_coordination_does_not_spawn_verification_followup(command_root, isolated_command_database, monkeypatch):
    """验证完成与取消协调相撞时，保留处理结果，不复活已取消的委托。"""
    from app.agent.fake_provider import FeedbackCoordinationModel, fake_reply_model
    from app.services import chat
    from app.workflows.feedback import dispatch_pending
    assigned = asyncio.Event()
    class HoldAfterAssign(FeedbackCoordinationModel):
        async def _astream(self, messages, **kwargs):
            if self.index == 4:
                assigned.set()
                await asyncio.Event().wait()
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: HoldAfterAssign(prompt=prompt, delay=0)
        if '本次包含节点反馈' in prompt else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        rid = await launch(client, headers, cid, {'runtime_version': 2, 'nodes': [
            {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1], 'task': '[FEEDBACK_REPORT]', 'tools': []},
            {'id': 'end', 'kind': 'join', 'title': '交付'},
        ], 'edges': [['review', 'end']]})
        run = await wait_state(client, headers, cid, rid, lambda r: r['status'] == 'waiting')
        item = run['feedback'][0]
        await client.post(f'/api/conversations/{cid}/workflows/runs/{rid}/feedback/{item["id"]}/actions', headers=headers,
            json={'action': 'coordinate', 'expected_revision': item['revision'], 'request_key': uuid4().hex, 'reason': '委托处理与验证'})
        await asyncio.wait_for(assigned.wait(), 10)
        verified = await wait_state(client, headers, cid, rid, lambda r: r['feedback'][0]['status'] == 'review')
        assert verified['feedback'][0]['coordination_requested'] is True
        data = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        grant = data['coordinations'][0]
        response = await client.post(f'/api/conversations/{cid}/workflows/coordination/{grant["id"]}/cancel', headers=headers,
            json={'expected_revision': grant['revision']})
        assert response.status_code == 200
        waiting = await wait_state(client, headers, cid, rid, lambda r: r['feedback'][0]['status'] == 'waiting')
        await asyncio.gather(*(dispatch_pending() for _ in range(3)))
        after = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        assert len(after['coordinations']) == 1
        assert waiting['graph_revision'] == 2
        assert next(a for a in waiting['attempts'] if a['id'] == waiting['feedback'][0]['verification_attempt_id'])['status'] == 'completed'
        assert not any(a['node_id'] == 'end' for a in waiting['attempts'])


@pytest.mark.anyio
async def test_model_review_without_new_evidence_does_not_wake_itself(command_root, isolated_command_database, monkeypatch):
    """只有真实新证据或 Owner 委托才能再交接，模型修改状态不会形成计费循环。"""
    import json
    from langchain_core.messages import ToolMessage
    from app.agent.fake_provider import FeedbackCoordinationModel, ScriptedTurn, fake_reply_model
    from app.services import chat
    from app.workflows.feedback import dispatch_pending
    class ReviewOnly(FeedbackCoordinationModel):
        async def _astream(self, messages, **kwargs):
            if self.index == 2:
                item = json.loads(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))['feedback'][0]
                turn = ScriptedTurn(tool_calls=[{'name': 'workflow_feedback_update', 'id': 'defer-review', 'args': {
                    'feedback_id': item['id'], 'expected_revision': item['revision'], 'request_key': 'review-no-evidence',
                    'action': 'review', 'reason': '仍待核对，尚无新验证结果',
                }}])
                self.index += 1
                for chunk in self._chunks(turn): yield chunk
            else:
                async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: ReviewOnly(prompt=prompt, delay=0)
        if '本次包含节点反馈' in prompt else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        did = uuid4().hex
        await client.put(f'/api/conversations/{cid}/workflows/definitions/{did}', headers=headers, json={
            'name': '复核不自唤醒', 'expected_revision': 0, 'graph': {'runtime_version': 2, 'nodes': [
                {'id': 'review', 'kind': 'role', 'title': '审查', 'role_id': ids[1], 'task': '[FEEDBACK_REPORT]', 'tools': []},
            ], 'edges': []},
        })
        response = await client.post(f'/api/conversations/{cid}/workflows/runs', headers=headers, json={
            'definition_id': did, 'expected_revision': 1, 'request_key': uuid4().hex, 'mode': 'coordinated', 'feedback_mode': 'automatic',
        })
        assert response.status_code == 202
        rid = response.json()['id']
        await wait_state(client, headers, cid, rid, lambda r: r['feedback'] and r['feedback'][0]['status'] == 'review')
        for _ in range(300):
            data = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
            if data['coordinations'][0]['status'] == 'completed': break
            await asyncio.sleep(.03)
        assert data['coordinations'][0]['status'] == 'completed'
        await asyncio.gather(*(dispatch_pending() for _ in range(4)))
        after = (await client.get(f'/api/conversations/{cid}/workflows', headers=headers)).json()
        assert len(after['coordinations']) == 1
        assert after['runs'][0]['status'] == 'waiting'
        assert after['runs'][0]['feedback'][0]['verification_attempt_id'] is None
