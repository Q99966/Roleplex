"""E1 精确局部编辑：复用原生文件提交、权限和私有 diff。"""
import asyncio
import hashlib
import json

import pytest
from test_workspace_commands import command_root, isolated_command_database, command_conversation, send_command, wait_command_messages

FIRST = "export const title = '第一版';\nexport const keep = '保持';\n"


async def enable_edit(client, headers, rid, wid):
    """通过真实配置启用 read/edit，不隐含 write 权限。

    Args:
        client：本轮客户端。
        headers：Owner 认证。
        rid：角色身份。
        wid：工作区身份。
    """
    role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
    assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_read', 'workspace_edit']})).status_code == 200
    assert (await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})).status_code == 200


async def wait_reply(client, headers, cid, after_id):
    """等待本轮而非上一轮的真实终态，支持一个用例内连续交互。

    Args:
        client：客户端。
        headers：认证头。
        cid：会话身份。
        after_id：本次用户消息 ID。
    """
    for _ in range(200):
        history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
        replies = [item for item in history['items'] if item['id'] > after_id and item['sender_type'] == 'role']
        if not history['active_generation_ids'] and replies:
            return replies[-1]
        await asyncio.sleep(.03)
    raise AssertionError('编辑回复未在预期时间结束')


@pytest.mark.anyio
@pytest.mark.parametrize('before,old,new,after', [
    ('甲\r\n    foo🙂\r\n尾行', 'foo🙂', 'bar', '甲\r\n    bar\r\n尾行'),
    ('hello', 'hello', '', ''), ('保持', '保持', '保持', '保持'),
    ('前\n  后', '  ', '\t', '前\n\t后'), ('a.*b', '.*', '+', 'a+b'),
], ids=['unicode-crlf', 'delete-fragment', 'unchanged', 'whitespace', 'literal-not-regex'])
async def test_edit_exact_text_preserves_unmodified_bytes(command_root, before, old, new, after):
    """精确替换保留未修改字节；删除片段不删除文件，不解释正则。

    Args:
        command_root：本轮独立工作区。
        before：旧内容。
        old：唯一旧片段。
        new：新片段。
        after：精确预期内容。
    """
    from app.workspaces.files import WorkspaceFileService
    path = command_root / 'sample.txt'
    path.write_bytes(before.encode())
    service = WorkspaceFileService(root=command_root, execution_id='edit-test')
    read = await service.read('sample.txt', max_bytes=16)
    evidence = []
    result = await service.edit('sample.txt', old, new, expected_sha256=read.sha256,
        capture_applied=lambda a, b: evidence.append((a, b)))
    assert path.read_bytes() == after.encode()
    assert result.created is False and result.sha256 == hashlib.sha256(after.encode()).hexdigest()
    assert evidence == [(before.encode(), after.encode())]


@pytest.mark.anyio
@pytest.mark.parametrize('before,old,new,code', [
    ('hello', '', 'new', 'WORKSPACE_EDIT_ARGUMENT_INVALID'),
    ('hello', 'missing', 'new', 'WORKSPACE_EDIT_MATCH_NOT_FOUND'),
    ('foo foo', 'foo', 'new', 'WORKSPACE_EDIT_MATCH_AMBIGUOUS'),
    ('aaa', 'aa', 'b', 'WORKSPACE_EDIT_MATCH_AMBIGUOUS'),
    ('hello', 'hello', '中' * 22000, 'WORKSPACE_EDIT_INPUT_TOO_LARGE'),
], ids=['empty-old', 'missing', 'multiple', 'overlapping', 'utf8-budget'])
async def test_edit_rejections_never_write(command_root, before, old, new, code):
    """非法或不唯一片段只返回稳定错误，文件保持不变。

    Args:
        command_root：独立目录。
        before：旧内容。
        old：旧片段。
        new：新片段。
        code：预期错误码。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    path = command_root / 'sample.txt'
    path.write_bytes(before.encode())
    service = WorkspaceFileService(root=command_root, execution_id='edit-test')
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit(path.name, old, new, expected_sha256=hashlib.sha256(before.encode()).hexdigest())
    assert error.value.code == code
    assert path.read_bytes() == before.encode()


@pytest.mark.anyio
async def test_edit_write_share_revision_lock_and_size_boundary(command_root):
    """edit/write 使用同一锁和提交路径，旧版本并发操作只能有一次成功。

    Args:
        command_root：独立工作区。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError, MAX_FILE_BYTES
    path = command_root / 'sample.txt'
    path.write_text('old')
    first = WorkspaceFileService(root=command_root, execution_id='first')
    second = WorkspaceFileService(root=command_root, execution_id='second')
    digest = hashlib.sha256(b'old').hexdigest()
    results = await asyncio.gather(first.edit(path.name, 'old', 'edited', expected_sha256=digest),
        second.write(path.name, 'written', expected_sha256=digest), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert next(result for result in results if isinstance(result, WorkspaceFileError)).code == 'WORKSPACE_FILE_REVISION_CONFLICT'
    large = b'x' * (MAX_FILE_BYTES - 1) + b'y'
    path.write_bytes(large)
    with pytest.raises(WorkspaceFileError) as error:
        await first.edit(path.name, 'y', 'yy', expected_sha256=hashlib.sha256(large).hexdigest())
    assert error.value.code == 'WORKSPACE_FILE_TOO_LARGE' and path.read_bytes() == large


@pytest.mark.anyio
@pytest.mark.parametrize('missing', [False, True], ids=['applied', 'match-rejected'])
async def test_agent_edit_has_private_diff_and_safe_errors(command_root, isolated_command_database, missing):
    """实际 Agent 工具循环保留名称、状态、私有差异，错误不泄漏片段。

    Args:
        command_root：独立目录。
        isolated_command_database：新迁移数据库。
        missing：是否令片段不匹配。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall, EventLog
    (command_root / 'edit.ts').write_bytes(FIRST.encode())
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_edit(client, headers, rid, wid)
        sent = await send_command(client, headers, cid, '[EDIT_FAKE_MISSING]' if missing else '[EDIT_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_edit')
        assert call['status'] == ('rejected' if missing else 'success')
        detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
        if missing:
            assert call['error_code'] == 'WORKSPACE_EDIT_MATCH_NOT_FOUND'
            assert detail['write']['availability'] == 'not_executed'
            assert (command_root / 'edit.ts').read_text() == FIRST
        else:
            assert detail['tool_name'] == 'workspace_edit'
            assert detail['write']['availability'] == 'recorded'
            assert detail['write']['files'][0]['added'] == detail['write']['files'][0]['removed'] == 1
            assert json.loads(detail['output']['text'])['created'] is False
            assert (command_root / 'edit.ts').read_text() == FIRST.replace('第一版', '第二版🙂')
        async with SessionLocal() as session:
            record = await session.scalar(select(ToolCall).where(ToolCall.conversation_id == cid, ToolCall.tool_name == 'workspace_edit'))
            summary = json.loads(record.args_summary)
            assert set(summary) == {'path_fingerprint', 'old_text_bytes', 'new_text_bytes', 'has_expected_sha256'}
            for event in (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid))).all():
                assert '第一版' not in str(event.payload_json) and 'before_sha256' not in str(event.payload_json)


@pytest.mark.anyio
async def test_running_service_blocks_edit_until_explicit_stop(command_root, isolated_command_database):
    """服务存续仍拒绝原生 edit，不自动停服；明确回收后可再次编辑。

    Args:
        command_root：独立工作区。
        isolated_command_database：新库。
    """
    import sys
    if sys.platform != 'linux':
        pytest.skip('受托管服务仅 Linux 开放')
    from test_runtime_service import enable_service, launch
    (command_root / 'edit.ts').write_bytes(FIRST.encode())
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_service(client, headers, rid, wid)
        running = await launch(client, headers, cid)
        await enable_edit(client, headers, rid, wid)
        sent = await send_command(client, headers, cid, '[EDIT_FAKE]')
        reply = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in reply['parts_json'] if part.get('tool_name') == 'workspace_edit')
        assert call['status'] == 'rejected' and call['error_code'] == 'WORKSPACE_SERVICE_ACTIVE'
        assert (command_root / 'edit.ts').read_text() == FIRST
        rows = (await client.get(f'/api/conversations/{cid}/processes', headers=headers)).json()['items']
        assert next(row for row in rows if row['id'] == running['id'])['state'] == 'ready'
        stopped = await client.post(f"/api/conversations/{cid}/processes/{running['id']}/stop", headers=headers)
        assert stopped.status_code == 200 and stopped.json()['state'] == 'stopped'
        sent = await send_command(client, headers, cid, '[EDIT_FAKE]')
        reply = await wait_reply(client, headers, cid, sent['message']['id'])
        assert next(part for part in reply['parts_json'] if part.get('tool_name') == 'workspace_edit')['status'] == 'success'


@pytest.mark.anyio
async def test_edit_rechecks_before_commit_and_preserves_external_change(command_root, monkeypatch):
    """临时写入后出现外部版本变化时拒绝，不覆盖外部新内容。

    Args:
        command_root：独立目录。
        monkeypatch：在提交边界制造外部更新。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    target = command_root / 'race.txt'
    target.write_bytes(b'old')
    service = WorkspaceFileService(root=command_root, execution_id='race')
    original = service._read_update
    reads = 0

    def changed(path, digest):
        """Args:
            path：原目标。
            digest：正在复核的旧 hash。
        """
        nonlocal reads
        reads += 1
        if reads == 2:
            target.write_bytes(b'external')
        return original(path, digest)

    monkeypatch.setattr(service, '_read_update', changed)
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit('race.txt', 'old', 'new', expected_sha256=hashlib.sha256(b'old').hexdigest())
    assert error.value.code == 'WORKSPACE_FILE_REVISION_CONFLICT'
    assert target.read_bytes() == b'external'
    assert not list(command_root.glob('.roleplex-w1a-*'))


@pytest.mark.anyio
async def test_edit_rejects_missing_sensitive_link_and_binary_files(command_root):
    """沿用路径安全边界，不创建缺失文件、不编辑敏感或非文本目标。

    Args:
        command_root：无用户数据的独立目录。
    """
    from app.workspaces.files import WorkspaceFileService, WorkspaceFileError
    service = WorkspaceFileService(root=command_root, execution_id='edit-paths')
    digest = hashlib.sha256(b'old').hexdigest()
    for name, code in [('missing.txt', 'WORKSPACE_FILE_NOT_FOUND'), ('.env', 'WORKSPACE_PATH_SENSITIVE'),
                       ('../escape.txt', 'WORKSPACE_PATH_INVALID')]:
        with pytest.raises(WorkspaceFileError) as error:
            await service.edit(name, 'old', 'new', expected_sha256=digest)
        assert error.value.code == code
    assert not (command_root / 'missing.txt').exists()
    binary = command_root / 'binary.bin'
    binary.write_bytes(b'\xff')
    with pytest.raises(WorkspaceFileError) as error:
        await service.edit(binary.name, 'old', 'new', expected_sha256=hashlib.sha256(b'\xff').hexdigest())
    assert error.value.code == 'WORKSPACE_FILE_NOT_TEXT'
    target = command_root / 'target.txt'
    target.write_bytes(b'old')
    link = command_root / 'link.txt'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('当前平台未开放 symlink 创建')
    with pytest.raises(WorkspaceFileError):
        await service.edit(link.name, 'old', 'new', expected_sha256=digest)
    assert target.read_bytes() == b'old'


@pytest.mark.anyio
async def test_edit_cancel_after_commit_keeps_applied_diff_fact(command_root, isolated_command_database, monkeypatch):
    """取消差异等待不会丢失 edit 已提交事实，也不会重复修改。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：只暂停技术差异计算。
    """
    from app.workspaces.diffs import DiffTicket, current_pool
    reached = asyncio.Event()

    async def held(_ticket):
        """Args:
            _ticket：成功提交后创建的有界计算 ticket。
        """
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DiffTicket, 'result', held)
    (command_root / 'edit.ts').write_bytes(FIRST.encode())
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_edit(client, headers, rid, wid)
        sent = await send_command(client, headers, cid, '[EDIT_FAKE]')
        await asyncio.wait_for(reached.wait(), 5)
        await client.post(f'/api/conversations/{cid}/stop', headers=headers)
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        call = next(part for part in message['parts_json'] if part.get('tool_name') == 'workspace_edit')
        detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
        assert detail['write']['files'][0]['applied'] is True
        assert detail['write']['reason'] == 'cancelled' and current_pool().retained_bytes == 0
        assert (command_root / 'edit.ts').read_text() == FIRST.replace('第一版', '第二版🙂')


@pytest.mark.anyio
async def test_edit_large_file_small_fragment_preserves_diff_budget(command_root):
    """大文件小改可用，但不偷偷提高 D 的全文件 diff 预算。

    Args:
        command_root：独立目录。
    """
    from app.workspaces.files import WorkspaceFileService
    from app.agent.write_capture import WriteReceipt
    from app.workspaces.diffs import current_pool
    old = 'a' * (130 * 1024) + 'unique'
    target = command_root / 'large.txt'
    target.write_bytes(old.encode())
    service = WorkspaceFileService(root=command_root, execution_id='large-edit')
    read = await service.read(target.name, max_bytes=16)
    receipt = WriteReceipt(target.name)
    result = await service.edit(target.name, 'unique', 'changed', expected_sha256=read.sha256, capture_applied=receipt.applied)
    await receipt.finish(service.json_result(result))
    assert target.read_text() == old[:-6] + 'changed'
    assert receipt.value['reason'] == 'input_budget' and receipt.value['files'][0]['applied'] is True
    assert current_pool().retained_bytes == 0


@pytest.mark.anyio
async def test_edit_schema_and_independent_capability_are_enforced(command_root, isolated_command_database):
    """缺少 hash、额外参数和撤销能力由服务端拒绝，edit 不隐含 write。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
    """
    from datetime import datetime, timezone
    from app.db import SessionLocal
    from app.models import AgentExecution, Generation, Role, Conversation
    from app.workspaces.tools import create_workspace_tools, workspace_tool_policy
    (command_root / 'edit.ts').write_bytes(FIRST.encode())
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_edit(client, headers, rid, wid)
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            generation = Generation(conversation_id=cid, status='running', stream_epoch='edit-test')
            session.add(generation)
            await session.flush()
            execution = AgentExecution(execution_id=command_root.name, conversation_id=cid, generation_id=generation.id,
                role_id=rid, chain_id='edit-test', execution_kind='single', status='running', created_at=datetime.now(timezone.utc))
            session.add(execution)
            await session.commit()
            tools = {tool.name: tool for tool in await create_workspace_tools(session, execution_id=execution.execution_id,
                conversation_id=cid, role=role, triggered_by_user_id=role.created_by, allow_dangerous=True)}
            assert set(tools) == {'workspace_read', 'workspace_edit'}
            assert await create_workspace_tools(session, execution_id=execution.execution_id, conversation_id=cid,
                role=role, triggered_by_user_id=role.created_by, allow_dangerous=False) == []
        args = {'path': 'edit.ts', 'old_text': '第一版', 'new_text': '第二版🙂', 'expected_sha256': hashlib.sha256(FIRST.encode()).hexdigest()}
        for invalid in [{k: v for k, v in args.items() if k != 'expected_sha256'}, {**args, 'old_text': ''},
                        {**args, 'old_text': {'private': 'not-to-log'}}, {**args, 'replace_all': True}]:
            output = await tools['workspace_edit'].ainvoke(invalid)
            assert output.startswith('[工具被拒绝]') and 'WORKSPACE_EDIT_ARGUMENT_INVALID' in output
            assert 'not-to-log' not in output and '第一版' not in output
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': False})
        assert 'WORKSPACE_TOOL_CAPABILITY_CHANGED' in await tools['workspace_edit'].ainvoke(args)
        assert (command_root / 'edit.ts').read_text() == FIRST
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})
        async with SessionLocal() as session:
            role = await session.get(Role, rid)
            conversation = await session.get(Conversation, cid)
            for enabled in [True, False]:
                role.builtin_tools_json = ['workspace_write', *(['workspace_edit'] if enabled else [])]
                await session.commit()
                policy = await workspace_tool_policy(session, conversation=conversation, role=role, triggered_by_user_id=role.created_by)
                description = next(item['description'] for item in policy['exposed_tools'] if item['name'] == 'workspace_write')
                assert ('workspace_edit' in description) is enabled
        assert 'WORKSPACE_TOOL_CAPABILITY_CHANGED' in await tools['workspace_edit'].ainvoke(args)


@pytest.mark.anyio
async def test_edit_diff_persistence_failure_does_not_repeat_edit(command_root, isolated_command_database, monkeypatch):
    """共用差异保存降级：编辑已成功不能因私有详情失败而向模型报失败。

    Args:
        command_root：独立目录。
        isolated_command_database：新库。
        monkeypatch：只阻断差异正文加密，不改变实际文件操作。
    """
    from app.services import tool_details
    original = tool_details._encrypt

    def fail_body(message_id, call_id, capture):
        """Args:
            message_id：消息身份。
            call_id：调用身份。
            capture：仅检查有无差异正文，不打印内容。
        """
        if capture.get('format') == 'write-v1' and any(file['hunks'] for file in capture['write']['files']):
            raise ValueError('controlled persistence failure')
        return original(message_id, call_id, capture)

    monkeypatch.setattr(tool_details, '_encrypt', fail_body)
    (command_root / 'edit.ts').write_bytes(FIRST.encode())
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_edit(client, headers, rid, wid)
        sent = await send_command(client, headers, cid, '[EDIT_FAKE]')
        message = await wait_reply(client, headers, cid, sent['message']['id'])
        calls = [part for part in message['parts_json'] if part.get('tool_name') == 'workspace_edit']
        assert len(calls) == 1 and calls[0]['status'] == 'success'
        value = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{calls[0]['call_id']}", headers=headers)).json()['write']
        assert value['availability'] == 'unavailable' and value['reason'] == 'capture_failed'
        assert value['files'][0]['applied'] is True
