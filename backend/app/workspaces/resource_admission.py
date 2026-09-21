"""当前进程内的公平共享读/排他修改；按真实根而非工作区 ID 仲裁。

仅约束受控操作，不是 OS 沙箱。调用不在获取模型结果、人工确认或 diff 时持有占用。
等待有界且可取消，嵌套准入只允许同一 asyncio Task 已持有的相容访问，不继承给子任务。
"""
import asyncio
import os
import logging
from time import monotonic
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from ..config import settings


@dataclass(eq=False)
class Claim:
    path: Path
    identity: tuple[int, int]
    mode: str
    task: object


_held = ContextVar('workspace_resource_claims', default=())


def overlaps(a, b):
    return a.identity == b.identity or a.path == b.path or a.path in b.path.parents or b.path in a.path.parents


def conflicts(a, b):
    return overlaps(a, b) and ('write' in (a.mode, b.mode))


class ResourceAdmission:
    """先到的冲突写者阻止后来读者插队；无冲突根不相互阻塞。"""
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = []
        self.waiting = []
        self.closed = False

    @asynccontextmanager
    async def claim(self, root, mode, execution_id=None):
        from .files import WorkspaceFileError
        try:
            canonical = Path(os.path.normcase(str(Path(root).resolve(strict=True))))
            stat = canonical.stat()
        except (OSError, ValueError):
            raise WorkspaceFileError('WORKSPACE_UNAVAILABLE') from None
        claim = Claim(canonical, (stat.st_dev, stat.st_ino), mode, asyncio.current_task())
        for parent in _held.get():
            if parent.task is claim.task and overlaps(parent, claim) and parent.mode == 'read' and mode == 'write':
                raise WorkspaceFileError('WORKSPACE_RESOURCE_UPGRADE_REQUIRED')
            if parent.task is claim.task and overlaps(parent, claim) and (parent.mode == 'write' or mode == 'read'):
                yield
                return
        token = None
        waiting_reported = False
        wait_started = monotonic()
        wait_status = 'cancelled'
        wait_finished = None
        try:
            async with self.condition:
                if self.closed: raise WorkspaceFileError('WORKSPACE_RESOURCE_CLOSED')
                self.waiting.append(claim)
            async with asyncio.timeout(settings.workspace_resource_wait_seconds):
                while True:
                    notify_wait = False
                    async with self.condition:
                        if self.closed: raise WorkspaceFileError('WORKSPACE_RESOURCE_CLOSED')
                        before = self.waiting[:self.waiting.index(claim)]
                        if not any(conflicts(claim, held) for held in self.active + before):
                            self.waiting.remove(claim); self.active.append(claim)
                            break
                        if not waiting_reported:
                            notify_wait = waiting_reported = True
                        else:
                            await self.condition.wait()
                    if notify_wait:
                        if execution_id: logging.getLogger('roleplex.resources').info('tool.resource_wait_started', extra={'execution_id': execution_id, 'reason': 'resource_' + mode, 'status': 'waiting'})
                        await report_wait(execution_id, mode)
            wait_finished = monotonic()
            wait_status = 'success'
            if waiting_reported: await report_wait(execution_id, None)
            token = _held.set((*_held.get(), claim))
            yield
        except TimeoutError:
            if wait_finished is not None:
                raise
            wait_status = 'failed'
            raise WorkspaceFileError('WORKSPACE_RESOURCE_TIMEOUT', {'phase': 'resource', 'reason': 'timeout'}) from None
        finally:
            if token is not None: _held.reset(token)
            async def cleanup():
                async with self.condition:
                    if claim in self.waiting: self.waiting.remove(claim)
                    if claim in self.active: self.active.remove(claim)
                    self.condition.notify_all()
                if waiting_reported:
                    await report_wait(execution_id, None)
                    if execution_id:
                        logging.getLogger('roleplex.resources').info('tool.resource_wait_completed', extra={
                            'execution_id': execution_id, 'reason': 'resource_' + mode,
                            'status': wait_status, 'duration_ms': round(((wait_finished or monotonic()) - wait_started) * 1000),
                        })
            from ..runtime.manager import settled
            await settled(asyncio.create_task(cleanup()))

    async def close(self):
        async with self.condition:
            self.closed = True
            self.condition.notify_all()


_current = None
_current_loop = None


def current():
    global _current, _current_loop
    loop = asyncio.get_running_loop()
    if _current is None or _current_loop is not loop or _current.closed:
        _current, _current_loop = ResourceAdmission(), loop
    return _current


async def report_wait(execution_id, mode):
    if not execution_id: return
    from sqlalchemy import update
    from ..db import SessionLocal, with_locked_retry
    from ..models import ExecutionAllocation
    async def write():
        async with SessionLocal() as session:
            await session.execute(update(ExecutionAllocation).where(ExecutionAllocation.execution_id == execution_id).values(waiting_mode=mode))
            await session.commit()
    await with_locked_retry(write)


def resource_operation(mode):
    """文件层准入保证普通聊天、工作流、别名和重叠根共用同一冲突边界。"""
    def decorate(function):
        @wraps(function)
        async def invoke(self, *args, **kwargs):
            async with current().claim(self.root, mode, self.execution_id if getattr(self, '_authorize', None) else None):
                if getattr(self, '_authorize', None) is not None:
                    await self._authorize()
                return await function(self, *args, **kwargs)
        return invoke
    return decorate


class OperationLock:
    """为原审批执行锁增加真实根协调；批准前不占用资源，保留原进程回收边界。"""
    def __init__(self, lock, root, execution_id, mode='write'):
        self.lock, self.root, self.execution_id, self.mode = lock, root, execution_id, mode
        self.context = None

    async def acquire(self):
        await self.lock.acquire()
        self.context = current().claim(self.root, self.mode, self.execution_id)
        try:
            await self.context.__aenter__()
        except BaseException as exc:
            self.lock.release(); self.context = None
            from .files import WorkspaceFileError
            if isinstance(exc, WorkspaceFileError):
                from .commands import WorkspaceCommandError
                raise WorkspaceCommandError(exc.code) from None
            raise
        return True

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *exc):
        try:
            if self.context: await self.context.__aexit__(*exc)
        finally:
            self.lock.release(); self.context = None
