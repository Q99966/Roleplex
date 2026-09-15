"""原生修改的独立 FIFO 准入，排队与写锁共用累计等待预算。"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from weakref import WeakKeyDictionary

from .files import WorkspaceFileError


@dataclass
class WaitBudget:
    """只累计等待，不把文件提交或 diff 计算放入超时取消范围。"""
    limit: float = 30.0
    queue_wait: float = 0.0
    lock_wait: float = 0.0
    failure: dict = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        """返回此次调用尚可用于等待的时间。"""
        return max(0.0, self.limit - self.queue_wait - self.lock_wait)


def busy(phase: str, reason: str) -> WorkspaceFileError:
    """Args:
        phase：固定 queue/lock 阶段。
        reason：已登记的有界等待原因。
    """
    return WorkspaceFileError('WORKSPACE_BATCH_BUSY', details={'phase': phase, 'reason': reason})


class WriteAdmission:
    """请求数量和队列参数字节同时有界，移交和取消不会泄漏容量。"""

    def __init__(self, capacity: int = 2, waiting: int = 8, queued_limit: int = 2 * 1024 * 1024, timeout: float = 30):
        """Args:
            capacity：活跃修改调用上限。
            waiting：等待调用数量上限。
            queued_limit：等待参数的 UTF-8 JSON 总量上限。
            timeout：排队与锁等待的累计预算。
        """
        self.capacity, self.waiting, self.queued_limit, self.timeout = capacity, waiting, queued_limit, timeout
        self.active = set()
        self.queue = deque()
        self.queued_bytes = 0
        self.closed = False

    def release(self, task) -> None:
        """Args:
            task：原宿主任务，移交时先计入活跃集合再唤醒。
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
    async def slot(self, size: int, budget: WaitBudget | None = None):
        """Args:
            size：已校验的完整参数 JSON 字节数。
            budget：宿主调用共用预算，不接受模型传入。
        """
        task = asyncio.current_task()
        budget = budget or WaitBudget(self.timeout)
        if self.closed:
            raise busy('queue', 'closed')
        if len(self.active) < self.capacity and not self.queue:
            self.active.add(task)
        else:
            if len(self.queue) >= self.waiting:
                raise busy('queue', 'queue_full')
            if self.queued_bytes + size > self.queued_limit:
                raise busy('queue', 'queue_bytes')
            future = asyncio.get_running_loop().create_future()
            entry = (future, task, size)
            self.queue.append(entry)
            self.queued_bytes += size
            started = monotonic()
            try:
                await asyncio.wait_for(asyncio.shield(future), budget.remaining)
            except BaseException as exc:
                if entry in self.queue:
                    self.queue.remove(entry)
                    self.queued_bytes -= size
                future.cancel()
                self.release(task)
                if isinstance(exc, TimeoutError):
                    raise busy('queue', 'queue_timeout') from None
                raise
            finally:
                budget.queue_wait += monotonic() - started
        try:
            yield
        finally:
            self.release(task)

    async def close(self) -> None:
        """关闭等待者并取消活跃宿主，等待所有资源归还。"""
        self.closed = True
        while self.queue:
            future, _, size = self.queue.popleft()
            self.queued_bytes -= size
            if not future.done():
                future.set_exception(busy('queue', 'closed'))
        tasks = [task for task in self.active if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_pools = WeakKeyDictionary()
_state: ContextVar[tuple | None] = ContextVar('workspace_write_wait', default=None)


def current_pool() -> WriteAdmission:
    """按事件循环隔离，测试与重启不复用已经关闭的池。"""
    loop = asyncio.get_running_loop()
    if loop not in _pools:
        _pools[loop] = WriteAdmission()
    return _pools[loop]


@asynccontextmanager
async def admitted(size: int):
    """Args:
        size：经过 schema 校验的请求 JSON 字节数，不持久化原参数。
    """
    task = asyncio.current_task()
    state = _state.get()
    if state and state[0] is task:
        yield
        return
    budget = WaitBudget(current_pool().timeout)
    token = _state.set((task, budget))
    status, details = 'success', {}
    try:
        async with current_pool().slot(size, budget):
            yield
    except BaseException as exc:
        status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
        if isinstance(exc, WorkspaceFileError) and exc.code == 'WORKSPACE_BATCH_BUSY':
            details = exc.details or {}
        raise
    finally:
        if budget.failure:
            details = budget.failure
            if status == 'success':
                status = 'failed'
        _state.reset(token)
        from ..agent.tool_context import tool_call_id
        call_id = tool_call_id.get()
        if call_id:
            logging.getLogger('roleplex.agent').info('tool.write_wait_completed', extra={
                'event': 'tool.write_wait_completed', 'tool_call_id': call_id, 'status': status,
                'queue_wait_ms': round(budget.queue_wait * 1000, 2), 'lock_wait_ms': round(budget.lock_wait * 1000, 2), **details})


@asynccontextmanager
async def locked(lock: asyncio.Lock):
    """Args:
        lock：工作区共同命令锁，最终授权和提交在持锁范围内执行。
    """
    state = _state.get()
    budget = state[1] if state and state[0] is asyncio.current_task() else WaitBudget(current_pool().timeout)
    started = monotonic()
    try:
        await asyncio.wait_for(lock.acquire(), budget.remaining)
    except TimeoutError:
        budget.failure = {'phase': 'lock', 'reason': 'lock_timeout'}
        raise busy('lock', 'lock_timeout') from None
    finally:
        budget.lock_wait += monotonic() - started
    try:
        yield
    finally:
        lock.release()


async def close_current_pool() -> None:
    """关闭本事件循环池，取消排队请求，不能迟到写入。"""
    pool = _pools.get(asyncio.get_running_loop())
    if pool is not None:
        await pool.close()
