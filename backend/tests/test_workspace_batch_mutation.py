"""批量写入/编辑：全批预检、逐项提交及不自动重放。"""
import asyncio
import hashlib
import json

import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command
from test_workspace_edit import wait_reply


@pytest.mark.anyio
async def test_batch_create_and_edit_keep_per_file_results(command_root):
    """Args:
        command_root：隔离的受控文件目录。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """复用受控文件服务。"""
        return service
    lock = asyncio.Lock()
    items = validate_items('write', [{'path': 'a.txt', 'content': '甲 old'}, {'path': 'b.txt', 'content': '乙 old'}])
    receipt = BatchMutationReceipt('write', items)
    result = json.loads(await mutate_many('write', items, authorize=authorize, lock=lock, receipt=receipt))
    assert result['status'] == 'success'
    assert [node['result']['created'] for node in result['items']] == [True, True]
    edits = validate_items('edit', [{'path': node['path'], 'old_text': 'old', 'new_text': 'new',
        'expected_sha256': node['result']['sha256']} for node in result['items']])
    edited = json.loads(await mutate_many('edit', edits, authorize=authorize, lock=lock, receipt=BatchMutationReceipt('edit', edits)))
    assert edited['status'] == 'success'
    assert (command_root / 'a.txt').read_text() == '甲 new'
    assert (command_root / 'b.txt').read_text() == '乙 new'
    repeated = json.loads(await mutate_many('edit', edits, authorize=authorize, lock=lock, receipt=BatchMutationReceipt('edit', edits)))
    assert repeated['status'] == 'rejected'
    assert all(node['result'] is None for node in repeated['items'])


@pytest.mark.anyio
async def test_batch_precheck_failure_never_writes_earlier_file(command_root):
    """Args:
        command_root：验证后项失败不会提前写入首项。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """返回当前服务。"""
        return service
    for bad_path in ['.env', '../outside', 'a.txt']:
        items = validate_items('write', [{'path': 'a.txt', 'content': 'first'}, {'path': bad_path, 'content': 'second'}])
        value = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', items)))
        assert value['status'] == 'rejected'
        assert not (command_root / 'a.txt').exists()


@pytest.mark.anyio
async def test_batch_execution_failure_preserves_success_and_skips_tail(command_root, monkeypatch):
    """Args:
        command_root：受控目录。
        monkeypatch：第二项在预检后发生版本冲突。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    service = WorkspaceFileService(root=command_root, execution_id='test')
    original = service.write
    async def fail_second(path, *args, **kwargs):
        """Args:
            path：当前目标。
            args：原写入参数。
            kwargs：版本及采集参数。
        """
        if path == 'b.txt':
            raise WorkspaceFileError('WORKSPACE_FILE_REVISION_CONFLICT')
        return await original(path, *args, **kwargs)
    monkeypatch.setattr(service, 'write', fail_second)
    async def authorize():
        """本轮授权服务。"""
        return service
    items = validate_items('write', [{'path': f'{name}.txt', 'content': name} for name in 'abc'])
    receipt = BatchMutationReceipt('write', items)
    value = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=receipt))
    assert value['status'] == 'partial'
    assert [node['status'] for node in value['items']] == ['success', 'failed', 'not_executed']
    assert receipt.value['items'][1]['write']['availability'] == 'not_executed'
    assert (command_root / 'a.txt').read_text() == 'a'
    assert not (command_root / 'c.txt').exists()
    first_mtime = (command_root / 'a.txt').stat().st_mtime_ns
    monkeypatch.setattr(service, 'write', original)
    retry = items[1:]
    retried = json.loads(await mutate_many('write', retry, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', retry)))
    assert retried['status'] == 'success' and (command_root / 'a.txt').stat().st_mtime_ns == first_mtime


@pytest.mark.anyio
async def test_batch_unknown_write_never_claims_unchanged(command_root, monkeypatch):
    """Args:
        command_root：部分 OS 写入固件。
        monkeypatch：模拟创建中途失败，无法确认完整落地。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    service = WorkspaceFileService(root=command_root, execution_id='test')
    original = service.write
    async def partial(path, *args, **kwargs):
        """Args:
            path：目标。
            args：原参数。
            kwargs：原版本/采集参数。
        """
        if path == 'b.txt':
            (command_root / path).write_text('partial')
            raise OSError('controlled-write-failure')
        return await original(path, *args, **kwargs)
    monkeypatch.setattr(service, 'write', partial)
    async def authorize():
        """本轮服务。"""
        return service
    items = validate_items('write', [{'path': f'{name}.txt', 'content': name} for name in 'abc'])
    value = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', items)))
    assert value['status'] == 'result_unconfirmed'
    assert [item['applied'] for item in value['items']] == [True, None, False]
    assert (command_root / 'b.txt').read_text() == 'partial' and not (command_root / 'c.txt').exists()


@pytest.mark.anyio
async def test_batch_cancel_after_commit_keeps_receipt(command_root, monkeypatch):
    """Args:
        command_root：隔离目录。
        monkeypatch：在第一项已提交后的差异等待点取消。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    from app.agent.write_capture import WriteReceipt
    entered = asyncio.Event()
    async def wait_diff(self, output):
        """Args:
            output：已经提交的普通结果，不重新写入。
        """
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(WriteReceipt, 'finish', wait_diff)
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """本轮服务。"""
        return service
    items = validate_items('write', [{'path': f'{name}.txt', 'content': name} for name in 'ab'])
    receipt = BatchMutationReceipt('write', items)
    task = asyncio.create_task(mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=receipt))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receipt.value['status'] == 'cancelled'
    assert [node['status'] for node in receipt.value['items']] == ['success', 'not_executed']
    assert receipt.value['items'][0]['write']['reason'] == 'cancelled'
    assert (command_root / 'a.txt').read_text() == 'a' and not (command_root / 'b.txt').exists()


@pytest.mark.anyio
async def test_batch_diff_failure_does_not_stop_writes(command_root, monkeypatch):
    """Args:
        command_root：受控目录。
        monkeypatch：注入差异基础设施失败，不作为文件操作失败。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    from app.agent.write_capture import WriteReceipt
    async def fail(self, output):
        """Args:
            output：已确认写入结果。
        """
        raise RuntimeError('controlled-diff-failure')
    monkeypatch.setattr(WriteReceipt, 'finish', fail)
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """本轮服务。"""
        return service
    items = validate_items('write', [{'path': f'{name}.txt', 'content': name} for name in 'ab'])
    receipt = BatchMutationReceipt('write', items)
    value = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=receipt))
    assert value['status'] == 'success'
    assert all(node['write']['reason'] == 'capture_failed' for node in receipt.value['items'])
    assert (command_root / 'b.txt').read_text() == 'b'


@pytest.mark.anyio
async def test_batch_rechecks_authorization_and_hardlink_alias(command_root):
    """Args:
        command_root：只包含受控硬链接的目录。
    """
    import os
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    service = WorkspaceFileService(root=command_root, execution_id='test')
    (command_root / 'a.txt').write_text('old')
    os.link(command_root / 'a.txt', command_root / 'b.txt')
    async def authorize():
        """受控服务。"""
        return service
    items = validate_items('write', [{'path': f'{name}.txt', 'content': 'new', 'expected_sha256': hashlib.sha256(b'old').hexdigest()} for name in 'ab'])
    value = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', items)))
    assert value['error_code'] == 'WORKSPACE_BATCH_TARGET_CONFLICT'
    assert (command_root / 'a.txt').read_text() == 'old'
    calls = 0
    async def revoked():
        """两项预检/第一项提交后撤销，第二项不得开始写入。"""
        nonlocal calls
        calls += 1
        return service if calls <= 3 else None
    creates = validate_items('write', [{'path': f'{name}.txt', 'content': 'new'} for name in 'cd'])
    value = json.loads(await mutate_many('write', creates, authorize=revoked, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', creates)))
    assert value['status'] == 'partial' and value['items'][1]['applied'] is False
    assert (command_root / 'c.txt').exists() and not (command_root / 'd.txt').exists()


@pytest.mark.parametrize('operation,items,code', [
    ('write', [], 'WORKSPACE_BATCH_ARGUMENT_INVALID'), ('write', [{'path': 'a', 'content': 'x'}] * 9, 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ('write', [{'path': 'a', 'content': '中' * 90000}], 'WORKSPACE_BATCH_INPUT_TOO_LARGE'),
    ('edit', [{'path': 'a', 'old_text': 'a', 'new_text': 'b'}], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ('write', [{'path': 'a', 'content': 'x', 'operation': 'edit'}], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
], ids=['empty', 'count', 'utf8-budget', 'missing-hash', 'mixed-operations'])
def test_batch_invalid_input_rejected_before_io(operation, items, code):
    """Args:
        operation：固定工具类型。
        items：无效受控请求。
        code：预期固定错误。
    """
    from app.workspaces.batch_mutation import validate_items
    from app.workspaces.files import WorkspaceFileError
    with pytest.raises(WorkspaceFileError) as error:
        validate_items(operation, items)
    assert error.value.code == code


@pytest.mark.anyio
async def test_agent_batch_mutation_has_private_diffs_and_safe_summary(command_root, isolated_command_database):
    """Args:
        command_root：本轮目录。
        isolated_command_database：迁移建立的隔离数据库。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall, EventLog
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_write', 'workspace_edit']})
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, '[BATCH_MUTATION_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert [call['status'] for call in calls] == ['success', 'success']
        for call in calls:
            detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
            assert detail['output'] is None and 'write' not in detail
            assert all(node['applied'] and node['write']['availability'] == 'recorded' for node in detail['write_batch']['items'])
            assert 'alpha old' not in detail['input']['text'] and 'old_text' not in json.loads(detail['input']['text'])['items'][0]
        assert (command_root / 'src/nested/batch-a.txt').read_text() == 'alpha new'
        async with SessionLocal() as session:
            records = (await session.scalars(select(ToolCall).where(ToolCall.conversation_id == cid))).all()
            assert all(json.loads(record.args_summary) == {'item_count': 2} for record in records)
            for event in (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid))).all():
                assert 'alpha old' not in str(event.payload_json) and 'src/nested/batch-a.txt' not in str(event.payload_json)


def test_batch_mutation_schema_preserves_legacy_and_rejects_mixing():
    """原写/编辑参数保持可用，批次字段不接受顶层默认值或 null 混入。"""
    from pydantic import ValidationError
    from app.workspaces.tools import WorkspaceWriteInput, WorkspaceEditInput
    assert WorkspaceWriteInput(path='a', content='').content == ''
    assert WorkspaceEditInput(path='a', old_text='x', new_text='', expected_sha256='0' * 64).new_text == ''
    for schema, item in [(WorkspaceWriteInput, {'path': 'a', 'content': 'x'}),
                         (WorkspaceEditInput, {'path': 'a', 'old_text': 'x', 'new_text': 'y', 'expected_sha256': '0' * 64})]:
        assert len(schema(items=[item]).items) == 1
        for invalid in [{'items': [item], 'path': None}, {'items': [item], 'expected_sha256': None},
                        {'items': None}, {}, {'items': [item], 'workspace_id': 1}]:
            with pytest.raises(ValidationError):
                schema.model_validate(invalid)


@pytest.mark.anyio
async def test_batch_aggregate_diff_budget_keeps_applied_metadata(command_root, monkeypatch):
    """Args:
        command_root：受控创建目标。
        monkeypatch：只为聚合器提供合成差异，不作为真实文件差异的证明。
    """
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    from app.workspaces.files import WorkspaceFileService
    from app.agent.write_capture import WriteReceipt
    from app.schemas import BatchMutationDetailView
    async def synthetic(self, output):
        """Args:
            output：已应用结果，与本单元测试的合成差异分开。
        """
        self.value.update(availability='recorded', reason=None)
        self.value['files'][0].update(added=500, removed=0, hunks=[{'old_start': 1, 'old_lines': 0,
            'new_start': 1, 'new_lines': 500, 'lines': [{'kind': 'insert', 'old_line': None, 'new_line': index + 1,
                'text': 'x' * 40, 'ending': 'lf'} for index in range(500)]}])
    monkeypatch.setattr(WriteReceipt, 'finish', synthetic)
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """本轮服务。"""
        return service
    items = validate_items('write', [{'path': f'{index}.txt', 'content': 'new'} for index in range(8)])
    receipt = BatchMutationReceipt('write', items)
    result = await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=receipt)
    value = BatchMutationDetailView.model_validate(receipt.value).model_dump()
    assert len(result.encode()) <= 65536 and len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) <= 65536
    assert all(node['applied'] and node['write']['availability'] == 'partial' for node in value['items'])
    assert sum(len(hunk['lines']) for node in value['items'] for file in node['write']['files'] for hunk in file['hunks']) <= 1000
    assert all(node['write']['files'][0]['added'] == 500 for node in value['items'])


@pytest.mark.anyio
async def test_batch_lock_timeout_and_capacity_do_not_start_writes(command_root, monkeypatch):
    """Args:
        command_root：隔离目标。
        monkeypatch：缩短测试等待，不修改产品超时配置。
    """
    from app.workspaces import batch_mutation as batch
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.write_admission import current_pool
    monkeypatch.setattr(current_pool(), 'timeout', .02)
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """本轮服务。"""
        return service
    items = batch.validate_items('write', [{'path': 'a.txt', 'content': 'x'}])
    lock = asyncio.Lock()
    await lock.acquire()
    try:
        result = json.loads(await batch.mutate_many('write', items, authorize=authorize, lock=lock, receipt=batch.BatchMutationReceipt('write', items)))
        assert result['error_code'] == 'WORKSPACE_BATCH_BUSY' and not (command_root / 'a.txt').exists()
    finally:
        lock.release()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def blocked():
        """停在两批预检授权，第三批排队至本轮短预算到期。"""
        nonlocal calls
        calls += 1
        if calls >= 2:
            entered.set()
        await release.wait()
        return service
    tasks = [asyncio.create_task(batch.mutate_many('write', items, authorize=blocked, lock=asyncio.Lock(), receipt=batch.BatchMutationReceipt('write', items))) for _ in range(2)]
    await entered.wait()
    result = json.loads(await batch.mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=batch.BatchMutationReceipt('write', items)))
    assert result['error_code'] == 'WORKSPACE_BATCH_BUSY'
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not current_pool().active and not (command_root / 'a.txt').exists()


@pytest.mark.anyio
async def test_batch_service_gate_does_not_stop_running_service(command_root, isolated_command_database):
    """Args:
        command_root：隔离目录。
        isolated_command_database：测试世界数据库，服务归属可核查。
    """
    import sys
    if sys.platform != 'linux':
        pytest.skip('托管服务仅在 Linux 开放')
    from test_runtime_service import enable_service, launch
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        running = await launch(client, headers, cid)
        try:
            role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
            await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_write', 'workspace_edit']})
            await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
            sent = await send_command(client, headers, cid, '[BATCH_MUTATION_FAKE]')
            message = await wait_reply(client, headers, cid, sent['message']['id'])
            calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
            assert all(part['status'] == 'rejected' and part['error_code'] == 'WORKSPACE_SERVICE_ACTIVE' for part in calls)
            assert not (command_root / 'src/nested/batch-a.txt').exists()
            rows = (await client.get(f'/api/conversations/{cid}/processes', headers=headers)).json()['items']
            assert next(row for row in rows if row['id'] == running['id'])['state'] == 'ready'
        finally:
            stopped = await client.post(f"/api/conversations/{cid}/processes/{running['id']}/stop", headers=headers)
            assert stopped.status_code == 200 and stopped.json()['state'] == 'stopped'
        sent = await send_command(client, headers, cid, '[BATCH_MUTATION_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        assert all(part['status'] == 'success' for part in message['parts_json'] if part['type'] == 'tool_call')


@pytest.mark.anyio
async def test_batch_encryption_failure_and_crash_gap_keep_truth(monkeypatch):
    """Args:
        monkeypatch：控制差异加密失败，不输出任何私有原文。
    """
    from datetime import datetime, timedelta, timezone
    from app.models import ToolExecutionDetail
    from app.services import tool_details
    from app.workspaces.diffs import change_metadata
    from app.workspaces.batch_mutation import BatchMutationReceipt, validate_items
    items = validate_items('write', [{'path': 'a.txt', 'content': 'x'}])
    receipt = BatchMutationReceipt('write', items)
    receipt.value['status'] = 'success'
    change = change_metadata('a.txt', None, b'x')
    change['files'][0]['hunks'] = [{'old_start': 1, 'old_lines': 0, 'new_start': 1, 'new_lines': 1,
        'lines': [{'kind': 'insert', 'old_line': None, 'new_line': 1, 'text': 'x', 'ending': 'none'}]}]
    receipt.value['items'][0].update(status='success', applied=True, write=change,
        result={'created': True, 'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()})
    now = datetime.now(timezone.utc)
    row = ToolExecutionDetail(message_id=1, call_id='call', execution_id='test', tool_name='workspace_write', status='interrupted',
        started_at=now, expires_at=now + timedelta(days=7), input_encrypted=tool_details._encrypt(1, 'call',
            {'text': '{"items":[{"path":"a.txt"}]}', 'bytes': 28, 'truncated': False}))
    assert tool_details.detail_payload(row)['write_batch'] is None
    class Session:
        """复用已有详情行。"""
        async def scalar(self, statement):
            """Args:
                statement：原消息/调用归属查询。
            """
            return row
    original = tool_details._encrypt
    def fail_diff(message_id, call_id, value):
        """Args:
            message_id：原消息。
            call_id：原调用。
            value：仅正文差异保存失败，元数据重试允许。
        """
        if any(node['write'] and node['write']['files'][0]['hunks'] for node in value['batch']['items']):
            raise ValueError('controlled-encryption-failure')
        return original(message_id, call_id, value)
    monkeypatch.setattr(tool_details, '_encrypt', fail_diff)
    await tool_details.update_detail(Session(), message_id=1, call_id='call', tool_name='workspace_write', status='success',
        execution_id='test', user_id=1, private_input=None, private_output=receipt.export(None))
    value = tool_details.detail_payload(row)['write_batch']
    assert value['status'] == 'success' and value['items'][0]['applied'] is True
    assert value['items'][0]['write']['reason'] == 'capture_failed' and not value['items'][0]['write']['files'][0]['hunks']


@pytest.mark.anyio
async def test_write_creates_parents_only_after_precheck_and_keeps_boundaries(command_root):
    """Args:
        command_root：本轮隔离目录，所有目录副作用均在其中核对。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    service = WorkspaceFileService(root=command_root, execution_id='parents')
    await service.preflight('write', 'test/nested/run.mjs', {'content': 'ok', 'expected_sha256': None})
    assert not (command_root / 'test').exists()
    await asyncio.gather(service.write('test/nested/run.mjs', 'ok'), service.write('test/nested/other.mjs', 'other'))
    assert (command_root / 'test/nested/run.mjs').read_text() == 'ok'
    async def authorize():
        """为确定性批次提供当前授权服务。"""
        return service
    for paths in [('fresh/a.txt', '.env'), ('fresh/file', 'fresh/file/child')]:
        items = validate_items('write', [{'path': path, 'content': 'ok'} for path in paths])
        result = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', items)))
        assert result['status'] == 'rejected'
        assert all(node['created_parent_count'] == 0 for node in result['items'])
        assert not (command_root / 'fresh').exists()
    items = validate_items('write', [{'path': path, 'content': 'ok'} for path in ['new/src/main.js', 'new/test/run.mjs']])
    result = json.loads(await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=BatchMutationReceipt('write', items)))
    assert result['status'] == 'success'
    (command_root / 'link').symlink_to(command_root / 'new', target_is_directory=True)
    for path in ['link/escape/file', '../escape/file', 'absent/.env/file', 'test/nested/run.mjs/file']:
        with pytest.raises(WorkspaceFileError):
            await service.write(path, 'denied')
    assert not (command_root / 'new/escape').exists()
    assert not (command_root / 'absent').exists()
    with pytest.raises(WorkspaceFileError):
        await service.write('stale/missing.txt', 'denied', expected_sha256='0' * 64)
    assert not (command_root / 'stale').exists()

@pytest.mark.anyio
async def test_parent_creation_rechecks_paths_and_reports_creation_failure(command_root, monkeypatch):
    """Args:
        command_root：预检后修改路径的受控目录。
        monkeypatch：模拟目录创建失败，不依赖当前用户的主机权限。
    """
    from pathlib import Path
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    service = WorkspaceFileService(root=command_root, execution_id='parent-race')
    await service.preflight('write', 'race/new/file.txt', {'content': 'ok', 'expected_sha256': None})
    destination = command_root / 'destination'
    destination.mkdir()
    (command_root / 'race').symlink_to(destination, target_is_directory=True)
    with pytest.raises(WorkspaceFileError) as error:
        await service.write('race/new/file.txt', 'denied')
    assert error.value.code == 'WORKSPACE_PATH_OUTSIDE_ROOT'
    assert list(destination.iterdir()) == []
    original = Path.mkdir
    def fail_second(path, *args, **kwargs):
        """Args:
            path：待创建目录，在第二层模拟无权限。
            args：原 mkdir 位置参数。
            kwargs：原 mkdir 关键字参数。
        """
        if path == command_root / 'partial/blocked':
            raise PermissionError('controlled fixture')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', fail_second)
    with pytest.raises(WorkspaceFileError) as error:
        await service.write('partial/blocked/file.txt', 'denied')
    assert error.value.code == 'WORKSPACE_PARENT_NOT_FOUND'
    assert (command_root / 'partial').is_dir()
    assert not (command_root / 'partial/blocked').exists()

@pytest.mark.anyio
async def test_created_parent_receipt_survives_file_rejection(command_root, monkeypatch):
    """Args:
        command_root：隔离工作区。
        monkeypatch：在创建父目录后模拟目标被另一操作创建。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    from app.agent.write_capture import WriteReceipt
    service = WorkspaceFileService(root=command_root, execution_id='parent-receipt')
    original = service._create_write_parents
    def competing_create(path, **kwargs):
        """Args:
            path：当前写入目标。
            kwargs：原目录创建凭据回调。
        """
        original(path, **kwargs)
        (command_root / path).write_text('other writer')
    monkeypatch.setattr(service, '_create_write_parents', competing_create)
    receipt = WriteReceipt('new/deep/file.txt')
    with pytest.raises(WorkspaceFileError) as error:
        await service.write(receipt.path, 'must not overwrite', capture_parent_created=receipt.parent_created)
    assert error.value.code == 'WORKSPACE_FILE_REVISION_CONFLICT'
    receipt.not_executed()
    assert receipt.export(None)['write']['created_parent_count'] == 2
    assert not receipt.commit_confirmed
    assert (command_root / receipt.path).read_text() == 'other writer'

@pytest.mark.anyio
@pytest.mark.parametrize('batch', [False, True])
async def test_parent_rejection_survives_encrypted_detail_roundtrip(command_root, isolated_command_database, monkeypatch, batch):
    """Args:
        command_root：本轮隔离工作区。
        isolated_command_database：独立迁移数据库。
        monkeypatch：受控模型与预检后竞争点。
        batch：覆盖单项与批量的私有凭据和模型错误结果。
    """
    from app.services import chat
    from app.agent.fake_provider import ScriptedChatModel, ScriptedTurn
    from app.workspaces.files import WorkspaceFileService
    from app.agent.tools import FAILED_OUTPUT_PREFIX
    target = 'nested/deep/file.txt'
    def model(prompt):
        """Args:
            prompt：普通测试消息，不转发外部 Provider。
        """
        item = {'path': target, 'content': 'must not overwrite'}
        return ScriptedChatModel(turns=[ScriptedTurn(tool_calls=[{'name': 'workspace_write',
            'args': {'items': [item]} if batch else item, 'id': 'parent-race'}]), ScriptedTurn(text='受控验证完成。')], delay=0)
    monkeypatch.setattr(chat, 'fake_reply_model', model)
    original = WorkspaceFileService._create_write_parents
    def changed(service, path, **kwargs):
        """Args:
            service：本轮真实文件服务。
            path：经过预检的目标。
            kwargs：同步目录计数回调。
        """
        original(service, path, **kwargs)
        (service.root / path).write_text('competing writer')
    monkeypatch.setattr(WorkspaceFileService, '_create_write_parents', changed)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_write']})
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, '验证父目录竞争')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in message['parts_json'] if part['type'] == 'tool_call')
        assert call['effect_state'] == 'unknown' and call['confirmed_applied_items'] == 0
        route = f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}"
        detail = (await client.get(route, headers=headers)).json()
        if batch:
            node = detail['write_batch']['items'][0]
            assert node['applied'] is False and node['created_parent_count'] == 2
        else:
            assert detail['write']['created_parent_count'] == 2
            assert detail['write']['availability'] == 'not_executed'
            output = json.loads(detail['output']['text'].removeprefix(FAILED_OUTPUT_PREFIX).strip())
            assert output['details']['created_parent_count'] == 2
        assert (command_root / target).read_text() == 'competing writer'
        assert (await client.get(route, headers=headers)).json() == detail


@pytest.mark.anyio
async def test_batch_parent_count_survives_cancelled_file_operation(command_root, monkeypatch):
    """Args:
        command_root：本轮目录。
        monkeypatch：在目录创建后、文件写入前确定性取消。
    """
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.batch_mutation import mutate_many, BatchMutationReceipt, validate_items
    service = WorkspaceFileService(root=command_root, execution_id='parent-cancel')
    original = service._create_write_parents
    def cancelled(path, **kwargs):
        """Args:
            path：实际目标。
            kwargs：原目录计数回调。
        """
        original(path, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(service, '_create_write_parents', cancelled)
    async def authorize():
        """返回本轮授权服务。"""
        return service
    items = validate_items('write', [{'path': 'new/child/file.txt', 'content': 'cancelled'}])
    receipt = BatchMutationReceipt('write', items)
    with pytest.raises(asyncio.CancelledError):
        await mutate_many('write', items, authorize=authorize, lock=asyncio.Lock(), receipt=receipt)
    node = receipt.export(None)['batch']['items'][0]
    assert node['created_parent_count'] == 2 and node['applied'] is None
    assert not (command_root / 'new/child/file.txt').exists()
