"""批量读取的预算、独立结果、并发与取消，不覆盖后续批量写入。"""
import asyncio
import hashlib
import json

import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command
from test_workspace_edit import wait_reply


def test_read_schema_accepts_legacy_or_items_but_never_mixed():
    """单工具保留旧路径形式，批量与单项字段互斥且不接受空模式。"""
    from pydantic import ValidationError
    from app.workspaces.tools import WorkspaceReadInput
    assert WorkspaceReadInput(path='a.txt').max_bytes == 65536
    assert WorkspaceReadInput(items=[{'path': 'a.txt'}]).items[0].max_bytes == 65536
    for value in [{}, {'items': None}, {'path': None}, {'path': 'a.txt', 'items': [{'path': 'a.txt'}]},
                  {'items': [{'path': 'a.txt'}], 'offset_bytes': 0}, {'items': [{'path': 'a.txt'}], 'path': None},
                  {'items': [{'path': 'a.txt'}], 'max_bytes': 4}]:
        with pytest.raises(ValidationError):
            WorkspaceReadInput.model_validate(value)


@pytest.mark.anyio
async def test_read_many_preserves_order_hash_and_individual_failure(command_root):
    """Args:
        command_root：本轮受控文件目录。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    (command_root / 'a.txt').write_text('甲🙂保留')
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """测试已绑定的文件服务。"""
        return service
    items = [{'path': 'a.txt', 'offset_bytes': 0, 'max_bytes': 4}, {'path': 'missing.txt'}, {'path': 'a.txt'}]
    receipt = ReadBatchReceipt(items)
    result = json.loads(await read_many(items, authorize=authorize, receipt=receipt))
    assert result['status'] == 'partial'
    assert [item['id'] for item in result['items']] == ['item-0', 'item-1', 'item-2']
    assert [item['status'] for item in result['items']] == ['success', 'failed', 'success']
    assert result['items'][0]['result']['sha256'] == hashlib.sha256('甲🙂保留'.encode()).hexdigest()
    assert result['items'][1]['error_code'] == 'WORKSPACE_FILE_NOT_FOUND'
    assert result['items'][2]['result']['text'] == '甲🙂保留'


@pytest.mark.anyio
async def test_read_many_json_budget_keeps_hash_cursor_and_unicode(command_root):
    """Args:
        command_root：仅含无实际价值预算固件的目录。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    content = '\x01' * 30000 + '🙂尾'
    (command_root / 'escaped.txt').write_text(content)
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """提供受控读取服务。"""
        return service
    items = [{'path': 'escaped.txt', 'max_bytes': 32768}]
    output = await read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items))
    assert len(output.encode()) <= 65536
    node = json.loads(output)['items'][0]
    assert node['output_limited'] is True
    assert node['result']['sha256'] == hashlib.sha256(content.encode()).hexdigest()
    assert node['result']['next_offset'] == node['result']['bytes'] == len(node['result']['text'].encode())
    assert node['result']['eof'] is False


@pytest.mark.anyio
async def test_read_many_cancel_drains_owned_tasks_and_preserves_finished_items(command_root):
    """Args:
        command_root：隔离工作区。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    (command_root / 'a.txt').write_text('done')
    service = WorkspaceFileService(root=command_root, execution_id='test')
    entered = asyncio.Event()
    calls = 0
    async def authorize():
        """第一个完成，其余授权阻塞至取消。"""
        nonlocal calls
        calls += 1
        if calls > 1:
            entered.set()
            await asyncio.Event().wait()
        return service
    items = [{'path': 'a.txt'}] * 8
    receipt = ReadBatchReceipt(items)
    task = asyncio.create_task(read_many(items, authorize=authorize, receipt=receipt))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receipt.value['status'] == 'cancelled'
    assert receipt.value['items'][0]['status'] == 'success'
    assert all(item['status'] in {'success', 'cancelled', 'not_executed'} for item in receipt.value['items'])
    assert calls <= 3


@pytest.mark.anyio
async def test_read_many_rechecks_permissions_and_bounds_batch_admission(command_root):
    """Args:
        command_root：隔离根，第三批由同一调用排队而非要求模型重试。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.scan_admission import current_pool
    (command_root / 'a.txt').write_text('read')
    service = WorkspaceFileService(root=command_root, execution_id='test')
    entered, proceed = asyncio.Event(), asyncio.Event()
    count = 0
    async def authorize():
        """同时持有两个准入槽，释放后后续调用公平获得执行机会。"""
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await proceed.wait()
        return service
    items = [{'path': 'a.txt'}]
    tasks = [asyncio.create_task(read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items))) for _ in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 3)
        third = asyncio.create_task(read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
        tasks.append(third)
        await asyncio.sleep(0)
        assert len(current_pool().queue) == 1 and not third.done()
        proceed.set()
        results = [json.loads(value) for value in await asyncio.gather(*tasks)]
        assert all(value['status'] == 'success' for value in results)
        assert not current_pool().active and not current_pool().queue
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('items,code', [
    ([], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ([{'path': 'a'}] * 9, 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ([{'path': 'a', 'max_bytes': True}], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ([{'path': 'a', 'offset_bytes': '0'}], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ([{'path': 'a', 'content': 'not allowed'}], 'WORKSPACE_BATCH_ARGUMENT_INVALID'),
    ([{'path': '中' * 1000}] * 8, 'WORKSPACE_BATCH_INPUT_TOO_LARGE'),
], ids=['empty', 'count', 'bool', 'coercion', 'extra', 'utf8-input-budget'])
def test_read_many_rejects_invalid_batch_before_any_io(items, code):
    """Args:
        items：受控无效请求。
        code：固定预期错误码。
    """
    from app.workspaces.batch_read import validate_items
    from app.workspaces.files import WorkspaceFileError
    with pytest.raises(WorkspaceFileError) as error:
        validate_items(items)
    assert error.value.code == code


@pytest.mark.anyio
async def test_agent_read_many_private_nodes_independent_permission_and_safe_summary(command_root, isolated_command_database):
    """Args:
        command_root：本轮目录。
        isolated_command_database：新迁移数据库，验证实际 Agent 与加密详情。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall, ToolExecutionDetail, EventLog
    (command_root / 'first.txt').write_text('BATCH-PRIVATE-ONE')
    (command_root / 'second.txt').write_text('BATCH-PRIVATE-TWO')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_read']})).status_code == 200
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, '[READ_MANY_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_read')
        assert call['error_code'] == 'WORKSPACE_BATCH_PARTIAL'
        detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
        assert [node['status'] for node in detail['read_batch']['items']] == ['success', 'failed', 'success']
        assert detail['read_batch']['items'][0]['result']['text'] == 'BATCH-PRIVATE-ONE'
        assert detail['output'] is None
        async with SessionLocal() as session:
            record = await session.scalar(select(ToolCall).where(ToolCall.conversation_id == cid))
            assert json.loads(record.args_summary) == {'item_count': 3}
            encrypted = await session.scalar(select(ToolExecutionDetail).where(ToolExecutionDetail.message_id == message['id']))
            assert 'BATCH-PRIVATE' not in encrypted.output_encrypted
            for event in (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid))).all():
                assert 'first.txt' not in str(event.payload_json) and 'BATCH-PRIVATE' not in str(event.payload_json)


@pytest.mark.anyio
async def test_read_many_file_boundaries_and_repeat_observation(command_root):
    """Args:
        command_root：边界固件目录，不读取任何真实用户文件。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    (command_root / 'a.txt').write_text('🙂')
    (command_root / 'large.txt').write_bytes(b'x' * (16 * 1024 * 1024 + 1))
    (command_root / 'binary.txt').write_bytes(b'\xff')
    (command_root / 'link.txt').symlink_to(command_root / 'a.txt')
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """复用已绑定服务。"""
        return service
    items = [{'path': name, 'max_bytes': 4} for name in ['a.txt', '../escape', '.env', 'link.txt', 'large.txt', 'binary.txt']]
    value = json.loads(await read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
    assert [node['error_code'] for node in value['items']] == [None, 'WORKSPACE_PATH_INVALID', 'WORKSPACE_PATH_SENSITIVE',
        'WORKSPACE_PATH_OUTSIDE_ROOT', 'WORKSPACE_FILE_TOO_LARGE', 'WORKSPACE_FILE_NOT_TEXT']
    assert value['items'][0]['result']['text'] == '🙂' and value['items'][0]['result']['bytes'] == 4
    first_hash = value['items'][0]['result']['sha256']
    (command_root / 'a.txt').write_text('new')
    again = json.loads(await read_many(items[:1], authorize=authorize, receipt=ReadBatchReceipt(items[:1])))
    assert again['items'][0]['result']['sha256'] != first_hash


@pytest.mark.anyio
async def test_read_many_factory_revocation_and_schema_errors(command_root, isolated_command_database):
    """Args:
        command_root：本轮目录。
        isolated_command_database：隔离数据库，用真实工具工厂验证二次授权。
    """
    from datetime import datetime, timezone
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, Role
    from app.workspaces.tools import create_workspace_tools
    (command_root / 'a.txt').write_text('authorized')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            role.builtin_tools_json = ['workspace_read', 'workspace_read_many']
            generation = Generation(conversation_id=cid, status='running', stream_epoch='batch-test')
            session.add(generation)
            await session.flush()
            execution = AgentExecution(execution_id=command_root.name, conversation_id=cid, generation_id=generation.id,
                role_id=rid, chain_id='batch-test', execution_kind='single', status='running', created_at=datetime.now(timezone.utc))
            session.add(execution)
            await session.commit()
            tools = {tool.name: tool for tool in await create_workspace_tools(session, execution_id=execution.execution_id,
                conversation_id=cid, role=role, triggered_by_user_id=role.created_by, allow_dangerous=True)}
            assert set(tools) == {'workspace_read'}
            assert await create_workspace_tools(session, execution_id=execution.execution_id, conversation_id=cid,
                role=role, triggered_by_user_id=role.created_by, allow_dangerous=False) == []
        tool = tools['workspace_read']
        for invalid in [{'items': []}, {'items': [{'path': 'a.txt', 'max_bytes': True}]}, {'items': [{'path': 'a.txt'}], 'workspace_id': wid}]:
            output = await tool.ainvoke(invalid)
            assert output.startswith('[工具被拒绝]') and 'WORKSPACE_READ_ARGUMENT_INVALID' in output
        assert json.loads(await tool.ainvoke({'items': [{'path': 'a.txt'}]}))['status'] == 'success'
        legacy = json.loads(await tool.ainvoke({'path': 'a.txt'}))
        assert set(legacy) == {'text', 'bytes', 'eof', 'next_offset', 'sha256'} and legacy['text'] == 'authorized'
        assert (await tool.ainvoke({'path': 'a.txt', 'items': [{'path': 'a.txt'}]})).startswith('[工具被拒绝]')
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': False})
        assert (await tool.ainvoke({'items': [{'path': 'a.txt'}]})).startswith('[工具被拒绝]')
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            role.builtin_tools_json = ['workspace_list']
            await session.commit()
        assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tool.ainvoke({'items': [{'path': 'a.txt'}]})


def test_read_many_private_scope_and_crash_gap_do_not_invent_results():
    """正常收尾通过既有作用域取结果，崩溃遗留仅显示中断而不从输入重读。"""
    from datetime import datetime, timedelta, timezone
    from app.agent.write_capture import WriteCaptureScope
    from app.models import ToolExecutionDetail
    from app.services.tool_details import detail_payload, _encrypt
    scope = WriteCaptureScope()
    receipt = scope.begin_read_batch('call', [{'path': 'a.txt'}])
    receipt.value.update(status='cancelled')
    receipt.value['items'][0]['status'] = 'not_executed'
    output = scope.take('call')
    assert output['format'] == 'read-batch-v1' and output['batch']['status'] == 'cancelled'
    assert scope.take('call') is None
    now = datetime.now(timezone.utc)
    row = ToolExecutionDetail(message_id=1, call_id='call', execution_id='test', tool_name='workspace_read',
        status='interrupted', started_at=now, expires_at=now + timedelta(days=7),
        input_encrypted=_encrypt(1, 'call', {'text': '{"items":[]}', 'bytes': 12, 'truncated': False}))
    assert detail_payload(row)['read_batch'] is None
    row.output_encrypted = _encrypt(1, 'call', output)
    assert detail_payload(row)['read_batch']['status'] == 'cancelled'
    row.tool_name = 'workspace_read_many'
    assert detail_payload(row)['tool_name'] == 'workspace_read_many'
    assert detail_payload(row)['read_batch']['status'] == 'cancelled'
    row.call_id = 'other'
    assert detail_payload(row)['availability'] == 'unavailable'


@pytest.mark.anyio
async def test_read_many_maximum_batch_keeps_serialized_bound(command_root):
    """Args:
        command_root：八个最大受支持文件的受控目录。
    """
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    from app.workspaces.files import WorkspaceFileService
    from app.schemas import BatchReadDetailView
    for index in range(8):
        (command_root / f'{index}.txt').write_bytes(b'\x01' * (1024 * 1024))
    service = WorkspaceFileService(root=command_root, execution_id='test')
    async def authorize():
        """提供本轮服务。"""
        return service
    items = [{'path': f'{index}.txt'} for index in range(8)]
    receipt = ReadBatchReceipt(items)
    output = await read_many(items, authorize=authorize, receipt=receipt)
    assert len(output.encode()) <= 65536
    assert len(json.dumps(BatchReadDetailView.model_validate(receipt.value).model_dump(), ensure_ascii=False, separators=(',', ':')).encode()) <= 65536
    assert receipt.value['items'][0]['output_limited'] and receipt.value['items'][0]['result']['next_offset'] > 0
    assert any(node['status'] == 'budget_exhausted' for node in receipt.value['items'])
    assert sum(node['result']['bytes'] for node in receipt.value['items'] if node['result']) <= 65536


@pytest.mark.anyio
async def test_read_many_private_save_failure_degrades_without_plaintext(monkeypatch):
    """Args:
        monkeypatch：仅使结果加密失败，不破坏认证或输入保存。
    """
    from datetime import datetime, timedelta, timezone
    from app.models import ToolExecutionDetail
    from app.services import tool_details
    now = datetime.now(timezone.utc)
    row = ToolExecutionDetail(message_id=1, call_id='call', execution_id='test', tool_name='workspace_read',
        status='running', started_at=now, expires_at=now + timedelta(days=7))
    class Session:
        """只提供既有记录查询，不接触真实数据库。"""
        async def scalar(self, statement):
            """Args:
                statement：按消息/调用身份查找的语句。
            """
            return row
    def fail(*args):
        """Args:
            args：加密输入不进入异常文本。
        """
        raise ValueError('controlled-cipher-failure')
    monkeypatch.setattr(tool_details, '_encrypt', fail)
    assert await tool_details.update_detail(Session(), message_id=1, call_id='call', tool_name='workspace_read',
        status='success', execution_id='test', user_id=1, private_input=None, private_output={'format': 'read-batch-v1'})
    assert row.status == 'success' and row.output_encrypted is None
    assert tool_details.detail_payload(row).get('read_batch') is None
