"""展示元数据与业务执行分离，使用真实 API 和隔离数据库验证兼容边界。"""
from uuid import uuid4
import pytest
from test_orchestrator import setup_group, launch, wait_state, command_root, isolated_command_database
from test_graph_control import write, approval
import asyncio


@pytest.mark.anyio
async def test_display_groups_and_labels_roundtrip_without_changing_graph(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, _, _):
        did = uuid4().hex
        graph = {'runtime_version': 2, 'nodes': [approval('a'), approval('b'), approval('c')], 'edges': [['a', 'b'], ['b', 'c']],
            'presentation': {'groups': [{'id': 'prepare', 'title': '准备阶段', 'node_ids': ['a', 'b']}],
                             'edge_labels': [{'source': 'b', 'target': 'c', 'label': '准备就绪'}]}}
        result = await write(client, headers, cid, did, graph)
        assert result.status_code == 200
        assert result.json()['graph']['presentation'] == graph['presentation']
        # 旧客户端整体写入未带展示元数据时保留，不能顺手清掉人类整理的阶段。
        legacy = {key: value for key, value in graph.items() if key != 'presentation'}
        updated = await write(client, headers, cid, did, legacy, version=1)
        assert updated.status_code == 200
        assert updated.json()['graph']['presentation'] == graph['presentation']
        base = f'/api/conversations/{cid}/workflows/graphs/definition/{did}'
        edit = await client.post(base + '/edit', headers=headers, json={
            'expected_graph_revision': 2, 'mutation_key': uuid4().hex,
            'operations': [{'op': 'set_presentation', 'presentation': {'groups': [], 'edge_labels': []}}],
        })
        assert edit.status_code == 200
        assert edit.json()['graph']['presentation'] == {'groups': [], 'edge_labels': []}
        assert edit.json()['graph']['edges'] == graph['edges']
        history = (await client.get(base + '?graph_revision=1', headers=headers)).json()
        assert history['graph']['presentation'] == graph['presentation']


@pytest.mark.anyio
async def test_presentation_changes_in_running_loop_do_not_wait_for_next_iteration(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, _, _):
        graph = {'runtime_version': 2, 'nodes': [approval('start'), {'id': 'judge', 'kind': 'condition', 'title': '检查',
            'condition': {'sources': ['start'], 'key': 'approved', 'value': True}}, {'id': 'end', 'kind': 'join', 'title': '交付'}],
            'edges': [['start', 'judge'], ['judge', 'start'], ['judge', 'end']],
            'loops': [{'id': 'review', 'entry': 'start', 'decision': 'judge', 'exit': 'end', 'body': ['start', 'judge'], 'max_iterations': 3}]}
        rid = await launch(client, headers, cid, graph)
        before = await wait_state(client, headers, cid, rid, lambda run: run['status'] == 'waiting')
        metadata = {'groups': [{'id': 'review-phase', 'title': '审查与修订', 'node_ids': ['start', 'judge']}],
                    'edge_labels': [{'source': 'judge', 'target': 'start', 'label': '需要修订'}, {'source': 'judge', 'target': 'end', 'label': '通过'}]}
        response = await client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit', headers=headers, json={
            'expected_graph_revision': 1, 'mutation_key': uuid4().hex, 'operations': [{'op': 'set_presentation', 'presentation': metadata}],
        })
        assert response.status_code == 200
        assert response.json()['status'] == 'applied'
        after = await wait_state(client, headers, cid, rid, lambda run: run['graph_revision'] == 2 and run['status'] == 'waiting')
        assert after['graph']['presentation'] == metadata
        assert after['pending_graph_revision'] is None
        assert after['loop_states'] == before['loop_states']
        assert after['graph']['edges'] == before['graph']['edges']
        assert after['graph']['edge_rules'] == before['graph']['edge_rules']
        assert after['attempts'] == before['attempts']


@pytest.mark.anyio
async def test_invalid_presentation_is_atomic_and_cannot_carry_runtime_state(command_root, isolated_command_database):
    async with setup_group(command_root) as (client, headers, cid, _, _):
        did = uuid4().hex
        saved = await write(client, headers, cid, did, {'nodes': [approval('a')], 'edges': []})
        assert saved.status_code == 200
        base = f'/api/conversations/{cid}/workflows/graphs/definition/{did}'
        for presentation in [
            {'groups': [{'id': 'stage', 'title': '不存在的来源', 'node_ids': ['missing']}]},
            {'edge_labels': [{'source': 'a', 'target': 'missing', 'label': '不能造出连线'}]},
            {'groups': [], 'status': 'completed'},
        ]:
            response = await client.post(base + '/edit', headers=headers, json={
                'expected_graph_revision': 1, 'mutation_key': uuid4().hex,
                'operations': [{'op': 'set_presentation', 'presentation': presentation}],
            })
            assert response.status_code == 422
        current = (await client.get(base, headers=headers)).json()
        assert current['graph_revision'] == 1


@pytest.mark.anyio
@pytest.mark.parametrize('phase', ['plan', 'summary'])
async def test_display_only_revision_preserves_active_coordination_phase(command_root, isolated_command_database, monkeypatch, phase):
    """展示修改不能越过尚未完成的分工，或使正在执行的汇总丢失/重跑。"""
    from app.agent.fake_provider import WorkflowV2Model, fake_reply_model
    from app.services import chat
    entered, release = asyncio.Event(), asyncio.Event()
    prefix = '你是本群已任命协调者' if phase == 'plan' else '作为本群协调者'
    class Paused(WorkflowV2Model):
        async def _astream(self, messages, **kwargs):
            if self.prompt.startswith(prefix) and self.index == 0:
                entered.set()
                await release.wait()
            async for chunk in super()._astream(messages, **kwargs): yield chunk
    monkeypatch.setattr(chat, 'fake_reply_model', lambda prompt: Paused(prompt=prompt, delay=0) if prompt.startswith(prefix) else fake_reply_model(prompt, delay=0))
    async with setup_group(command_root) as (client, headers, cid, ids, _):
        await client.put(f'/api/conversations/{cid}/orchestrator', headers=headers, json={'role_id': ids[3], 'expected_revision': 0})
        rid = await launch(client, headers, cid, {'runtime_version': 2, 'nodes': [
            {'id': 'work', 'kind': 'role', 'title': '任务', 'role_id': ids[0], 'task': '完成受控任务', 'tools': []},
        ], 'edges': []}, mode='coordinated')
        await asyncio.wait_for(entered.wait(), 10)
        before = await wait_state(client, headers, cid, rid, lambda run: run['phase'] == phase)
        result = await client.post(f'/api/conversations/{cid}/workflows/graphs/run/{rid}/edit', headers=headers, json={
            'expected_graph_revision': 1, 'mutation_key': uuid4().hex, 'operations': [
                {'op': 'set_presentation', 'presentation': {'groups': [{'id': 'stage', 'title': '工作阶段', 'node_ids': ['work']}]}},
                {'op': 'update_node', 'node_id': 'work', 'changes': {'position': {'x': 40, 'y': 80}}},
            ],
        })
        try:
            assert result.status_code == 200
            after = await wait_state(client, headers, cid, rid, lambda run: run['graph_revision'] == 2)
            assert after['phase'] == phase
            assert after['used_decisions'] == before['used_decisions']
            assert after['activations'] == before['activations']
        finally:
            release.set()
        completed = await wait_state(client, headers, cid, rid, lambda run: run['status'] == 'completed')
        assert len([a for a in completed['attempts'] if a['phase'] == 'summary']) == 1
        work = next(a for a in completed['attempts'] if a['node_id'] == 'work')
        assert work['graph_revision'] == 1 and work['node_snapshot']['position'] is None
