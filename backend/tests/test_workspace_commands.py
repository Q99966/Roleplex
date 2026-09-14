"""W1b 真实子进程、参数边界、输出和取消回收测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import pytest


@pytest.fixture
async def isolated_command_database(tmp_path):
    """为停止/落库竞态提供逐用例迁移的新库，避免共享单例状态。

    Args:
        tmp_path：该用例独占的临时目录。
    """
    previous_url = os.environ['DATABASE_URL']
    os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{tmp_path / ('command-' + os.environ['ROLEPLEX_TEST_STAMP'] + '.db')}"
    for name in [key for key in sys.modules if key == 'app' or key.startswith('app.')]:
        del sys.modules[name]
    try:
        yield
    finally:
        from app.db import engine
        await engine.dispose()
        os.environ['DATABASE_URL'] = previous_url
        for name in [key for key in sys.modules if key == 'app' or key.startswith('app.')]:
            del sys.modules[name]


@asynccontextmanager
async def command_conversation(root: Path):
    """通过真实 REST 登记命令工作区、角色和 single 会话。

    Args:
        root：本轮独立外部目录。
    """
    from httpx import AsyncClient, ASGITransport
    from accounts import ensure_owner_async, stable_auth_clock
    from app.main import app
    async with app.router.lifespan_context(app):
        with stable_auth_clock():
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                owner = await ensure_owner_async(client)
                headers = {'Authorization': f"Bearer {owner['access_token']}"}

                async def diagnose_owner_auth(response):
                    """仅在本轮 Owner 的 401 中记录安全分类，不记录 Token 或签名密钥。

                    Args:
                        response：测试客户端刚收到的 HTTP 响应。
                    """
                    if response.status_code != 401 or response.request.headers.get('authorization') != headers['Authorization']:
                        return
                    import jwt
                    from datetime import datetime, timezone
                    from app.config import settings
                    await response.aread()
                    failure_kind = 'claims_valid'
                    delta = None
                    try:
                        jwt.decode(owner['access_token'], settings.resolved_jwt_secret(), algorithms=[settings.jwt_algorithm])
                    except jwt.PyJWTError as exc:
                        failure_kind = type(exc).__name__
                        claims = jwt.decode(owner['access_token'], options={'verify_signature': False})
                        if isinstance(claims.get('iat'), (int, float)):
                            delta = round((datetime.now(timezone.utc).timestamp() - claims['iat']) * 1000)
                    print('Owner fixture 401:', response.json().get('error', {}).get('code'), failure_kind, 'iat_age_ms=', delta)

                client.event_hooks['response'].append(diagnose_owner_auth)
                config = await client.post('/api/model-configs', headers=headers, json={
                    'name': root.name, 'provider_type': 'openai_compatible', 'api_key': 'sk-placeholder',
                })
                role = await client.post('/api/roles', headers=headers, json={
                    'name': root.name, 'model_config_id': config.json()['id'], 'model_name': 'fake-model',
                    'system_prompt': '使用受控命令完成测试。',
                    'builtin_tools': ['workspace_run_command'],
                })
                assert role.status_code == 201
                workspace = await client.post('/api/workspaces', headers=headers, json={
                    'display_name': root.name, 'root_path': str(root), 'acknowledge_existing_content': True,
                })
                assert workspace.status_code == 201
                binding_id = workspace.json()['id']
                enabled = await client.patch(f'/api/workspaces/{binding_id}', headers=headers, json={
                    'basic_commands_enabled': True,
                })
                # 准备阶段失败只报告固定状态/错误码，不能用缺字段掩盖 401 或打印凭据响应。
                assert enabled.status_code == 200, (
                    enabled.status_code, enabled.json().get('error', {}).get('code'))
                assert enabled.json()['basic_commands_enabled'] is True
                assert enabled.json()['file_tools_enabled'] is False
                conversation = await client.post('/api/conversations', headers=headers, json={
                    'type': 'single', 'title': '命令测试', 'role_ids': [role.json()['id']], 'workspace_binding_id': binding_id,
                })
                assert conversation.status_code == 201
                yield client, headers, conversation.json()['id'], role.json()['id'], binding_id


async def send_command(client, headers, conversation_id, prompt):
    """发送 fake 工具脚本并返回生成响应。

    Args:
        client：HTTP 测试客户端。
        headers：Owner 认证头。
        conversation_id：本轮会话 ID。
        prompt：确定性的 fake 脚本标记。
    """
    sent = await client.post(f'/api/conversations/{conversation_id}/messages', headers=headers,
                             json={'parts': [{'type': 'text', 'text': prompt}]})
    assert sent.status_code == 202
    return sent.json()


async def wait_command_messages(client, headers, conversation_id):
    """等待生成与工具卡完成落库。

    Args:
        client：HTTP 测试客户端。
        headers：Owner 认证头。
        conversation_id：本轮会话 ID。
    """
    for _ in range(300):
        result = (await client.get(f'/api/conversations/{conversation_id}/messages', headers=headers)).json()
        roles = [item for item in result['items'] if item['sender_type'] == 'role']
        if roles and roles[-1]['status'] in {'done', 'error', 'stopped'} and not result['active_generation_ids']:
            return roles[-1]
        await asyncio.sleep(0.03)
    raise AssertionError('命令生成未收口')


@pytest.mark.anyio
async def test_command_tool_loop_exposes_safe_cards_and_audit(command_root):
    """文件开关关闭仍可单独启用命令，工具卡/审计不保存 cwd 和正文。

    Args:
        command_root：本轮独立工作区。
    """
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall, ExecutionWorkspace
    (command_root / 'hello.txt').write_text('bounded-placeholder-content', encoding='utf-8')
    async with command_conversation(command_root) as (client, headers, conversation_id, _, binding_id):
        await send_command(client, headers, conversation_id, '[W1B_FAKE_E2E]')
        message = await wait_command_messages(client, headers, conversation_id)
        cards = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert [part.get('command') for part in cards] == ['pwd', 'list', 'read', 'count']
        assert all(part['status'] == 'success' and part['exit_code'] == 0 for part in cards)
        assert str(command_root) not in json.dumps(cards)
        assert 'bounded-placeholder-content' not in json.dumps(cards)
        async with SessionLocal() as session:
            calls = (await session.scalars(select(ToolCall).where(ToolCall.conversation_id == conversation_id))).all()
            leases = (await session.scalars(select(ExecutionWorkspace).where(
                ExecutionWorkspace.workspace_binding_id == binding_id))).all()
            assert len(calls) == 4 and all(call.status == 'ok' for call in calls)
            assert all(set(json.loads(call.args_summary)) == {'command'} for call in calls)
            assert len(leases) == 1 and leases[0].status == 'retained'


@pytest.mark.anyio
async def test_existing_command_tool_rechecks_all_authorization(command_root):
    """已创建工具在每次调用前复核主体、角色、会话、能力、租约和停止状态。

    Args:
        command_root：本轮独立工作区。
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import AgentExecution, Conversation, ExecutionWorkspace, Generation, Role, User, WorkspaceBinding
    from app.workspaces.tools import create_workspace_tools, workspace_tool_policy
    from accounts import TEST_PASSWORD, guest_username
    async with command_conversation(command_root) as (client, headers, conversation_id, role_id, binding_id):
        guest = await client.post('/api/auth/register', json={
            'username': guest_username('w1b'), 'password': TEST_PASSWORD, 'nickname': '命令 Guest',
        })
        denied = await client.patch(f'/api/workspaces/{binding_id}',
            headers={'Authorization': f"Bearer {guest.json()['access_token']}"}, json={'basic_commands_enabled': True})
        assert denied.status_code == 403
        async with SessionLocal() as session:
            role = await session.get(Role, role_id)
            conversation = await session.get(Conversation, conversation_id)
            owner_id = role.created_by
            policy = await workspace_tool_policy(session, conversation=conversation, role=role, triggered_by_user_id=owner_id)
            assert [tool['name'] for tool in policy['exposed_tools']] == ['workspace_run_command']
            generation = Generation(conversation_id=conversation_id, status='running', stream_epoch='test')
            session.add(generation)
            await session.flush()
            execution = AgentExecution(execution_id=command_root.name, conversation_id=conversation_id,
                generation_id=generation.id, role_id=role_id, chain_id='test', execution_kind='single',
                status='running', created_at=datetime.now(timezone.utc))
            session.add(execution)
            await session.commit()
            tools = await create_workspace_tools(session, execution_id=execution.execution_id,
                conversation_id=conversation_id, role=role, triggered_by_user_id=owner_id, allow_dangerous=True)
            assert len(tools) == 1
            assert await create_workspace_tools(session, execution_id=execution.execution_id,
                conversation_id=conversation_id, role=role, triggered_by_user_id=owner_id, allow_dangerous=False) == []
            generation_id = generation.id
            execution_pk = execution.id
            lease = await session.scalar(select(ExecutionWorkspace).where(ExecutionWorkspace.execution_id == execution.execution_id))
            lease_id = lease.id
        tool = tools[0]
        assert json.loads(await tool.ainvoke({'command': 'pwd', 'args': {}}))['exit_code'] == 0
        invalid = await tool.ainvoke({'command': 'pwd', 'cwd': '/'})
        assert 'COMMAND_ARGUMENT_INVALID' in invalid
        assert invalid.startswith('[工具被拒绝]')
        for model, pk, attr, value in [
            (User, owner_id, 'is_owner', False), (Role, role_id, 'active', False),
            (Role, role_id, 'builtin_tools_json', []), (Conversation, conversation_id, 'type', 'group'),
            (Conversation, conversation_id, 'workspace_binding_id', None),
            (WorkspaceBinding, binding_id, 'basic_commands_enabled', False),
            (WorkspaceBinding, binding_id, 'active', False),
            (ExecutionWorkspace, lease_id, 'status', 'retained'),
            (ExecutionWorkspace, lease_id, 'root_path_snapshot', str(command_root.parent)),
            (AgentExecution, execution_pk, 'status', 'interrupted'),
            (Generation, generation_id, 'stop_requested_at', datetime.now(timezone.utc)),
        ]:
            async with SessionLocal() as session:
                row = await session.get(model, pk)
                old = getattr(row, attr)
                setattr(row, attr, value)
                await session.commit()
            assert 'WORKSPACE_TOOL_NOT_AVAILABLE' in await tool.ainvoke({'command': 'pwd', 'args': {}})
            async with SessionLocal() as session:
                row = await session.get(model, pk)
                setattr(row, attr, old)
                await session.commit()


@pytest.mark.anyio
async def test_stop_generation_cancels_command_and_finishes_card(command_root, monkeypatch):
    """真实停止接口取消正在执行的子进程，卡片与审计记录正常取消。

    Args:
        command_root：本轮独立工作区。
        monkeypatch：仅在本测试替换受控进程 adapter。
    """
    from app.workspaces import commands
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import ToolCall
    original = commands.run_process
    started = asyncio.Event()

    async def controlled_process(argv, **kwargs):
        """提供确定性长运行 profile。

        Args:
            argv：产品 adapter 的固定参数。
            kwargs：保持产品 cwd/环境/输出配置。
        """
        started.set()
        return await original((sys.executable, '-I', '-c', 'import time;time.sleep(60)'), **kwargs)

    monkeypatch.setattr(commands, 'run_process', controlled_process)
    (command_root / 'cancel.txt').write_text('placeholder', encoding='utf-8')
    async with command_conversation(command_root) as (client, headers, conversation_id, _, _):
        await send_command(client, headers, conversation_id, '[W1B_CANCEL]')
        await asyncio.wait_for(started.wait(), 5)
        history = (await client.get(f'/api/conversations/{conversation_id}/messages', headers=headers)).json()
        running = next(message for message in history['items'] if message['sender_type'] == 'role')
        call = next(part for part in running['parts_json'] if part['type'] == 'tool_call')
        route = f"/api/conversations/{conversation_id}/messages/{running['id']}/tools/{call['call_id']}"
        detail = (await client.get(route, headers=headers)).json()
        assert detail['status'] == 'running' and detail['input'] is not None and detail['output'] is None
        stopped = await client.post(f'/api/conversations/{conversation_id}/stop', headers=headers)
        assert stopped.status_code == 202
        message = await wait_command_messages(client, headers, conversation_id)
        cards = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert message['status'] == 'stopped'
        assert cards[0]['status'] == cards[0]['command_status'] == 'cancelled'
        detail = (await client.get(route, headers=headers)).json()
        assert detail['status'] == 'cancelled' and detail['output'] is None
        async with SessionLocal() as session:
            calls = (await session.scalars(select(ToolCall).where(ToolCall.conversation_id == conversation_id))).all()
            assert len(calls) == 1 and calls[0].status == 'cancelled'


@pytest.fixture
def command_root(tmp_path_factory):
    """每轮在外部测试根创建独立空目录，避免接触产品源码。

    Args:
        tmp_path_factory：Windows 下提供临时目录基址。
    """
    import tempfile
    import re
    import shutil
    base = Path('/home/chen/workspace/testworkspace') if os.name != 'nt' else tmp_path_factory.getbasetemp()
    root = base / 'roleplex-command'
    root.mkdir(parents=True, exist_ok=True)
    run_root = root / f"test-{os.environ['ROLEPLEX_TEST_STAMP']}"
    run_root.mkdir(exist_ok=True)
    runs = sorted(path for path in root.iterdir() if re.fullmatch(r'test-\d{14}', path.name)
                  and path.is_dir() and not path.is_symlink())
    for stale in runs[:-5]:
        if stale != run_root:
            shutil.rmtree(stale)
    return Path(tempfile.mkdtemp(prefix='case-', dir=run_root))


@pytest.mark.anyio
async def test_commands_use_bound_cwd_and_read_only_utf8(command_root, monkeypatch):
    """真实进程使用绑定 cwd，读取中文并给出准确字节/行数。

    Args:
        command_root：本轮独立外部工作区。
        monkeypatch：注入不应继承的测试环境占位值。
    """
    from app.workspaces.commands import WorkspaceCommandService, command_environment
    monkeypatch.setenv('ROLEPLEX_COMMAND_SECRET', 'placeholder-never-inherited')
    monkeypatch.setenv('PYTHONPATH', str(command_root))
    (command_root / 'hello.txt').write_text('你好\n第二行', encoding='utf-8')
    service = WorkspaceCommandService(root=command_root, execution_id='command-test')
    pwd = await service.run('pwd', {})
    assert pwd['status'] == 'exited' and pwd['exit_code'] == 0
    assert pwd['stdout'].strip() == str(command_root.resolve())
    assert (await service.run('read', {'path': 'hello.txt'}))['stdout'] == '你好\n第二行'
    import json
    count = json.loads((await service.run('count', {'path': 'hello.txt'}))['stdout'])
    assert count == {'bytes': len('你好\n第二行'.encode()), 'lines': 2}
    assert 'hello.txt' in (await service.run('list', {}))['stdout']
    assert 'ROLEPLEX_COMMAND_SECRET' not in command_environment()
    assert 'PYTHONPATH' not in command_environment()


@pytest.mark.anyio
async def test_command_rejects_arbitrary_syntax_and_sensitive_paths(command_root):
    """任意命令、额外参数、Shell 语法、敏感文件和 symlink 均被拒绝。

    Args:
        command_root：本轮独立外部工作区。
    """
    from app.workspaces.commands import WorkspaceCommandError, WorkspaceCommandService
    service = WorkspaceCommandService(root=command_root, execution_id='reject-test')
    for command, args, code in [
        ('bash', {}, 'COMMAND_NOT_ALLOWED'),
        ('pwd', {'cwd': '/'}, 'COMMAND_ARGUMENT_INVALID'),
        ('pwd', {'path': '.'}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': 'hello;pwd'}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': 'hello\nworld'}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': '.env:stream'}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': '.env '}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': '.env.'}, 'COMMAND_ARGUMENT_INVALID'),
        ('read', {'path': '../outside'}, 'WORKSPACE_PATH_INVALID'),
        ('read', {'path': '.env'}, 'WORKSPACE_PATH_SENSITIVE'),
    ]:
        with pytest.raises(WorkspaceCommandError) as error:
            await service.run(command, args)
        assert error.value.code == code
    (command_root / '.env').write_text('placeholder-sensitive', encoding='utf-8')
    assert '.env' not in (await service.run('list', {}))['stdout']
    if os.name != 'nt':
        (command_root / 'escape').symlink_to(command_root.parent, target_is_directory=True)
        with pytest.raises(WorkspaceCommandError) as error:
            await service.run('list', {'path': 'escape'})
        assert error.value.code == 'WORKSPACE_PATH_OUTSIDE_ROOT'


@pytest.mark.anyio
async def test_process_drains_both_streams_with_total_output_limit(command_root):
    """截断仍排空双流，非零退出保留准确状态且输出总量有界。

    Args:
        command_root：本轮独立外部工作区。
    """
    from app.workspaces.commands import run_process
    result = await run_process(
        (sys.executable, '-I', '-c', 'import sys; sys.stdout.write("x"*200000); sys.stderr.write("y"*200000); sys.exit(7)'),
        root=command_root, payload=b'', timeout=5, output_limit=1024,
    )
    assert result['exit_code'] == 7 and result['error_code'] == 'COMMAND_FAILED'
    assert result['truncated'] is True
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= 1024
    assert result['stdout_bytes'] == result['stderr_bytes'] == 200000
    assert result['stdout_seq'] > 0 and result['stderr_seq'] > 0


@pytest.mark.anyio
@pytest.mark.parametrize('cancel', [False, True])
async def test_timeout_and_cancel_reap_process_tree(command_root, cancel):
    """超时及外部取消都等待真实父子进程清理。

    Args:
        command_root：本轮独立外部工作区。
        cancel：选择任务取消或命令超时。
    """
    from app.workspaces.commands import run_process
    marker = command_root / 'pids.txt'
    script = (
        'import subprocess,sys,time,os,pathlib; '
        'p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"]); '
        'pathlib.Path("pids.txt").write_text(str(os.getpid())+","+str(p.pid));time.sleep(60)'
    )
    task = asyncio.create_task(run_process(
        (sys.executable, '-I', '-c', script), root=command_root, payload=b'',
        timeout=10 if cancel else 0.5, output_limit=1024,
    ))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    pids = [int(value) for value in marker.read_text().split(',')]
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result['status'] == 'timed_out' and result['error_code'] == 'COMMAND_TIMEOUT'
    assert all(not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE for pid in pids)


@pytest.mark.anyio
async def test_parent_exit_does_not_leave_inherited_output_pipe_open(command_root):
    """父进程先退出时仍回收后代，管道不能让正常结束挂起。

    Args:
        command_root：本轮独立外部工作区。
    """
    from app.workspaces.commands import run_process
    result = await run_process((sys.executable, '-I', '-c',
        'import subprocess,sys; p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"]); print(p.pid)'),
        root=command_root, payload=b'', timeout=5, output_limit=1024)
    assert result['status'] == 'exited' and result['exit_code'] == 0
    pid = int(result['stdout'].strip())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


@pytest.mark.anyio
async def test_child_environment_and_python_imports_are_isolated(command_root, monkeypatch):
    """实际进程不能继承后端凭据占位值或导入工作区 sitecustomize。

    Args:
        command_root：本轮独立外部工作区。
        monkeypatch：注入仅用于验证过滤的占位环境。
    """
    from app.workspaces.commands import run_process
    monkeypatch.setenv('JWT_SECRET', 'test-placeholder')
    monkeypatch.setenv('PYTHONPATH', str(command_root))
    (command_root / 'sitecustomize.py').write_text('raise RuntimeError("must not import")', encoding='utf-8')
    result = await run_process((sys.executable, '-I', '-c',
        'import os,sys,json; print(json.dumps({"secret": "JWT_SECRET" in os.environ,"cwd_import":os.getcwd() in sys.path}))'),
        root=command_root, payload=b'', timeout=5, output_limit=1024)
    assert json.loads(result['stdout']) == {'secret': False, 'cwd_import': False}


@pytest.mark.anyio
async def test_cancellation_during_spawn_waits_for_process_handoff(command_root, monkeypatch):
    """取消发生在启动交接窗口时也必须取得句柄并回收。

    Args:
        command_root：本轮独立外部工作区。
        monkeypatch：延迟启动句柄交接，保留真实子进程。
    """
    from app.workspaces import commands
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    release = asyncio.Event()
    processes = []

    async def delayed_spawn(*args, **kwargs):
        """在真实进程创建后延迟交接。

        Args:
            args：服务端构造的固定 argv。
            kwargs：真实 cwd、管道、进程组与最小环境。
        """
        process = await spawn(*args, **kwargs)
        processes.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(commands.asyncio, 'create_subprocess_exec', delayed_spawn)
    task = asyncio.create_task(commands.run_process((sys.executable, '-I', '-c', 'import time;time.sleep(60)'),
        root=command_root, payload=b'', timeout=5, output_limit=1024))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.anyio
async def test_command_calls_are_serial_within_same_directory(command_root, monkeypatch):
    """同一目录的多个命令只同时运行一个进程，不依赖数据库写锁。

    Args:
        command_root：本轮独立外部工作区。
        monkeypatch：将进程 adapter 替换为确定性并行探针。
    """
    from app.workspaces import commands
    active = 0
    peak = 0

    async def probe(*args, **kwargs):
        """记录进入进程层的并行数。

        Args:
            args：服务端固定 argv。
            kwargs：服务端执行边界。
        """
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {'status': 'exited', 'exit_code': 0}

    monkeypatch.setattr(commands, 'run_process', probe)
    service = commands.WorkspaceCommandService(root=command_root, execution_id='serial-test')
    await asyncio.gather(*(service.run('pwd', {}) for _ in range(3)))
    assert peak == 1


@pytest.mark.anyio
async def test_restart_marks_command_result_unknown_without_replay(command_root):
    """启动恢复只标记执行中断，不能把未观察到的进程终态编成成功或取消。

    Args:
        command_root：本轮独立外部工作区。
    """
    from datetime import datetime, timezone
    from app.db import SessionLocal, recover_interrupted_messages
    from app.models import Message
    async with command_conversation(command_root) as (_, _, conversation_id, role_id, _):
        async with SessionLocal() as session:
            message = Message(conversation_id=conversation_id, sender_type='role', sender_id=role_id,
                status='generating', created_at=datetime.now(timezone.utc), revision=2,
                parts_json=[{'type': 'tool_call', 'call_id': 'test', 'tool_name': 'workspace_run_command',
                             'command': 'read', 'status': 'running'}])
            session.add(message)
            await session.commit()
            message_id = message.id
        await recover_interrupted_messages()
        await recover_interrupted_messages()
        async with SessionLocal() as session:
            message = await session.get(Message, message_id)
            assert message.status == 'interrupted' and message.revision == 3
            assert message.parts_json[0]['error_code'] == 'EXECUTION_INTERRUPTED'
            assert message.parts_json[0]['exit_code'] is None and 'command_status' not in message.parts_json[0]


@pytest.mark.anyio
async def test_stop_after_observed_result_does_not_duplicate_audit(command_root, monkeypatch, isolated_command_database):
    """命令已结束但审计交接中收到停止时，保留真实结果且只记一条审计。

    Args:
        command_root：本轮独立外部工作区。
        monkeypatch：延迟审计提交以稳定复现交接窗口。
        isolated_command_database：本用例新建且由迁移重放的数据库。
    """
    from app.services import chat
    from app.db import SessionLocal
    from app.models import ToolCall
    from sqlalchemy import select
    reached = asyncio.Event()
    release = asyncio.Event()
    original = chat._record_tool_call

    async def delayed_record(*args, **kwargs):
        """在已观察到结果后暂停审计。

        Args:
            args：真实完成事件。
            kwargs：该事件的执行关联。
        """
        reached.set()
        await release.wait()
        await original(*args, **kwargs)

    monkeypatch.setattr(chat, '_record_tool_call', delayed_record)
    async with command_conversation(command_root) as (client, headers, conversation_id, _, _):
        await send_command(client, headers, conversation_id, '[W1B_FAKE_E2E]')
        await asyncio.wait_for(reached.wait(), 5)
        stopping = asyncio.create_task(client.post(f'/api/conversations/{conversation_id}/stop', headers=headers))
        await asyncio.sleep(0.05)
        release.set()
        assert (await stopping).status_code == 202
        message = await wait_command_messages(client, headers, conversation_id)
        assert message['status'] == 'stopped'
        cards = [part for part in message['parts_json'] if part['type'] == 'tool_call']
        assert len(cards) == 1 and cards[0]['status'] == 'success'
        async with SessionLocal() as session:
            calls = (await session.scalars(select(ToolCall).where(ToolCall.conversation_id == conversation_id))).all()
            assert len(calls) == 1 and calls[0].status == 'ok'
