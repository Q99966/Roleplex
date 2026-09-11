"""重启恢复需要实际回收证明，不能仅凭旧 PID 不存在释放服务。"""
import pytest
import sys
from test_workspace_commands import command_conversation, command_root, isolated_command_database


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux 内核出生身份')
def test_kernel_birth_does_not_depend_on_epoch_clock(monkeypatch):
    """墙钟变化不改变 Linux 进程出生身份。

    Args:
        monkeypatch：替换 epoch 派生时间，不修改真实系统时钟。
    """
    import os
    import psutil
    from app.workspaces.process_identity import birth_identity, same_birth
    before = birth_identity(os.getpid())
    monkeypatch.setattr(psutil.Process, 'create_time', lambda _self: 0)
    assert birth_identity(os.getpid()) == before
    assert same_birth(os.getpid(), before)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux 监管器持久凭据')
def test_receipt_is_bounded_private_and_bound_to_identity(tmp_path, monkeypatch):
    """错误令牌、资源、PID 或出生身份不能成为回收证明；正文不进入失败断言。

    Args:
        tmp_path：私有测试文件目录。
        monkeypatch：固定到本用例目录，不访问实际 World。
    """
    import hashlib
    import json
    import os
    import secrets
    from types import SimpleNamespace
    import uuid
    import psutil
    from app.runtime import receipts
    from app.workspaces.shell_supervisor import write_receipt
    from app.workspaces.process_identity import birth_identity
    monkeypatch.setattr(receipts, 'directory', lambda: tmp_path / 'receipts')
    identity = uuid.uuid4().hex
    path, token = receipts.prepare(identity)
    row = SimpleNamespace(id=identity, recovery_token_hash=hashlib.sha256(token).hexdigest(),
        pid=os.getpid(), birth=birth_identity(os.getpid()))
    write_receipt(str(path), identity, token, 143)
    assert receipts.read(row) is not None
    assert path.stat().st_mode & 0o077 == 0
    body = json.loads(path.read_text())
    for key, replacement in [('token', secrets.token_hex(32)), ('runtime_id', uuid.uuid4().hex),
        ('pid', -1), ('birth', 'wrong-birth')]:
        path.write_text(json.dumps({**body, key: replacement}))
        assert receipts.read(row) is None
    path.write_bytes(b'x' * 4097)
    assert receipts.read(row) is None
    path.write_text(json.dumps(body))
    assert receipts.discard(row)
    assert not path.exists()


@pytest.mark.anyio
async def test_missing_recovery_proof_stays_unknown(command_root, isolated_command_database):
    """同一内核启动期内，旧服务无回收证明且根 PID 消失仍必须保留未知状态。

    Args:
        command_root：独立测试目录。
        isolated_command_database：新库。
    """
    from app.runtime import registry
    from app.runtime.manager import manager
    async with command_conversation(command_root) as (_client, _headers, cid, rid, wid):
        row = await registry.reserve(owner_id=1, conversation_id=cid, workspace_id=wid,
            execution_id='missing-proof', role_id=rid, tool_call_id='legacy', tool_name='workspace_start_service', kind='service')
        await registry.change(row.id, state='running', pid=999999999, birth='old-birth')
        try:
            await manager.initialize()
            assert (await registry.get(row.id)).state == 'cleanup_required'
            assert (await registry.quota_view('conversation', cid))['used'] == 1
        finally:
            # 该行由测试伪造，未实际创建任何进程；只清理测试登记，不发送信号。
            await registry.finish(row.id, 'interrupted', verified=True)
