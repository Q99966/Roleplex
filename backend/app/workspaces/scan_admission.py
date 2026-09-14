"""只读扫描的有界 FIFO 准入；同一宿主任务借用已有槽位，不形成嵌套队列。"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from weakref import WeakKeyDictionary


class ScanAdmission:
    """一个事件循环的扫描池；等待者和实际任务都有固定上限。"""

    def __init__(self, capacity: int = 2, waiting: int = 8, timeout: float = 3):
        """Args:
            capacity：允许同时运行的宿主调用数。
            waiting：等待者数量上限。
            timeout：单次最长等待秒数。
        """
        self.capacity, self.waiting, self.timeout = capacity, waiting, timeout
        self.active = set()
        self.queue = deque()
        self.queued_bytes = 0
        self.closed = False

    def release(self, task) -> None:
        """释放后按 FIFO 移交槽位，未恢复的等待者也占用预留容量。

        Args:
            task：原宿主任务，不是模型传入的身份。
        """
        self.active.discard(task)
        while self.queue and len(self.active) < self.capacity and not self.closed:
            future, owner, size = self.queue.popleft()
            self.queued_bytes -= size
            if future.done() or owner.done():
                continue
            self.active.add(owner)
            future.set_result(None)

    @asynccontextmanager
    async def slot(self, size: int = 0):
        """取得容量或明确失败；取消与被唤醒竞争时也必须归还槽位。

        Args:
            size：已验证的请求 UTF-8 字节数。
        """
        from .files import WorkspaceFileError
        task = asyncio.current_task()
        if self.closed:
            raise WorkspaceFileError('WORKSPACE_SCAN_CLOSED')
        if len(self.active) < self.capacity and not self.queue:
            self.active.add(task)
        else:
            if len(self.queue) >= self.waiting or self.queued_bytes + size > 128 * 1024:
                raise WorkspaceFileError('WORKSPACE_SCAN_BUSY')
            future = asyncio.get_running_loop().create_future()
            entry = (future, task, size)
            self.queue.append(entry)
            self.queued_bytes += size
            try:
                await asyncio.wait_for(asyncio.shield(future), self.timeout)
            except BaseException as exc:
                if entry in self.queue:
                    self.queue.remove(entry)
                    self.queued_bytes -= size
                future.cancel()
                self.release(task)
                if isinstance(exc, TimeoutError):
                    raise WorkspaceFileError('WORKSPACE_SCAN_QUEUE_TIMEOUT') from None
                raise
        try:
            yield
        finally:
            self.release(task)

    async def close(self) -> None:
        """关闭等待者，取消活跃扫描并等待它们归还资源。"""
        from .files import WorkspaceFileError
        self.closed = True
        while self.queue:
            future, _, size = self.queue.popleft()
            self.queued_bytes -= size
            if not future.done():
                future.set_exception(WorkspaceFileError('WORKSPACE_SCAN_CLOSED'))
        tasks = [task for task in self.active if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_pools = WeakKeyDictionary()
_owner: ContextVar[object | None] = ContextVar('workspace_scan_owner', default=None)
_metrics: ContextVar[dict | None] = ContextVar('workspace_scan_metrics', default=None)


def record_scanned(size: int) -> None:
    """Args:
        size：本调用实际读出的字节数，只累计不逐块记录日志。
    """
    value = _metrics.get()
    if value is not None and _owner.get() is asyncio.current_task():
        value['scanned_bytes'] += size


def current_pool() -> ScanAdmission:
    """每个实际事件循环独立持有准入状态，测试轮次不会复用旧循环。"""
    loop = asyncio.get_running_loop()
    if loop not in _pools:
        _pools[loop] = ScanAdmission()
    return _pools[loop]


@asynccontextmanager
async def admitted(size: int = 0):
    """同一调用的内部顺序扫描共享槽位；新任务不能继承准入授权。

    Args:
        size：已校验请求的字节数。
    """
    task = asyncio.current_task()
    if _owner.get() is task:
        yield
        return
    started, entered, status = monotonic(), None, 'success'
    metric = {'scanned_bytes': 0}
    try:
        async with current_pool().slot(size):
            entered = monotonic()
            token = _owner.set(task)
            metric_token = _metrics.set(metric)
            try:
                yield
            finally:
                _owner.reset(token)
                _metrics.reset(metric_token)
    except BaseException as exc:
        status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'rejected' if entered is None else 'failed'
        raise
    finally:
        from ..agent.tool_context import tool_call_id
        call_id = tool_call_id.get()
        if call_id:
            now = monotonic()
            logging.getLogger('roleplex.workspaces.scan').info('tool.scan_completed', extra={
                'tool_call_id': call_id, 'status': status,
                'queue_wait_ms': round(((entered or now) - started) * 1000, 2),
                'scan_duration_ms': round((now - entered) * 1000, 2) if entered else 0, **metric})


async def close_current_pool() -> None:
    """服务退出后关闭本轮扫描池，不保留等待者。"""
    pool = _pools.pop(asyncio.get_running_loop(), None)
    if pool is not None:
        await pool.close()
