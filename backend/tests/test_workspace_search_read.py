"""T2 搜索定位、范围读取与实际预算的离线行为验收。"""
import hashlib
import json
import asyncio

import pytest

from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command


@pytest.mark.anyio
async def test_small_files_do_not_reserve_requested_batch_budget(command_root):
    """Args:
        command_root：独立的本轮文件目录。
    """
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    service = WorkspaceFileService(root=command_root, execution_id='read-test')
    for name in ['a.txt', 'b.txt']:
        (command_root / name).write_text('tiny', encoding='utf-8')

    async def authorize():
        """每次授权返回本轮绑定服务。"""
        return service

    items = [{'path': name, 'max_bytes': 32768} for name in ['a.txt', 'b.txt']]
    value = json.loads(await read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
    assert value['status'] == 'success'
    assert [item['result']['text'] for item in value['items']] == ['tiny', 'tiny']


@pytest.mark.anyio
async def test_large_file_range_has_full_hash_and_exact_lines(command_root):
    """Args:
        command_root：大文件只含占位内容，不能碰用户文件。
    """
    from app.workspaces.files import WorkspaceFileService
    content = b'padding\n' * 150000 + 'target🙂\r\nkeep\nlast'.encode()
    (command_root / 'large.txt').write_bytes(content)
    service = WorkspaceFileService(root=command_root, execution_id='range-test')
    result = await service.read_lines('large.txt', start_line=150001, end_line=150002)
    assert result['text'] == 'target🙂\r\nkeep\n'
    assert result['sha256'] == hashlib.sha256(content).hexdigest()
    assert result['next_line'] == 150003 and not result['eof']
    assert result['scanned_bytes'] == len(content)
    assert result['start_offset'] == len(b'padding\n' * 150000)


@pytest.mark.anyio
async def test_search_locates_unknown_line_and_rejects_changed_version(command_root):
    """Args:
        command_root：带干扰文件与敏感路径的受控目录。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    (command_root / 'source.py').write_text('head\ndef target():\n    pass\n', encoding='utf-8')
    (command_root / '.env').write_text('target=placeholder', encoding='utf-8')
    service = WorkspaceFileService(root=command_root, execution_id='search-test')
    value = await service.search(query='def target', mode='text')
    assert value['status'] == 'complete'
    assert len(value['matches']) == 1
    hit = value['matches'][0]
    assert hit['path'] == 'source.py' and hit['line_number'] == 2
    result = await service.read_lines(hit['path'], start_line=hit['line_number'], end_line=3, expected_sha256=hit['sha256'])
    assert result['text'] == 'def target():\n    pass\n'
    (command_root / 'source.py').write_text('new\n', encoding='utf-8')
    with pytest.raises(WorkspaceFileError) as error:
        await service.read_lines(hit['path'], start_line=2, expected_sha256=hit['sha256'])
    assert error.value.code == 'WORKSPACE_FILE_REVISION_CONFLICT'


@pytest.mark.anyio
async def test_batch_spends_actual_bytes_and_preserves_unread_nodes(command_root, monkeypatch):
    """Args:
        command_root：本轮文件目录。
        monkeypatch：固定主机预算，不允许模型增大。
    """
    from app.config import settings
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    monkeypatch.setattr(settings, 'workspace_read_content_bytes', 1024)
    for name in ['a', 'b', 'c']:
        (command_root / name).write_text('x' * 600)
    service = WorkspaceFileService(root=command_root, execution_id='budget')
    async def authorize():
        """返回当前测试服务。"""
        return service
    items = [{'path': name} for name in ['a', 'b', 'c']]
    value = json.loads(await read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
    assert [item['status'] for item in value['items']] == ['success', 'success', 'budget_exhausted']
    assert [item['result']['bytes'] for item in value['items'][:2]] == [600, 424]
    assert value['items'][1]['result']['next_offset'] == 424
    assert value['items'][2]['result'] is None


@pytest.mark.anyio
async def test_utf8_budget_does_not_cross_remaining_batch_allowance(command_root, monkeypatch):
    """Args:
        command_root：UTF-8 边界固件。
        monkeypatch：固定实际内容上限。
    """
    from app.config import settings
    from app.workspaces.files import WorkspaceFileService
    from app.workspaces.batch_read import read_many, ReadBatchReceipt
    monkeypatch.setattr(settings, 'workspace_read_content_bytes', 1024)
    (command_root / 'a').write_text('x' * 1022)
    (command_root / 'b').write_text('🙂', encoding='utf-8')
    service = WorkspaceFileService(root=command_root, execution_id='utf8')
    async def authorize():
        """返回本轮服务。"""
        return service
    items = [{'path': 'a'}, {'path': 'b'}]
    value = json.loads(await read_many(items, authorize=authorize, receipt=ReadBatchReceipt(items)))
    assert value['items'][1]['status'] == 'budget_exhausted'
    assert value['items'][1]['result'] is None
    assert (await service.read('b', max_bytes=1)).text == '🙂'


@pytest.mark.anyio
async def test_search_exclusions_binary_and_budget_are_not_false_empty(command_root, monkeypatch):
    """Args:
        command_root：隔离的排除目录/链接/二进制固件。
        monkeypatch：缩小扫描量以稳定触发未覆盖。
    """
    from app.config import settings
    from app.workspaces.files import WorkspaceFileService
    (command_root / 'node_modules').mkdir()
    (command_root / 'node_modules' / 'hidden.py').write_text('needle')
    (command_root / '.env').write_text('needle')
    (command_root / 'visible.py').write_text('needle\n')
    (command_root / 'linked.py').symlink_to(command_root / 'visible.py')
    service = WorkspaceFileService(root=command_root, execution_id='exclusions')
    files = await service.search(query='*.py', mode='files')
    assert [hit['path'] for hit in files['matches']] == ['visible.py']
    assert files['matches'][0]['line_number'] is None and files['matches'][0]['sha256'] is None
    (command_root / 'binary').write_bytes(b'needle\0')
    result = await service.search(query='absent')
    assert result['status'] == 'partial' and not result['matches']
    assert any(issue['reason'] == 'WORKSPACE_FILE_NOT_TEXT' for issue in result['issues'])
    monkeypatch.setattr(settings, 'workspace_scan_total_bytes', 1024)
    (command_root / 'large').write_text('a' * 2048)
    result = await service.search(query='absent', path='large')
    assert result['status'] == 'partial' and result['scanned_bytes'] <= 1025
    assert not result['matches']


@pytest.mark.anyio
async def test_line_eof_long_line_and_mid_scan_change(command_root, monkeypatch):
    """Args:
        command_root：边界文件目录。
        monkeypatch：在最终校验前改变文件，不伪造模型结果。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    service = WorkspaceFileService(root=command_root, execution_id='line-cases')
    (command_root / 'empty').write_bytes(b'')
    assert (await service.read_lines('empty', start_line=1))['eof'] is True
    (command_root / 'long').write_text('x' * 70000 + '\nlast')
    long = await service.read_lines('long', start_line=1)
    assert long['bytes'] == 0 and long['next_line'] == 1 and long['limited_reason'] == 'line_too_long'
    last = await service.read_lines('long', start_line=2)
    assert last['text'] == 'last' and last['start_offset'] == 70001
    assert (await service.read_lines('long', start_line=9))['eof'] is True
    (command_root / 'changing').write_text('before\n')
    original = service._resolve
    calls = 0
    def changed(path, **kwargs):
        """Args:
            path：当前测试目标。
            kwargs：原路径检查参数。
        """
        nonlocal calls
        calls += 1
        if calls == 2:
            (command_root / 'changing').write_text('after\n')
        return original(path, **kwargs)
    monkeypatch.setattr(service, '_resolve', changed)
    with pytest.raises(WorkspaceFileError) as error:
        await service.read_lines('changing', start_line=1)
    assert error.value.code == 'WORKSPACE_FILE_REVISION_CONFLICT'


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'close', 'handoff'])
async def test_scan_queue_cancellation_timeout_close_and_handoff(mode):
    """Args:
        mode：覆盖排队取消/超时/关闭/移交与取消竞争。
    """
    from app.workspaces.scan_admission import ScanAdmission
    from app.workspaces.files import WorkspaceFileError
    pool = ScanAdmission(capacity=1, waiting=1, timeout=.03)
    entered, release = asyncio.Event(), asyncio.Event()
    executed = []
    async def holder():
        """持有实际槽位直到测试释放。"""
        async with pool.slot():
            entered.set()
            await release.wait()
    async def waiter():
        """仅真正获得槽位才记录执行，不允许取消后迟到运行。"""
        async with pool.slot(100):
            executed.append(True)
    first = asyncio.create_task(holder())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        with pytest.raises(WorkspaceFileError) as full:
            async with pool.slot(100):
                pytest.fail('队列已满不应准入')
        assert full.value.code == 'WORKSPACE_SCAN_BUSY'
        if mode == 'timeout':
            with pytest.raises(WorkspaceFileError) as timed:
                await second
            assert timed.value.code == 'WORKSPACE_SCAN_QUEUE_TIMEOUT'
        elif mode == 'close':
            await pool.close()
            with pytest.raises(WorkspaceFileError) as closed:
                await second
            assert closed.value.code == 'WORKSPACE_SCAN_CLOSED'
        else:
            if mode == 'handoff':
                pool.release(first)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        assert not executed
    finally:
        release.set()
        for task in [first, second]:
            if task is not None:
                task.cancel()
        await asyncio.gather(*[task for task in [first, second] if task], return_exceptions=True)
    assert not pool.active and not pool.queue and pool.queued_bytes == 0


@pytest.mark.anyio
async def test_factory_search_range_details_and_version_conflict(command_root, isolated_command_database):
    """Args:
        command_root：带未知行号的实际文件。
        isolated_command_database：本用例迁移新库，鉴权/落库走正常链路。
    """
    from test_workspace_edit import wait_reply
    (command_root / 'source.txt').write_text('padding\n' * 150000 + 'TARGET_FUNCTION\nsecret-placeholder\n', encoding='utf-8')
    (command_root / 'small.txt').write_text('small')
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_search', 'workspace_read']})
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        sent = await send_command(client, headers, cid, '[SEARCH_READ_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        assert message['status'] == 'done'
        assert 'source.txt' not in json.dumps(message) and 'secret-placeholder' not in json.dumps(message)
        cards = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert [part['tool_name'] for part in cards] == ['workspace_search', 'workspace_read', 'workspace_read']
        details = [(await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{part['call_id']}", headers=headers)).json()
                   for part in cards]
        assert details[0]['search']['matches'][0]['line_number'] == 150001
        assert details[0]['search']['matches'][0]['matched_queries'] == [0]
        assert json.loads(details[0]['input']['text'])['queries'] == ['TARGET_FUNCTION', 'SECOND_TARGET']
        assert details[1]['read_range']['text'] == 'TARGET_FUNCTION\nsecret-placeholder\n'
        assert [node['status'] for node in details[2]['read_batch']['items']] == ['success', 'success', 'failed', 'success']
        assert details[2]['read_batch']['items'][0]['result']['mode'] == 'lines'
        stale = await send_command(client, headers, cid, '[SEARCH_STALE_FAKE]')
        reply = await wait_reply(client, headers, cid, stale['message']['id'])
        call = next(part for part in reply['parts_json'] if part.get('tool_name') == 'workspace_read')
        assert call['status'] == 'rejected' and call['error_code'] == 'WORKSPACE_FILE_REVISION_CONFLICT'


@pytest.mark.anyio
async def test_queued_search_rechecks_role_before_any_scan(command_root, isolated_command_database, monkeypatch):
    """Args:
        command_root：本轮工作区。
        isolated_command_database：独立权限数据库。
        monkeypatch：只记录实际搜索入口，不能跳过执行层授权。
    """
    from app.workspaces.scan_admission import current_pool
    from app.workspaces.files import WorkspaceFileService
    from test_workspace_edit import wait_reply
    scans = []
    original = WorkspaceFileService.search
    async def observed(self, **kwargs):
        """Args:
            kwargs：原搜索参数，仅记录是否执行。
        """
        scans.append(True)
        return await original(self, **kwargs)
    monkeypatch.setattr(WorkspaceFileService, 'search', observed)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
        await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_search', 'workspace_read']})
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        release = asyncio.Event()
        async def held():
            """占用槽位但不接触文件。"""
            async with current_pool().slot():
                await release.wait()
        tasks = [asyncio.create_task(held()) for _ in range(2)]
        try:
            await asyncio.sleep(0)
            sent = await send_command(client, headers, cid, '[SEARCH_READ_FAKE]')
            for _ in range(100):
                if current_pool().queue:
                    break
                await asyncio.sleep(.01)
            assert current_pool().queue
            await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_read']})
            release.set()
            await asyncio.gather(*tasks)
            message = await wait_reply(client, headers, cid, sent['message']['id'])
            call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_search')
            assert call['status'] == 'rejected' and scans == []
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

@pytest.mark.anyio
async def test_multi_query_scans_once_and_preserves_literal_operators(command_root):
    """Args:
        command_root：本轮隔离文件目录。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    from app.agent.tools import summarize_tool_args
    summary = json.loads(summarize_tool_args('workspace_search', {'queries': ['私有甲', '私有乙']}))
    assert summary == {'mode': 'text', 'query_bytes': 18}
    content = 'alpha\nbeta\nalpha beta\na|b & c\n'
    (command_root / 'multi.txt').write_text(content)
    service = WorkspaceFileService(root=command_root, execution_id='multi-search')
    result = await service.search(path='multi.txt', queries=['alpha', 'beta'])
    assert result['scanned_bytes'] == len(content.encode())
    assert [hit['line_number'] for hit in result['matches']] == [1, 2, 3]
    assert result['matches'][2]['matched_queries'] == [0, 1]
    result = await service.search(path='multi.txt', queries=['alpha', 'beta'], match='all')
    assert [hit['line_number'] for hit in result['matches']] == [3]
    assert len((await service.search(path='multi.txt', query='a|b & c'))['matches']) == 1
    for args in ({'queries': []}, {'query': 'a', 'queries': ['b']}, {'queries': ['a'], 'mode': 'files'}, {'queries': ['a'] * 9}, {'queries': ['']}, {'queries': ['a'], 'match': 'bad'}):
        with pytest.raises(WorkspaceFileError) as error:
            await service.search(**args)
        assert error.value.code == 'WORKSPACE_SEARCH_ARGUMENT_INVALID'
