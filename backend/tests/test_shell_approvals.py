"""W1c 审批权限、执行前门槛与取消；仅访问本轮外部测试目录。"""
import asyncio

import pytest
from test_workspace_commands import command_conversation, command_root, isolated_command_database, send_command


@pytest.mark.anyio
async def test_pending_shell_requires_owner_digest_and_executes_only_once(command_root, isolated_command_database, monkeypatch):
    """审批前不创建文件，重复批准仅启动一次；Guest 和错摘要不能批准。

    Args:
        command_root：本轮独占测试目录。
        isolated_command_database：逐用例迁移的新数据库。
        monkeypatch：只统计真实运行器启动次数，不替换执行结果。
    """
    from accounts import TEST_PASSWORD, guest_username
    from app.workspaces import shell
    original = shell.run_process
    starts = []
    async def observe(*args, **kwargs):
        """统计进程入口，不保存脚本或输出。

        Args:
            args：运行器位置参数。
            kwargs：运行器命名参数。
        """
        starts.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(shell, 'run_process', observe)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        changed = await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'shell_enabled': True})
        assert changed.json()['shell_enabled'] is True
        roles = (await client.get('/api/roles', headers=headers)).json()
        role = next(row for row in roles if row['id'] == rid)
        assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_run_shell']})).status_code == 200
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        url = f'/api/conversations/{cid}/tool-approvals'
        rows = []
        for _ in range(100):
            response = await client.get(url, headers=headers)
            assert response.status_code == 200
            rows = response.json()
            if rows:
                break
            await asyncio.sleep(.02)
        assert len(rows) == 1
        approval = rows[0]
        assert not (command_root / 'shell-proof.txt').exists()
        assert starts == []
        guest = (await client.post('/api/auth/register', json={'username': guest_username('shell'), 'password': TEST_PASSWORD, 'nickname': 'Guest'})).json()
        assert (await client.get(url, headers={'Authorization': f"Bearer {guest['access_token']}"})).status_code == 403
        decision = f"{url}/{approval['id']}/decision"
        assert (await client.post(decision, headers={'Authorization': f"Bearer {guest['access_token']}"},
            json={'decision': 'approve', 'request_digest': approval['request_digest']})).status_code == 403
        assert (await client.post(decision, headers=headers, json={'decision': 'approve', 'request_digest': '0' * 64})).status_code == 409
        payload = {'decision': 'approve', 'request_digest': approval['request_digest']}
        results = await asyncio.gather(*[client.post(decision, headers=headers, json=payload) for _ in range(2)])
        assert all(result.status_code == 200 and result.json()['status'] == 'approved' for result in results)
        for _ in range(100):
            if (command_root / 'shell-proof.txt').exists():
                break
            await asyncio.sleep(.02)
        assert (command_root / 'shell-proof.txt').read_text() == 'approved\n'
        assert len(starts) == 1
        from app.db import SessionLocal
        from app.models import ToolApprovalRequest
        async with SessionLocal() as session:
            row = await session.get(ToolApprovalRequest, approval['id'])
            assert approval['script'] not in row.request_encrypted


@pytest.mark.anyio
async def test_reject_and_stop_pending_never_spawn(command_root, isolated_command_database):
    """拒绝和停止等待必须以无进程结果收口。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：全新数据库。
    """
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'shell_enabled': True})
        roles = (await client.get('/api/roles', headers=headers)).json()
        role = next(row for row in roles if row['id'] == rid)
        assert (await client.put(f'/api/roles/{rid}', headers=headers, json={**role, 'builtin_tools': ['workspace_run_shell']})).status_code == 200
        for action in ('reject', 'stop'):
            await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
            url = f'/api/conversations/{cid}/tool-approvals'
            for _ in range(100):
                response = await client.get(url, headers=headers)
                assert response.status_code == 200
                if response.json():
                    break
                await asyncio.sleep(.02)
            approval = response.json()[0]
            if action == 'reject':
                result = await client.post(f"{url}/{approval['id']}/decision", headers=headers,
                    json={'decision': 'reject', 'request_digest': approval['request_digest']})
                assert result.json()['status'] == 'rejected'
            else:
                await client.post(f'/api/conversations/{cid}/stop', headers=headers)
            for _ in range(100):
                history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
                if not history['active_generation_ids']:
                    break
                await asyncio.sleep(.02)
            assert not history['active_generation_ids']
            assert not (command_root / 'shell-proof.txt').exists()


async def enable_shell(client, headers, role_id, workspace_id):
    """用正常管理接口开启本轮角色和工作区开关。

    Args:
        client：真实 ASGI 客户端。
        headers：Owner 认证头。
        role_id：本用例角色。
        workspace_id：本用例工作区。
    """
    assert (await client.patch(f'/api/workspaces/{workspace_id}', headers=headers, json={'shell_enabled': True})).status_code == 200
    roles = (await client.get('/api/roles', headers=headers)).json()
    role = next(row for row in roles if row['id'] == role_id)
    assert (await client.put(f'/api/roles/{role_id}', headers=headers, json={**role, 'builtin_tools': ['workspace_run_shell']})).status_code == 200


async def pending(client, headers, cid):
    """等待实际 pending，而不是按固定时间假定审批已创建。

    Args:
        client：本轮客户端。
        headers：Owner 认证头。
        cid：会话身份。
    """
    for _ in range(100):
        result = await client.get(f'/api/conversations/{cid}/tool-approvals', headers=headers)
        if result.status_code == 200 and result.json():
            return result.json()[0]
        await asyncio.sleep(.02)
    raise AssertionError('未收到 pending 审批')


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['expired', 'restart', 'tamper', 'disabled'])
async def test_expiry_recovery_tamper_and_permission_revocation(command_root, isolated_command_database, monkeypatch, mode):
    """到期、启动恢复、密文替换和撤销能力均不能执行。

    Args:
        command_root：本轮外部空目录。
        isolated_command_database：每个参数用例使用独立迁移库。
        monkeypatch：仅缩短过期故障测试窗口。
        mode：当前拒绝或恢复路径。
    """
    from app.workspaces import approvals
    from app.db import SessionLocal
    from app.models import ToolApprovalRequest
    if mode == 'expired':
        monkeypatch.setattr(approvals, 'APPROVAL_SECONDS', .5)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        row = await pending(client, headers, cid)
        if mode == 'restart':
            await approvals.recover_approvals()
        elif mode == 'tamper':
            async with SessionLocal() as session:
                record = await session.get(ToolApprovalRequest, row['id'])
                record.request_encrypted = 'invalid-ciphertext-placeholder'
                await session.commit()
        elif mode == 'disabled':
            await client.patch(f'/api/workspaces/{wid}', headers=headers, json={'shell_enabled': False})
        if mode in {'tamper', 'disabled'}:
            result = await client.post(f"/api/conversations/{cid}/tool-approvals/{row['id']}/decision", headers=headers,
                json={'decision': 'approve', 'request_digest': row['request_digest']})
            assert result.status_code == 409
            await client.post(f'/api/conversations/{cid}/stop', headers=headers)
        for _ in range(100):
            history = (await client.get(f'/api/conversations/{cid}/messages', headers=headers)).json()
            if not history['active_generation_ids']:
                break
            await asyncio.sleep(.02)
        assert not history['active_generation_ids']
        async with SessionLocal() as session:
            record = await session.get(ToolApprovalRequest, row['id'])
            assert record.status == 'expired'
        assert not (command_root / 'shell-proof.txt').exists()


@pytest.mark.anyio
async def test_approved_but_interrupted_request_is_not_replayed_on_startup(command_root, isolated_command_database, monkeypatch):
    """批准后尚未启动的崩溃窗口，重启不能猜测执行或重新消费批准。

    Args:
        command_root：外部测试目录。
        isolated_command_database：新数据库。
        monkeypatch：在进程启动交接之前受控暂停。
    """
    from app.workspaces import approvals
    from app.main import app
    entered = asyncio.Event()
    async def held(_request):
        """模拟批准后、spawn 前的受控中断。

        Args:
            _request：不会执行的审批上下文。
        """
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(approvals, 'run_shell', held)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        row = await pending(client, headers, cid)
        result = await client.post(f"/api/conversations/{cid}/tool-approvals/{row['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': row['request_digest']})
        assert result.json()['status'] == 'approved'
        await asyncio.wait_for(entered.wait(), 2)
    entered.clear()
    async with app.router.lifespan_context(app):
        assert not entered.is_set()
        assert not (command_root / 'shell-proof.txt').exists()


@pytest.mark.anyio
async def test_shell_uses_stdin_minimal_environment_and_process_limits(command_root, isolated_command_database, monkeypatch):
    """Shell 复用真实运行器，验证 no-profile、环境隔离、截断和超时。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：隔离设置来源。
        monkeypatch：注入不得继承的占位环境。
    """
    from app.workspaces.shell import shell_configuration, run_shell, validate_script
    from app.workspaces.commands import WorkspaceCommandError
    from app.agent.tools import classify_tool
    from app.workspaces.tools import WorkspaceShellInput
    assert classify_tool('workspace_run_shell', owner_safe_overrides=['workspace_run_shell']) == 'dangerous'
    assert set(WorkspaceShellInput.model_json_schema()['properties']) == {'script'}
    for invalid in ['', '   ', 'x\0', '中' * 30000]:
        with pytest.raises(WorkspaceCommandError):
            validate_script(invalid)
    config = shell_configuration()
    if config['shell_kind'] != 'bash':
        pytest.skip('当前用例的 POSIX 脚本只在 Bash 下执行')
    monkeypatch.setenv('ROLEPLEX_TEST_CREDENTIAL', 'sensitive-placeholder')
    monkeypatch.setenv('BASH_ENV', str(command_root / 'must-not-load.sh'))
    (command_root / 'must-not-load.sh').write_text('echo PROFILE_LOADED\n')
    request = {**config, 'root_path': str(command_root), 'script': 'printf "%s" "${ROLEPLEX_TEST_CREDENTIAL-unset}"', 'output_bytes': 128}
    result = await run_shell(request)
    assert result['stdout'] == 'unset'
    assert config['argv'][1:] == ['--noprofile', '--norc', '-s']
    result = await run_shell({**request, 'script': 'printf "%1000s" x', 'output_bytes': 16})
    assert result['truncated'] and len(result['stdout'].encode()) <= 16
    result = await run_shell({**request, 'script': 'sleep 10', 'timeout_seconds': .05})
    assert result['error_code'] == 'COMMAND_TIMEOUT'


@pytest.mark.anyio
async def test_decision_conflicts_and_cross_conversation_are_safe(command_root, isolated_command_database):
    """批准与拒绝竞争只有一个决定，审批 ID 不能跨会话使用。

    Args:
        command_root：本轮独占目录。
        isolated_command_database：独立迁移的新库。
    """
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        other = (await client.post('/api/conversations', headers=headers, json={'title': '另一个会话', 'type': 'single', 'role_ids': [rid]})).json()['id']
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        row = await pending(client, headers, cid)
        payload = {'decision': 'approve', 'request_digest': row['request_digest']}
        wrong = await client.post(f"/api/conversations/{other}/tool-approvals/{row['id']}/decision", headers=headers, json=payload)
        assert wrong.status_code == 404 and wrong.json()['error']['code'] == 'SHELL_APPROVAL_NOT_FOUND'
        url = f"/api/conversations/{cid}/tool-approvals/{row['id']}/decision"
        results = await asyncio.gather(*[client.post(url, headers=headers, json={**payload, 'decision': action}) for action in ('approve', 'reject')])
        assert all(result.status_code == 200 for result in results)
        assert len({result.json()['status'] for result in results}) == 1


@pytest.mark.anyio
async def test_shell_platform_selection_does_not_expose_executable(command_root, isolated_command_database, monkeypatch):
    """无可执行文件时关闭能力，PowerShell argv/stdin 只由宿主生成。

    Args:
        command_root：安全测试 cwd。
        isolated_command_database：隔离设置。
        monkeypatch：替换本机解析与进程入口，不冒充 Windows 实机验证。
    """
    import sys
    from app.config import settings
    from app.workspaces import shell
    from app.workspaces.commands import WorkspaceCommandError
    monkeypatch.setattr(shell.shutil, 'which', lambda _name: None)
    with pytest.raises(WorkspaceCommandError, match='SHELL_NOT_SUPPORTED'):
        shell.shell_configuration()
    monkeypatch.setattr(settings, 'workspace_shell_kind', 'powershell')
    monkeypatch.setattr(shell.shutil, 'which', lambda _name: sys.executable)
    config = shell.shell_configuration()
    assert config['argv'][1:] == ['-NoProfile', '-NonInteractive', '-Command', '-']
    seen = []
    async def process(argv, **kwargs):
        """仅验证宿主参数，不启动假冒的 PowerShell。

        Args:
            argv：固定入口和无 profile 参数。
            kwargs：含 stdin/cwd 的运行器参数。
        """
        seen.append((argv, kwargs))
        return {'status': 'exited'}
    monkeypatch.setattr(shell, 'run_process', process)
    await shell.run_shell({**config, 'root_path': str(command_root), 'script': 'Write-Output "占位"'})
    assert 'Write-Output' not in ' '.join(seen[0][0])
    assert seen[0][1]['payload'].decode('utf-8').endswith('Write-Output "占位"\n')


@pytest.mark.anyio
@pytest.mark.parametrize('cancel', [False, True])
async def test_linux_shell_reaps_detached_descendants(command_root, isolated_command_database, cancel):
    """正常结束或取消均回收 setsid 后代，不只清理原进程组。

    Args:
        command_root：本轮独占目录。
        isolated_command_database：隔离设置来源。
        cancel：是否在 Shell 等待期间取消。
    """
    import sys
    import shlex
    import psutil
    from app.workspaces.shell import shell_configuration, run_shell
    if sys.platform != 'linux':
        pytest.skip('Linux subreaper 专项；Windows 使用 Job Object')
    child = 'import os,time; os.setsid(); open("detached.pid","w").write(str(os.getpid())); time.sleep(30)'
    script = f'{shlex.quote(sys.executable)} -c {shlex.quote(child)} &\nwhile [ ! -s detached.pid ]; do sleep .01; done\n'
    if cancel:
        script += 'wait\n'
    task = asyncio.create_task(run_shell({**shell_configuration(), 'root_path': str(command_root), 'script': script}))
    pid_file = command_root / 'detached.pid'
    for _ in range(200):
        if pid_file.exists() and pid_file.stat().st_size:
            break
        await asyncio.sleep(.01)
    assert pid_file.exists()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task)['exit_code'] == 0
    pid = int(pid_file.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


@pytest.mark.anyio
async def test_late_authorization_cannot_approve_after_deadline(command_root, isolated_command_database, monkeypatch):
    """授权查询跨过截止时间时，不能沿用请求到达时间批准。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：全新数据库。
        monkeypatch：可控地延迟授权和独立的到期唤醒。
    """
    from app.workspaces import approvals
    original_authorized = approvals._authorized
    original_resolve = approvals.resolve_approval
    checks = 0
    async def authorize(request):
        """延迟决定阶段的复核。

        Args:
            request：冻结上下文，不记录内容。
        """
        nonlocal checks
        checks += 1
        if checks == 2:
            await asyncio.sleep(.4)
        return await original_authorized(request)
    async def resolve(*args, **kwargs):
        """模拟定时到期任务暂时尚未得到执行机会。

        Args:
            args：审批身份。
            kwargs：决定和安全原因。
        """
        if kwargs.get('reason') == 'expired':
            await asyncio.sleep(.3)
        return await original_resolve(*args, **kwargs)
    monkeypatch.setattr(approvals, 'APPROVAL_SECONDS', .3)
    monkeypatch.setattr(approvals, '_authorized', authorize)
    monkeypatch.setattr(approvals, 'resolve_approval', resolve)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        row = await pending(client, headers, cid)
        result = await client.post(f"/api/conversations/{cid}/tool-approvals/{row['id']}/decision", headers=headers,
            json={'decision': 'approve', 'request_digest': row['request_digest']})
        assert result.json()['status'] == 'expired'
        assert not (command_root / 'shell-proof.txt').exists()


@pytest.mark.anyio
async def test_cancel_during_committed_request_handoff_closes_pending(command_root, isolated_command_database, monkeypatch):
    """提交已经成功但 ID 尚未交接时取消，也不能留下可批准的孤儿 pending。

    Args:
        command_root：本轮外部目录。
        isolated_command_database：新数据库。
        monkeypatch：在审批提交成功后、宿主拿到句柄之前暂停。
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.db import SessionLocal
    from app.models import ToolApprovalRequest, Generation
    original = AsyncSession.commit
    committed, release = asyncio.Event(), asyncio.Event()
    async def commit(session):
        """只暂停首次 pending 提交的返回。

        Args:
            session：被测短事务。
        """
        is_request = any(isinstance(row, ToolApprovalRequest) and row.status == 'pending' for row in session.identity_map.values())
        await original(session)
        if is_request and not committed.is_set():
            committed.set()
            await release.wait()
    monkeypatch.setattr(AsyncSession, 'commit', commit)
    async with command_conversation(command_root) as (client, headers, cid, rid, wid):
        await enable_shell(client, headers, rid, wid)
        sent = await send_command(client, headers, cid, '[SHELL_APPROVAL_FAKE]')
        await asyncio.wait_for(committed.wait(), 2)
        stopping = asyncio.create_task(client.post(f'/api/conversations/{cid}/stop', headers=headers))
        try:
            for _ in range(100):
                async with SessionLocal() as session:
                    generation = await session.get(Generation, sent['generation_id'])
                    if generation.stop_requested_at is not None:
                        break
                await asyncio.sleep(.01)
        finally:
            release.set()
        assert (await stopping).status_code == 202
        # 202 只代表停止已受理，等待后台收尾事实，而不是假定 REST 返回即完成。
        for _ in range(100):
            async with SessionLocal() as session:
                row = await session.scalar(select(ToolApprovalRequest))
                if row.status == 'expired':
                    break
            await asyncio.sleep(.01)
        assert row.status == 'expired'
        assert not (command_root / 'shell-proof.txt').exists()
