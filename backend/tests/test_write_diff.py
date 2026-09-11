"""D 写入差异：可信前后版本、有界计算及非执行内容展示。"""
import asyncio
import hashlib
import json

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command, wait_command_messages


async def enable_write(client, headers, rid, wid):
    """经实际 API 启用本轮文件能力。

    Args:
        client：本轮客户端。
        headers：Owner 认证头。
        rid：角色身份。
        wid：工作区身份。
    """
    role = next(row for row in (await client.get('/api/roles', headers=headers)).json() if row['id'] == rid)
    assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_write']})).status_code == 200
    assert (await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'file_tools_enabled': True})).status_code == 200


@pytest.mark.anyio
async def test_diff_records_unicode_and_exact_versions():
    """新建、修改和空文件有精确身份，结果对象受字节上限约束。"""
    from app.workspaces.diffs import DiffPool, change_metadata
    pool = DiffPool()
    old, new = '甲\r\n旧行'.encode(), '甲\n新行🙂\n'.encode()
    ticket = pool.submit(old, new, change_metadata('sample.txt', old, new))
    result = await ticket.result()
    assert result['availability'] == 'recorded'
    file = result['files'][0]
    assert file['before_sha256'] == hashlib.sha256(old).hexdigest()
    assert file['after_sha256'] == hashlib.sha256(new).hexdigest()
    assert file['added'] == file['removed'] == 2
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 65536
    empty = await pool.submit(None, b'', change_metadata('empty.txt', None, b'')).result()
    assert empty['files'][0]['operation'] == 'created'
    assert empty['files'][0]['added'] == 0
    await pool.close()


@pytest.mark.anyio
async def test_diff_budget_does_not_start_a_worker():
    """130 KiB 文件的小改超出合计输入预算，不能伪装为空差异。"""
    from app.workspaces.diffs import DiffPool, change_metadata
    pool = DiffPool()
    old, new = b'a' * (130 * 1024), b'b' * (130 * 1024)
    result = await pool.submit(old, new, change_metadata('large.txt', old, new)).result()
    assert result['availability'] == 'unavailable' and result['reason'] == 'input_budget'
    assert result['files'][0]['applied'] is True and result['files'][0]['added'] is None
    assert pool.active == 0 and pool.waiting == 0
    await pool.close()


@pytest.mark.anyio
async def test_diff_bounded_admission_cancel_and_shutdown():
    """最多两计算四等待；取消或关闭释放所有输入、名额与子进程。"""
    from app.workspaces.diffs import DiffPool, change_metadata
    pool = DiffPool()
    old, new = b'same\n' * 4900, b'other\n' + b'same\n' * 4899
    tickets = [pool.submit(old, new, change_metadata('a.txt', old, new)) for _ in range(7)]
    assert pool.active == 2 and pool.waiting == 4
    assert (await tickets[-1].result())['reason'] == 'queue_full'
    tasks = [asyncio.create_task(ticket.result()) for ticket in tickets[:6]]
    await asyncio.sleep(.02)
    tasks[0].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await pool.close()
    assert pool.active == 0 and pool.waiting == 0 and pool.retained_bytes == 0
    assert not pool.processes


def test_worker_limits_lines_and_marks_partial():
    """巨长行不会截坏 JSON，未保留正文仍明确部分显示。"""
    from app.workspaces.diff_worker import build_diff
    result = build_diff({'version': 1, 'availability': 'recorded', 'reason': None,
        'files': [{'path': 'long.txt', 'hunks': [], 'added': None, 'removed': None}]}, 'a' * 80000, 'b' * 80000)
    assert result['availability'] == 'partial'
    assert result['files'][0]['added'] == 1
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 65536


@pytest.mark.anyio
async def test_write_diff_is_private_persistent_and_not_rebuilt(command_root, isolated_command_database):
    """两次真实文件写入的差异与消息位置绑定，刷新不读现文件，Guest 不获得详情。

    Args:
        command_root：本轮外部工作区。
        isolated_command_database：全新迁移库。
    """
    from sqlalchemy import select
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.models import ToolExecutionDetail, ConversationMember, EventLog
    from accounts import TEST_PASSWORD, guest_username
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_write(client, headers, rid, wid)
        await send_command(client, headers, cid, '[WRITE_DIFF_FAKE]')
        message = await wait_command_messages(client, headers, cid)
        assert [part['type'] for part in message['parts_json']] == ['text', 'tool_call', 'text', 'tool_call', 'text']
        calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert all(part['status'] == 'success' for part in calls)
        route = f"/api/conversations/{cid}/messages/{message['id']}/tools/{calls[1]['call_id']}"
        response = await client.get(route, headers=headers)
        assert response.headers['cache-control'] == 'no-store'
        detail = response.json()
        assert detail['write']['availability'] == 'recorded'
        file = detail['write']['files'][0]
        assert file['operation'] == 'modified' and file['added'] == file['removed'] == 1
        assert any(line['kind'] == 'delete' and '第一版' in line['text'] for hunk in file['hunks'] for line in hunk['lines'])
        assert set(json.loads(detail['output']['text'])) == {'created', 'bytes', 'sha256'}
        (command_root / 'demo.ts').write_text('人工修改', encoding='utf-8')
        assert (await client.get(route, headers=headers)).json()['write'] == detail['write']
        guest = (await client.post('/api/auth/register', json={'username': guest_username('diff'), 'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
        guest_headers = {'Authorization': f"Bearer {guest['access_token']}"}
        async with SessionLocal() as session:
            session.add(ConversationMember(conversation_id=cid, member_type='user', member_id=guest['user']['id'], joined_at=datetime.now(timezone.utc)))
            rows = list((await session.scalars(select(ToolExecutionDetail).where(ToolExecutionDetail.message_id == message['id']).order_by(ToolExecutionDetail.id))).all())
            assert all('第一版' not in (row.output_encrypted or '') for row in rows)
            for event in (await session.scalars(select(EventLog).where(EventLog.conversation_id == cid))).all():
                assert 'before_sha256' not in str(event.payload_json) and '第一版' not in str(event.payload_json)
            original = rows[1].output_encrypted
            rows[1].output_encrypted = rows[0].output_encrypted
            await session.commit()
        assert (await client.get(route, headers=headers)).json()['availability'] == 'unavailable'
        assert (await client.get(route, headers=guest_headers)).status_code == 403
        assert 'before_sha256' not in (await client.get(f'/api/conversations/{cid}/messages', headers=guest_headers)).text
        async with SessionLocal() as session:
            row = await session.get(ToolExecutionDetail, rows[1].id)
            row.output_encrypted = original
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        expired = (await client.get(route, headers=headers)).json()
        assert expired['availability'] == 'expired' and expired.get('write') is None


@pytest.mark.anyio
async def test_cancel_after_write_preserves_applied_fact(command_root, isolated_command_database, monkeypatch):
    """写入已完成但 diff 尚在等待时取消，不丢失已应用事实，不启动下一次写入。

    Args:
        command_root：独立工作区。
        isolated_command_database：全新迁移库。
        monkeypatch：只暂停差异计算，文件和生成链路真实执行。
    """
    from app.workspaces.diffs import DiffTicket, current_pool
    reached = asyncio.Event()

    async def held(ticket):
        """Args:
            ticket：已在文件提交后预留的差异计算。
        """
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DiffTicket, 'result', held)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_write(client, headers, rid, wid)
        await send_command(client, headers, cid, '[WRITE_DIFF_FAKE]')
        await asyncio.wait_for(reached.wait(), 5)
        assert '第一版' in (command_root / 'demo.ts').read_text(encoding='utf-8')
        await client.post(f'/api/conversations/{cid}/stop', headers=headers)
        message = await wait_command_messages(client, headers, cid)
        call = next(part for part in message['parts_json'] if part['type'] == 'tool_call')
        detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{call['call_id']}", headers=headers)).json()
        assert detail['write']['files'][0]['applied'] is True
        assert detail['write']['reason'] == 'cancelled'
        assert current_pool().retained_bytes == 0


@pytest.mark.anyio
async def test_capture_failure_does_not_repeat_or_fail_write(command_root, isolated_command_database, monkeypatch):
    """计算基础设施故障不改变模型可见写入结果，不诱发重复写入。

    Args:
        command_root：本轮工作区。
        isolated_command_database：全新迁移库。
        monkeypatch：只替换差异计算为确定性故障。
    """
    from app.workspaces.diffs import DiffTicket

    async def failed(_ticket):
        """Args:
            _ticket：模拟无法完成的技术计算。
        """
        raise RuntimeError('controlled capture failure')

    monkeypatch.setattr(DiffTicket, 'result', failed)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_write(client, headers, rid, wid)
        await send_command(client, headers, cid, '[WRITE_DIFF_FAKE]')
        message = await wait_command_messages(client, headers, cid)
        calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert len(calls) == 2 and all(call['status'] == 'success' for call in calls)
        detail = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{calls[1]['call_id']}", headers=headers)).json()
        assert detail['write']['availability'] == 'unavailable'
        assert detail['write']['reason'] == 'capture_failed'
        assert detail['write']['files'][0]['applied'] is True
        assert '第二版' in (command_root / 'demo.ts').read_text(encoding='utf-8')


@pytest.mark.anyio
async def test_write_observer_failure_cannot_change_committed_result(tmp_path):
    """即使观察器自身异常，文件服务也不把成功提交改判为失败。

    Args:
        tmp_path：无用户数据的临时目录。
    """
    from app.workspaces.files import WorkspaceFileService
    service = WorkspaceFileService(root=tmp_path, execution_id='observer')

    def failed(_before, _after):
        """Args:
            _before：旧字节。
            _after：已提交新字节。
        """
        raise ValueError('controlled observer failure')

    result = await service.write('new.txt', 'once', capture_applied=failed)
    assert result.created and (tmp_path / 'new.txt').read_text() == 'once'


@pytest.mark.anyio
async def test_unconfirmed_worker_cleanup_blocks_new_admission():
    """无法证明计算进程回收时关闭准入，不能释放容量后继续创建更多进程。"""
    from app.workspaces.diffs import DiffPool, change_metadata

    class Unreaped:
        """只模拟 OS 拒绝回收，不启动实际进程。"""
        returncode = None

        def kill(self):
            """模拟句柄不可回收。"""
            raise PermissionError('controlled cleanup failure')

    pool = DiffPool()
    process = Unreaped()
    pool.processes.add(process)
    with pytest.raises(RuntimeError, match='not reaped'):
        await pool.close()
    assert process in pool.processes
    rejected = await pool.submit(None, b'a', change_metadata('a.txt', None, b'a')).result()
    assert rejected['reason'] == 'shutdown' and pool.active == 0


@pytest.mark.anyio
async def test_diff_encryption_failure_keeps_write_success(command_root, isolated_command_database, monkeypatch):
    """差异正文加密保存失败时保留提交元数据，不能向模型返回失败诱发重写。

    Args:
        command_root：独立工作区。
        isolated_command_database：全新迁移库。
        monkeypatch：只令包含差异正文的加密失败。
    """
    from app.services import tool_details
    original = tool_details._encrypt

    def failed_diff(message_id, call_id, capture):
        """Args:
            message_id：所属消息。
            call_id：所属调用。
            capture：只检查格式和有无正文，不打印内容。
        """
        if capture.get('format') == 'write-v1' and any(file['hunks'] for file in capture['write']['files']):
            raise ValueError('controlled encryption failure')
        return original(message_id, call_id, capture)

    monkeypatch.setattr(tool_details, '_encrypt', failed_diff)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_write(client, headers, rid, wid)
        await send_command(client, headers, cid, '[WRITE_DIFF_FAKE]')
        message = await wait_command_messages(client, headers, cid)
        calls = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert len(calls) == 2 and all(call['status'] == 'success' for call in calls)
        value = (await client.get(f"/api/conversations/{cid}/messages/{message['id']}/tools/{calls[1]['call_id']}", headers=headers)).json()['write']
        assert value['availability'] == 'unavailable' and value['reason'] == 'capture_failed'
        assert value['files'][0]['applied'] is True and value['files'][0]['hunks'] == []


@pytest.mark.anyio
async def test_expired_queue_item_cannot_start_after_promotion():
    """已等待超时的项目即使刚获空槽也必须降级，而不是重置等待期限。"""
    from app.workspaces.diffs import DiffPool, change_metadata
    pool = DiffPool()
    first, second, queued = [pool.submit(None, b'a', change_metadata('a.txt', None, b'a')) for _ in range(3)]
    queued.enqueued_at -= 1
    first.release()
    assert (await queued.result())['reason'] == 'queue_timeout'
    second.release()
    await pool.close()
    assert pool.retained_bytes == 0 and not pool.processes


@pytest.mark.anyio
async def test_long_capture_path_is_not_truncated_into_false_identity():
    """无法保留完整路径时明确降级，不能用截断路径指向另一文件。"""
    from app.agent.write_capture import WriteReceipt
    receipt = WriteReceipt('a' * 5000)
    receipt.applied(None, b'value')
    assert receipt.value['availability'] == 'unavailable'
    assert receipt.value['files'] == [] and receipt.ticket is None
