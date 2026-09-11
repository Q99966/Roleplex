"""独立后台服务宿主与统一回收协调器；不由聊天组件或 generation 保活。"""
from __future__ import annotations

import asyncio
import codecs
import hashlib
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import signal
import socket
import sys
import time
import uuid

import httpx
import psutil
from sqlalchemy import select, update

from ..config import settings
from ..db import SessionLocal, with_locked_retry
from ..workspaces.commands import command_environment, WorkspaceCommandError
from ..workspaces.process_identity import birth_identity, same_birth
from . import logs, registry, receipts
from .models import CleanupItem, CleanupOperation, RuntimeEntry, RuntimeGate

current_runtime: ContextVar[str | None] = ContextVar('current_runtime', default=None)
STOP_BUDGET_SECONDS = 12


def supported() -> bool:
    """后台服务只在已具备监管/宿主消失验证的 Linux 开放。"""
    return sys.platform == 'linux'


async def settled(task):
    """等待必须完成的交接/回收，重复取消也不能遗留资源。

    Args:
        task：已绑定链路上下文的有限生命周期任务。
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


@dataclass
class Host:
    """只由一个后台任务改变状态，外部控制通过 stop 信号合流。"""
    id: str
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    process: asyncio.subprocess.Process | None = None
    birth: str | None = None
    ready: asyncio.Future | None = None
    control_fd: int | None = None
    ring: logs.LogRing = field(default_factory=logs.LogRing)
    reason: str = 'owner_stop'
    cleanup_id: str | None = None
    verified_gone: bool = False
    final_state: str = 'stopped'
    error_code: str | None = None


class RuntimeManager:
    """本后端实例的真实句柄所有者；数据库负责恢复与审计，不按裸 PID 接管。"""
    def __init__(self):
        """初始化内存句柄，不在导入时访问数据库。"""
        self.hosts: dict[str, Host] = {}
        self.waiting: dict[str, asyncio.Task] = {}
        self.commands: dict[str, asyncio.subprocess.Process] = {}
        self.command_births: dict[str, str] = {}
        self.command_stopping: set[str] = set()
        self.cleanup_lock = asyncio.Lock()
        self.world_operation_lock = asyncio.Lock()
        self.switch_target: str | None = None
        self.stop_jobs: dict[str, asyncio.Task] = {}

    async def initialize(self):
        """启动核查旧实例；旧 PID 不符时不发送信号，不重放服务。"""
        self.cleanup_lock = asyncio.Lock()
        self.world_operation_lock = asyncio.Lock()
        self.switch_target = None
        await logs.maintain()
        async with SessionLocal() as session:
            rows = list((await session.scalars(select(RuntimeEntry).where(RuntimeEntry.state.in_(registry.ACTIVE)))).all())
        boot = registry.boot_identity()
        recovery_deadline = time.monotonic() + 2
        for row in rows:
            rebooted = bool(boot and row.host_boot_id and boot != row.host_boot_id)
            gone = rebooted or row.pid is None or not self.alive(row.pid)
            if row.state == 'cleanup_required' and row.pid is not None and not rebooted:
                # 上次明确缺少回收证明时，根 PID 消失并不能证明脱离进程组的后代已退出。
                gone = False
            if row.kind == 'service' and row.pid is not None and not rebooted:
                gone = False
                while row.recovery_token_hash:
                    if receipts.read(row) is not None and receipts.old_root_gone(row):
                        gone = True
                        break
                    if time.monotonic() >= recovery_deadline:
                        break
                    await asyncio.sleep(.02)
            if row.kind != 'service' and row.state != 'cleanup_required' and not gone and self.same_process(row.pid, row.birth):
                # 正常宿主消失后 guardian 会通过 EOF 收口；只等候，不能以旧记录盲目接管。
                for _ in range(100):
                    if not self.alive(row.pid):
                        gone = True
                        break
                    await asyncio.sleep(.02)
            if gone:
                row = await registry.finish(row.id, 'interrupted', verified=True, error_code='EXECUTION_INTERRUPTED')
                receipts.discard(row)
            else:
                row = await registry.change(row.id, state='cleanup_required', error_code='RUNTIME_CLEANUP_UNCONFIRMED')
            registry.logger.info('runtime.recovery_checked', extra={**registry.links(row), 'verified_gone': gone,
                'reason': 'host_reboot' if rebooted else ('receipt_verified' if gone else 'receipt_unconfirmed')
                    if row.kind == 'service' and row.pid is not None else 'pid_checked'})
        try:
            for runtime_id in receipts.recorded_ids():
                row = await registry.get(runtime_id)
                if row and row.state in registry.TERMINAL and not receipts.discard(row):
                    registry.audit('runtime.recovery_checked', runtime_id=runtime_id, reason='receipt_cleanup_failed',
                        status='failed', error_code='RUNTIME_STATE_UNAVAILABLE')
        except OSError:
            registry.audit('runtime.recovery_checked', reason='receipt_scan_failed', status='failed', error_code='RUNTIME_STATE_UNAVAILABLE')
        async with SessionLocal() as session:
            await registry.gate(session)
            await session.execute(update(RuntimeGate).where(RuntimeGate.id == 1).values(closing=False))
            # 旧未完成清单仍保留事实；没有遗留活动实例时解除其门槛，不补造旧成功终态。
            operations = list((await session.scalars(select(CleanupOperation).where(CleanupOperation.state.in_(('running', 'prepared', 'failed'))))).all())
            for operation in operations:
                live = await session.scalar(select(RuntimeEntry.id).where(registry.scope_filter(operation.scope, operation.scope_id), RuntimeEntry.state.in_(registry.ACTIVE)).limit(1))
                if live is None:
                    old_state = operation.state
                    operation.state = 'superseded'
                    registry.logger.info('runtime.cleanup_reconciled', extra={'cleanup_id': operation.id,
                        'previous_state': old_state, 'reason': 'startup_verified_no_active', 'status': 'success'})
            await session.commit()

    @staticmethod
    def alive(pid: int) -> bool:
        """Args:
            pid：仅检查存活，不以此授予停止权限。
        """
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    @staticmethod
    def same_process(pid: int, birth: str | None) -> bool:
        """复核 PID 出生身份，PID 被复用不是原资源仍存活。

        Args:
            pid：已经登记的进程。
            birth：创建时实际观察的出生时间。
        """
        try:
            process = psutil.Process(pid)
            return same_birth(pid, birth) and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    async def notify(self, row: RuntimeEntry):
        """保留宿主调用点；事件由 registry 与状态原子保存后广播，不再另开事务。

        Args:
            row：已经提交的实例状态。
        """
        return row

    @asynccontextmanager
    async def command(self, **identity):
        """把一次性命令加入配额和 /ps，但不改变命令的结束清理语义。

        Args:
            identity：工具适配层提供的可信身份。
        """
        runtime_id = uuid.uuid4().hex
        task = asyncio.current_task()
        self.waiting[runtime_id] = task
        marker = current_runtime.set(runtime_id)
        cancelled = False
        try:
            reserve_task = asyncio.create_task(registry.reserve(**identity, kind='command', runtime_id=runtime_id), context=copy_context())
            await settled(reserve_task)
            await self.notify(await registry.get(runtime_id))
            yield runtime_id
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            current_runtime.reset(marker)
            async def close():
                """第二次取消不能切断账本收尾，也不能把进程句柄泄漏在映射中。"""
                try:
                    row = await registry.get(runtime_id)
                    process = self.commands.get(runtime_id)
                    if row and row.state in registry.ACTIVE:
                        if row.pid is None and process is not None:
                            row = await registry.change(runtime_id, pid=process.pid, birth=self.command_births.get(runtime_id))
                        gone = row.pid is None or (process is not None and process.returncode is not None) or not self.alive(row.pid)
                        terminal = 'rejected' if row.pid is None else 'stopped' if cancelled else 'exited'
                        ended = (await registry.finish(runtime_id, terminal, verified=True, exit_code=process.returncode if process else None) if gone
                            else await registry.change(runtime_id, state='cleanup_required', error_code='RUNTIME_CLEANUP_UNCONFIRMED'))
                        await self.notify(ended)
                finally:
                    self.waiting.pop(runtime_id, None)
                    self.commands.pop(runtime_id, None)
                    self.command_births.pop(runtime_id, None)
                    self.command_stopping.discard(runtime_id)
            await settled(asyncio.create_task(close(), context=copy_context()))

    async def attach_command(self, process: asyncio.subprocess.Process):
        """交付 stdin 前登记真实句柄和出生身份，降低崩溃交接窗口。

        Args:
            process：本次运行器刚创建的进程，不能是系统扫描结果。
        """
        runtime_id = current_runtime.get()
        if runtime_id is None:
            return
        self.commands[runtime_id] = process
        birth = birth_identity(process.pid)
        self.command_births[runtime_id] = birth
        await registry.check_start(runtime_id)
        await self.notify(await registry.change(runtime_id, state='running', pid=process.pid, birth=birth, started_at=datetime.now(timezone.utc)))

    async def start_service(self, *, identity: dict, script: str, port: int, health_path: str, lifetime_seconds: int) -> dict:
        """创建后台服务的请求宿主；ready 返回后生命周期转移给后台任务。

        Args:
            identity：执行层冻结的 Owner/会话/工作区/调用身份，含 root_path。
            script：前台脚本。
            port：审批端口。
            health_path：仅相对路径。
            lifetime_seconds：批准的寿命。
        """
        if not supported():
            raise registry.RuntimeRejected('RUNTIME_NOT_SUPPORTED')
        runtime_id = uuid.uuid4().hex
        self.waiting[runtime_id] = asyncio.current_task()
        try:
            values = {key: value for key, value in identity.items() if key != 'root_path'}
            reserve_task = asyncio.create_task(registry.reserve(**values, kind='service', port=port, runtime_id=runtime_id), context=copy_context())
            row = await settled(reserve_task)
            await self.notify(row)
            from ..workspaces.approvals import request_and_run
            request = await request_and_run(script=script, execution_id=identity['execution_id'], conversation_id=identity['conversation_id'],
                role_id=identity['role_id'], owner_id=identity['owner_id'], workspace_binding_id=identity['workspace_id'],
                root_path=identity['root_path'], tool_call_id=identity['tool_call_id'], service_request={
                    'runtime_id': runtime_id, 'port': port, 'health_path': health_path, 'lifetime_seconds': lifetime_seconds,
                    'ready_timeout_seconds': settings.runtime_ready_timeout_seconds})
            await registry.check_start(runtime_id)
            host = Host(runtime_id, ready=asyncio.get_running_loop().create_future())
            self.hosts[runtime_id] = host
            host.task = asyncio.create_task(self._host(host, request), context=copy_context())
            completed, _ = await asyncio.wait((host.ready, host.task), return_when=asyncio.FIRST_COMPLETED)
            if host.ready not in completed:
                await host.task
                raise registry.RuntimeRejected('RUNTIME_START_FAILED')
            result = host.ready.result()
            if result is None:
                raise registry.RuntimeRejected('RUNTIME_STATE_UNAVAILABLE')
            if result.state != 'ready':
                raise registry.RuntimeRejected(result.error_code or 'RUNTIME_START_FAILED')
            return {'runtime_id': runtime_id, 'state': result.state, 'port': port, 'health_code': result.health_code}
        except BaseException as exc:
            if runtime_id in self.hosts:
                host = self.hosts[runtime_id]
                host.reason = 'startup_cancelled'
                host.stop.set()
                await settled(asyncio.create_task(self._wait_host(host), context=copy_context()))
            else:
                row = await registry.get(runtime_id)
                if row and row.state in registry.ACTIVE:
                    code = row.error_code or getattr(exc, 'code', 'RUNTIME_START_CANCELLED' if isinstance(exc, asyncio.CancelledError) else 'RUNTIME_START_FAILED')
                    await self.notify(await registry.finish(runtime_id, 'expired' if isinstance(exc, asyncio.CancelledError) else 'rejected', error_code=code))
                    if isinstance(exc, WorkspaceCommandError):
                        raise registry.RuntimeRejected(code) from None
            raise
        finally:
            self.waiting.pop(runtime_id, None)
            self.command_stopping.discard(runtime_id)

    async def _wait_host(self, host: Host):
        """Args:
            host：本实例的唯一服务宿主。
        """
        if host.task:
            await asyncio.shield(host.task)

    async def _probe(self, host: Host, port: int, path: str) -> tuple[bool, int | None]:
        """验证本进程树的监听归属，再进行不带凭据的有界 GET。

        Args:
            host：受监管的真实进程。
            port：声明端口。
            path：已验证的相对路径。
        """
        if host.process.returncode is not None:
            return False, None
        try:
            children = psutil.Process(host.process.pid).children(recursive=True)
            owned = False
            for child in children:
                try:
                    for connection in child.net_connections(kind='tcp'):
                        if connection.status != psutil.CONN_LISTEN:
                            continue
                        if connection.laddr.port != port or connection.laddr.ip not in ('127.0.0.1', '::1'):
                            raise registry.RuntimeRejected('RUNTIME_LISTENER_MISMATCH')
                        owned = True
                except psutil.NoSuchProcess:
                    pass
            if not owned:
                return False, None
            async with httpx.AsyncClient(trust_env=False, timeout=1, follow_redirects=False) as client:
                async with client.stream('GET', httpx.URL(scheme='http', host='127.0.0.1', port=port, path=path)) as response:
                    return 200 <= response.status_code < 300, response.status_code
        except (psutil.NoSuchProcess, httpx.HTTPError):
            return False, None
        except psutil.AccessDenied:
            raise registry.RuntimeRejected('RUNTIME_LISTENER_UNVERIFIED') from None

    async def _host(self, host: Host, request: dict):
        """后台单一所有者处理启动、健康、停止和终态输出，generation 不持有该任务。

        Args:
            host：独占句柄与信号。
            request：审批后重新验证的冻结上下文。
        """
        readers = []
        spawning = None
        read_fd = None
        proof_reader = proof_writer = None
        token_reader = None
        final_state, error_code = 'stopped', None
        try:
            from ..workspaces.approvals import authorized_execution
            if not await authorized_execution(request):
                raise registry.RuntimeRejected('WORKSPACE_TOOL_NOT_AVAILABLE')
            # 预检不抢占端口；真正 ready 还必须确认 listener 的进程归属。
            try:
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(('127.0.0.1', request['port']))
            except OSError:
                raise registry.RuntimeRejected('RUNTIME_PORT_BUSY') from None
            receipt_path, receipt_token = receipts.prepare(host.id)
            await self.notify(await registry.change(host.id, state='starting', recovery_token_hash=hashlib.sha256(receipt_token).hexdigest()))
            token_reader, token_writer = os.pipe()
            try:
                os.write(token_writer, receipt_token)
            finally:
                os.close(token_writer)
            read_fd, host.control_fd = os.pipe()
            proof_reader, proof_writer = os.pipe()
            os.set_blocking(proof_reader, False)
            argv = (sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve().parents[1] / 'workspaces' / 'shell_supervisor.py'),
                '--parent-fd', str(read_fd), '--runtime-id', host.id, '--result-fd', str(proof_writer),
                '--receipt-path', str(receipt_path), '--receipt-token-fd', str(token_reader), *request['argv'])
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(*argv, cwd=request['root_path'], env=command_environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, pass_fds=(read_fd, proof_writer, token_reader)), context=copy_context())
            host.process = await asyncio.shield(spawning)
            os.close(read_fd)
            read_fd = None
            os.close(proof_writer)
            proof_writer = None
            os.close(token_reader)
            token_reader = None
            host.birth = birth_identity(host.process.pid)
            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=request['lifetime_seconds'])
            lifetime_deadline = time.monotonic() + request['lifetime_seconds']
            await registry.check_start(host.id)
            await self.notify(await registry.change(host.id, state='waiting_ready', pid=host.process.pid, birth=host.birth,
                started_at=now, expires_at=expires, approval_id=request['approval_id']))
            async def drain(stream_name: str):
                """增量解码并始终排空，慢浏览器不能阻塞服务输出。

                Args:
                    stream_name：独立的 stdout 或 stderr。
                """
                decoder = codecs.getincrementaldecoder('utf-8')('replace')
                while chunk := await getattr(host.process, stream_name).read(8192):
                    host.ring.append(stream_name, decoder.decode(chunk))
                host.ring.append(stream_name, decoder.decode(b'', final=True))
            readers = [asyncio.create_task(drain(name), context=copy_context()) for name in ('stdout', 'stderr')]
            # 宿主创建和状态登记均可能等待；交付实际脚本前再次复核。ready 后不再用 generation 存活约束服务。
            if not await authorized_execution(request):
                raise registry.RuntimeRejected('WORKSPACE_TOOL_NOT_AVAILABLE')
            host.process.stdin.write((request['script'] + '\n').encode())
            await host.process.stdin.drain()
            host.process.stdin.close()
            deadline = time.monotonic() + request['ready_timeout_seconds']
            last_health_write = 0.0
            ready = False
            while not host.stop.is_set():
                if host.process.returncode is not None:
                    final_state = 'exited' if ready else 'failed'
                    error_code = None if ready else 'RUNTIME_START_FAILED'
                    break
                if time.monotonic() >= lifetime_deadline:
                    host.reason = 'lifetime_expired'
                    final_state = 'expired'
                    break
                healthy, code = await self._probe(host, request['port'], request['health_path'])
                if host.stop.is_set():
                    break
                if not ready and not healthy and time.monotonic() >= deadline:
                    raise registry.RuntimeRejected('RUNTIME_READY_TIMEOUT')
                if healthy or ready:
                    row = await registry.get(host.id)
                    state = 'ready' if healthy else 'unhealthy'
                    state_changed = row.state != state
                    if state_changed or code != row.health_code or time.monotonic() - last_health_write >= 5:
                        row = await registry.change(host.id, **({'state': state} if state_changed else {}),
                            health_code=code, health_checked_at=datetime.now(timezone.utc))
                        last_health_write = time.monotonic()
                        if state_changed:
                            await self.notify(row)
                    if healthy and not ready:
                        ready = True
                        host.ready.set_result(row)
                try:
                    await asyncio.wait_for(host.stop.wait(), min(2 if ready else .1, max(.001, lifetime_deadline - time.monotonic())))
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            final_state, error_code = 'failed', 'RUNTIME_START_CANCELLED'
        except (registry.RuntimeRejected, WorkspaceCommandError) as exc:
            final_state, error_code = 'failed', exc.code
        except (OSError, psutil.Error):
            final_state, error_code = 'failed', 'RUNTIME_START_FAILED'
        except Exception:
            final_state, error_code = 'failed', 'RUNTIME_STATE_UNAVAILABLE'
        finally:
            if spawning is not None and host.process is None:
                try:
                    host.process = await settled(spawning)
                except asyncio.CancelledError:
                    host.process = spawning.result()
                except OSError:
                    pass
            if read_fd is not None:
                os.close(read_fd)
            if token_reader is not None:
                os.close(token_reader)
            if proof_writer is not None:
                os.close(proof_writer)
            # 即使后续数据库或日志失败，也先通知监管器回收；不能让审计故障遗留服务。
            if host.control_fd is not None:
                os.close(host.control_fd)
                host.control_fd = None
            if host.process is not None:
                try:
                    await self.notify(await registry.change(host.id, state='stopping', pid=host.process.pid, birth=host.birth))
                except Exception:
                    error_code = 'RUNTIME_STATE_UNAVAILABLE'
                if not registry.audit('runtime.stop_dispatched', runtime_id=host.id, pid=host.process.pid,
                    process_birth=host.birth, reason=host.reason, cleanup_id=host.cleanup_id):
                    error_code = 'RUNTIME_AUDIT_UNAVAILABLE'
                try:
                    async with asyncio.timeout(8):
                        await host.process.wait()
                except TimeoutError:
                    try:
                        if host.process.returncode is None and self.same_process(host.process.pid, host.birth):
                            if not registry.audit('runtime.force_requested', runtime_id=host.id, pid=host.process.pid, process_birth=host.birth):
                                error_code = 'RUNTIME_AUDIT_UNAVAILABLE'
                            os.killpg(host.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except (OSError, psutil.Error):
                        final_state, error_code = 'cleanup_required', 'RUNTIME_CLEANUP_UNCONFIRMED'
                    try:
                        async with asyncio.timeout(2):
                            await host.process.wait()
                    except (TimeoutError, OSError):
                        final_state, error_code = 'cleanup_required', 'RUNTIME_CLEANUP_UNCONFIRMED'
                if readers:
                    try:
                        async with asyncio.timeout(1):
                            await asyncio.gather(*readers)
                    except TimeoutError:
                        final_state, error_code = 'cleanup_required', 'RUNTIME_CLEANUP_UNCONFIRMED'
            proof = False
            if proof_reader is not None:
                try:
                    proof = os.read(proof_reader, 1) == b'1'
                except BlockingIOError:
                    pass
                finally:
                    os.close(proof_reader)
            if host.process is not None and (host.process.returncode is None or not proof):
                final_state, error_code = 'cleanup_required', 'RUNTIME_CLEANUP_UNCONFIRMED'
            host.verified_gone = final_state != 'cleanup_required' and (host.process is None or host.process.returncode is not None)
            host.final_state = final_state
            error_code = error_code or host.error_code
            try:
                await logs.save(host.id, host.ring)
            except Exception:
                error_code = error_code or 'RUNTIME_LOG_UNAVAILABLE'
            host.error_code = error_code
            try:
                values = {'error_code': error_code, 'exit_code': host.process.returncode if host.process else None}
                row = (await registry.change(host.id, state=final_state, **values) if final_state == 'cleanup_required'
                    else await registry.finish(host.id, final_state, verified=host.verified_gone, **values))
                if not host.ready.done():
                    host.ready.set_result(row)
                if host.verified_gone:
                    if not receipts.discard(row):
                        registry.audit('runtime.recovery_checked', runtime_id=host.id, reason='receipt_cleanup_failed',
                            verified_gone=True, status='failed', error_code='RUNTIME_STATE_UNAVAILABLE')
                    self.hosts.pop(host.id, None)
            except Exception:
                # 保留实际句柄与已完成回收的证据，数据库恢复后可重试终态，不重放脚本。
                host.error_code = 'RUNTIME_STATE_UNAVAILABLE'
                if not host.ready.done():
                    host.ready.set_result(None)

    async def stop_one(self, runtime_id: str, reason: str = 'owner_stop', cleanup_id: str | None = None) -> RuntimeEntry:
        """共用独立停止任务，单请求超时或取消不重复打断已在进行的回收。

        Args:
            runtime_id：已授权的运行实例。
            reason：可信停止原因。
            cleanup_id：冻结批次身份或空。
        """
        task = self.stop_jobs.get(runtime_id)
        if task is None:
            task = asyncio.create_task(self._stop_one(runtime_id, reason, cleanup_id), context=copy_context())
            self.stop_jobs[runtime_id] = task
            def finished(done):
                """Args:
                    done：实际结束的停止任务；消费异常但不抹掉数据库失败事实。
                """
                if self.stop_jobs.get(runtime_id) is done:
                    self.stop_jobs.pop(runtime_id, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), STOP_BUDGET_SECONDS)
        except TimeoutError:
            raise registry.RuntimeRejected('RUNTIME_CLEANUP_UNCONFIRMED') from None

    async def _stop_one(self, runtime_id: str, reason: str, cleanup_id: str | None) -> RuntimeEntry:
        """幂等通知唯一宿主停止，绝不扫描并杀死全机同端口/PID 进程。

        Args:
            runtime_id：已经过范围授权的实例。
            reason：已登记停止原因。
            cleanup_id：批量操作的资源身份；独立停止为空。
        """
        host = self.hosts.get(runtime_id)
        try:
            row = await registry.get(runtime_id)
        except Exception:
            if host:
                host.reason, host.cleanup_id = reason, cleanup_id
                host.stop.set()
                await self._wait_host(host)
            raise registry.RuntimeRejected('RUNTIME_STATE_UNAVAILABLE') from None
        if row is None:
            raise registry.RuntimeRejected('RUNTIME_NOT_FOUND')
        boot = registry.boot_identity()
        rebooted = bool(row.host_boot_id and boot and row.host_boot_id != boot)
        if row.state in registry.TERMINAL:
            if host and host.task.done():
                self.hosts.pop(runtime_id, None)
            return row
        if host:
            if host.task.done() and not host.verified_gone and receipts.read(row) is not None and receipts.old_root_gone(row):
                host.verified_gone, host.final_state, host.error_code = True, 'interrupted', 'EXECUTION_INTERRUPTED'
            if host.task.done() and host.verified_gone:
                await logs.save(host.id, host.ring)
                row = await registry.finish(host.id, host.final_state, verified=True,
                    exit_code=host.process.returncode if host.process else None,
                    error_code=None if host.error_code == 'RUNTIME_STATE_UNAVAILABLE' else host.error_code)
                self.hosts.pop(host.id, None)
                receipts.discard(row)
                return row
            if not host.stop.is_set():
                if not registry.audit('runtime.stop_requested', **registry.links(row), reason=reason, cleanup_id=cleanup_id):
                    host.error_code = 'RUNTIME_AUDIT_UNAVAILABLE'
                host.reason, host.cleanup_id = reason, cleanup_id
                host.stop.set()
            await self._wait_host(host)
        elif runtime_id in self.waiting:
            task = self.waiting[runtime_id]
            recorded = True
            if runtime_id not in self.command_stopping:
                recorded = registry.audit('runtime.stop_requested', **registry.links(row), reason=reason, cleanup_id=cleanup_id)
                self.command_stopping.add(runtime_id)
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if not recorded:
                raise registry.RuntimeRejected('RUNTIME_AUDIT_UNAVAILABLE')
        elif row.pid is None or rebooted or (row.kind == 'service' and receipts.read(row) is not None and receipts.old_root_gone(row)) or (row.kind != 'service' and row.state != 'cleanup_required' and not self.alive(row.pid)):
            await registry.finish(runtime_id, 'interrupted', verified=True)
            receipts.discard(row)
        else:
            await registry.change(runtime_id, state='cleanup_required', error_code='RUNTIME_CLEANUP_UNCONFIRMED')
        return await registry.get(runtime_id)

    async def _drain_cleanup(self, operation, targets) -> bool:
        """逆序尝试完整清单；停止、审计或结果持久化单项失败都不短路。

        Args:
            operation：已持久化的范围与批次身份。
            targets：已冻结且稳定排序的目标清单。
        """
        failed = getattr(operation, 'audit_failed', False)
        for item in targets:
            started = datetime.now(timezone.utc)
            started_clock = time.monotonic()
            if not registry.audit('runtime.cleanup_target_started', cleanup_id=operation.id,
                runtime_id=item.runtime_id, target_ordinal=item.ordinal):
                failed, operation.error_code = True, 'RUNTIME_AUDIT_UNAVAILABLE'
            try:
                try:
                    before = await registry.get(item.runtime_id)
                except Exception:
                    before = None
                result = await self.stop_one(item.runtime_id, operation.reason, operation.id)
                success = result.state in registry.TERMINAL
                if result.error_code in {'RUNTIME_AUDIT_UNAVAILABLE', 'RUNTIME_STATE_UNAVAILABLE'}:
                    failed, operation.error_code = True, result.error_code
                outcome = ('already_terminal' if before and before.state in registry.TERMINAL else 'no_registered_process'
                    if result.pid is None else 'verified_exited') if success else 'unconfirmed'
            except Exception as exc:
                success, outcome = False, 'unconfirmed'
                operation.error_code = exc.code if isinstance(exc, WorkspaceCommandError) else 'RUNTIME_STATE_UNAVAILABLE'
            ended = datetime.now(timezone.utc)
            async def persist():
                """逐项幂等保存结果；不在进程停止期间持有事务。"""
                async with SessionLocal() as session:
                    await session.execute(update(CleanupItem).where(CleanupItem.id == item.id).values(
                        state='complete' if success else 'failed', outcome=outcome,
                        error_code=None if success else 'RUNTIME_CLEANUP_UNCONFIRMED', started_at=started, ended_at=ended))
                    await session.commit()
            try:
                await with_locked_retry(persist)
            except Exception:
                failed, operation.error_code = True, 'RUNTIME_STATE_UNAVAILABLE'
            failed |= not success
            recorded = registry.audit('runtime.cleanup_target_completed', cleanup_id=operation.id,
                runtime_id=item.runtime_id, target_ordinal=item.ordinal, outcome=outcome,
                status='success' if success else 'failed', duration_ms=int((time.monotonic() - started_clock) * 1000))
            if not recorded:
                failed, operation.error_code = True, 'RUNTIME_AUDIT_UNAVAILABLE'
        async with SessionLocal() as session:
            remaining = await session.scalar(select(RuntimeEntry.id).where(registry.scope_filter(operation.scope, operation.scope_id),
                RuntimeEntry.state.in_(registry.ACTIVE)).limit(1))
        return not failed and remaining is None

    @asynccontextmanager
    async def cleanup_scope(self, scope: str, scope_id: int, reason: str, actor_id: int | None = None,
                            *, expected_revision: int | None = None, resource_revision: int | None = None,
                            confirmation: bool | None = None):
        """冻结后逆序逐项回收，失败继续；资源变更提交前保持门槛。

        Args:
            scope：World/会话/工作区。
            scope_id：范围身份。
            reason：删除、换绑、退出等已登记原因。
            actor_id：Owner 或系统。
            expected_revision：配置停用的预期版本，冻结前原子复核。
            resource_revision：会话换绑时的资源版本。
            confirmation：交互操作的范围回收确认，系统清理为空。
        """
        async with self.cleanup_lock:
            async def prepare():
                """冻结与处理放在同一宿主任务，取消不会遗失刚提交的清单身份。"""
                operation, targets = await registry.begin_cleanup(scope, scope_id, reason=reason,
                    actor_id=actor_id, expected_revision=expected_revision, resource_revision=resource_revision,
                    confirmation=confirmation)
                try:
                    complete = await self._drain_cleanup(operation, targets)
                except BaseException as exc:
                    await registry.end_cleanup(operation.id, success=False,
                        error_code=operation.error_code or (exc.code if isinstance(exc, WorkspaceCommandError) else 'RUNTIME_STATE_UNAVAILABLE'))
                    raise
                return operation, complete
            preparing = asyncio.create_task(prepare(), context=copy_context())
            try:
                operation, complete = await settled(preparing)
                if not complete:
                    raise registry.RuntimeRejected('RUNTIME_CLEANUP_UNCONFIRMED')
                yield operation.id
            except BaseException as exc:
                if preparing.done() and not preparing.cancelled() and preparing.exception() is None:
                    operation, _complete = preparing.result()
                    await settled(asyncio.create_task(registry.end_cleanup(operation.id, success=False,
                        error_code=operation.error_code or (exc.code if isinstance(exc, WorkspaceCommandError) else None)
                        or ('RUNTIME_AUDIT_UNAVAILABLE' if getattr(operation, 'audit_failed', False) else None)), context=copy_context()))
                raise
            else:
                await settled(asyncio.create_task(registry.end_cleanup(operation.id, success=True), context=copy_context()))

    async def shutdown(self, reason: str = 'application_shutdown'):
        """先关闭新预留，再逐项清理；由 lifespan 在关闭 DB/日志之前调用。

        Args:
            reason：可信调用方提供的应用退出或 World 切换原因。
        """
        try:
            async with SessionLocal() as session:
                await registry.gate(session)
                await session.execute(update(RuntimeGate).where(RuntimeGate.id == 1).values(closing=True))
                await session.commit()
            async with self.cleanup_scope('world', 0, reason):
                pass
        except Exception:
            # 审计/数据库故障不能使内存里已经持有的进程句柄失去清理机会。
            for host in list(self.hosts.values()):
                host.reason = 'shutdown_fallback'
                host.stop.set()
                if host.control_fd is not None:
                    os.close(host.control_fd)
                    host.control_fd = None
                try:
                    await self._wait_host(host)
                except Exception:
                    pass
            raise


manager = RuntimeManager()
