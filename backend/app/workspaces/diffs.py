"""进程内有界差异计算适配：不是 Agent 调度器，不占业务服务配额。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections import deque
from contextvars import copy_context
from pathlib import Path
from weakref import WeakKeyDictionary

from .commands import command_environment

INPUT_LIMIT = 256 * 1024
OUTPUT_LIMIT = 64 * 1024
WAIT_SECONDS = .25
COMPUTE_SECONDS = 1.0


def change_metadata(path: str, before: bytes | None, after: bytes) -> dict:
    """记录已提交的单文件身份，不包含前后全文。

    Args:
        path：授权后的规范相对路径。
        before：提交前字节；不存在为 None。
        after：确认提交的字节。
    """
    return {'version': 1, 'availability': 'recorded', 'reason': None, 'files': [{
        'id': 'file-0', 'path': path, 'operation': 'created' if before is None else 'unchanged' if before == after else 'modified',
        'applied': True, 'before_sha256': hashlib.sha256(before).hexdigest() if before is not None else None,
        'after_sha256': hashlib.sha256(after).hexdigest(), 'before_bytes': len(before or b''), 'after_bytes': len(after),
        'added': None, 'removed': None, 'hunks': [],
    }]}


class DiffTicket:
    """一次有界采集的所有权；调用结束必须释放输入与计算句柄。"""

    def __init__(self, value: dict, pool=None, before: bytes = b'', after: bytes = b''):
        """Args:
            value：提交身份或降级结果。
            pool：已预留容量的池；即时降级为 None。
            before：预算内旧字节。
            after：预算内新字节。
        """
        self.value, self.pool = value, pool
        self.before, self.after = before, after
        self.ready = asyncio.get_running_loop().create_future()
        self.task = None
        self.queued = False
        self.state = 'finished' if pool is None else 'waiting'
        self.enqueued_at = asyncio.get_running_loop().time()

    def unavailable(self, reason: str) -> dict:
        """只降级差异，不改已写入事实。

        Args:
            reason：协议允许的固定原因。
        """
        self.value.update(availability='unavailable', reason=reason)
        return self.value

    def release(self) -> None:
        """幂等释放输入及容量；活跃进程由 result 的 finally 先回收。"""
        if self.pool is None or self.state == 'finished':
            return
        if self.task is not None and not self.task.done() and self.task is not asyncio.current_task():
            return
        pool = self.pool
        pool.retained_bytes -= len(self.before) + len(self.after)
        self.before = self.after = b''
        if self.state == 'waiting':
            pool.queue.remove(self)
        else:
            pool.active -= 1
        self.state = 'finished'
        pool.tickets.discard(self)
        while pool.queue and pool.active < 2 and not pool.closing:
            candidate = pool.queue.popleft()
            candidate.state = 'reserved'
            pool.active += 1
            candidate.ready.set_result(None)

    async def result(self) -> dict:
        """释放写锁后调用；超时/失败返回降级，取消在完成回收后继续传播。"""
        if self.pool is None or self.state == 'finished':
            return self.value
        self.task = asyncio.current_task()
        process = spawning = writer = None
        try:
            if self.state == 'waiting':
                remaining = WAIT_SECONDS - (asyncio.get_running_loop().time() - self.enqueued_at)
                try:
                    await asyncio.wait_for(asyncio.shield(self.ready), max(.0001, remaining))
                except TimeoutError:
                    return self.unavailable('queue_timeout')
            if self.queued and asyncio.get_running_loop().time() - self.enqueued_at >= WAIT_SECONDS:
                return self.unavailable('queue_timeout')
            if self.pool.closing:
                return self.unavailable('shutdown')
            async with asyncio.timeout(COMPUTE_SECONDS):
                spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                    sys.executable, '-I', str(Path(__file__).with_name('diff_worker.py')),
                    env=command_environment(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL), context=copy_context())
                process = await asyncio.shield(spawning)
                self.pool.processes.add(process)
                payload = json.dumps({'metadata': self.value, 'before': self.before.decode('utf-8'),
                                      'after': self.after.decode('utf-8')}, ensure_ascii=False).encode()

                async def send() -> None:
                    """排空输入管道，不将正文写到临时文件或命令行。"""
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    process.stdin.close()

                writer = asyncio.create_task(send(), context=copy_context())
                raw = await process.stdout.read(OUTPUT_LIMIT + 1)
                # read 可能只取到一段，继续读取但不超过硬上限。
                while len(raw) <= OUTPUT_LIMIT:
                    chunk = await process.stdout.read(OUTPUT_LIMIT + 1 - len(raw))
                    if not chunk:
                        break
                    raw += chunk
                if len(raw) > OUTPUT_LIMIT:
                    return self.unavailable('capture_failed')
                await writer
                await process.wait()
                if process.returncode != 0:
                    return self.unavailable('capture_failed')
                from ..schemas import WriteDetailView
                value = WriteDetailView.model_validate_json(raw).model_dump()
                if any(value['files'][0][key] != self.value['files'][0][key] for key in
                       ('id', 'path', 'operation', 'applied', 'before_sha256', 'after_sha256', 'before_bytes', 'after_bytes')):
                    return self.unavailable('capture_failed')
                self.value = value
                return value
        except TimeoutError:
            return self.unavailable('compute_timeout')
        except asyncio.CancelledError:
            self.unavailable('shutdown' if self.pool.closing else 'cancelled')
            raise
        except UnicodeDecodeError:
            return self.unavailable('not_text')
        except Exception:
            return self.unavailable('capture_failed')
        finally:
            async def cleanup() -> None:
                """spawn 交接即使被取消也取得真实句柄后再回收。"""
                nonlocal process
                try:
                    if process is None and spawning is not None:
                        process = await spawning
                        self.pool.processes.add(process)
                    if process is not None:
                        if process.returncode is None:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                        await asyncio.wait_for(process.wait(), 1)
                        self.pool.processes.discard(process)
                except Exception:
                    # 无完整回收证明时关闭整个池的准入，不能另建池绕过容量边界。
                    self.pool.closing = True
                    self.unavailable('capture_failed')
                    for waiting in self.pool.queue:
                        if not waiting.ready.done():
                            waiting.ready.set_result(None)
                finally:
                    if writer is not None:
                        writer.cancel()
                        await asyncio.gather(writer, return_exceptions=True)
            cleanup_task = asyncio.create_task(cleanup(), context=copy_context())
            cancelled = False
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cancelled = True
            try:
                cleanup_task.result()
            finally:
                self.release()
            if cancelled:
                raise asyncio.CancelledError


class DiffPool:
    """最多两份计算、四份等待；原文总准入上限六份 256 KiB。"""

    def __init__(self):
        """创建当前生命周期的空准入池，不启动后台任务。"""
        self.active = 0
        self.retained_bytes = 0
        self.queue = deque()
        self.tickets = set()
        self.processes = set()
        self.closing = False

    @property
    def waiting(self) -> int:
        """返回当前等待项数量。"""
        return len(self.queue)

    def submit(self, before: bytes | None, after: bytes, metadata: dict) -> DiffTicket:
        """同步预留后才持有前后字节，不在写锁中启动或等待进程。

        Args:
            before：已确认的旧内容。
            after：本次已提交内容。
            metadata：本次写入事实。
        """
        old = before or b''
        reason = ('shutdown' if self.closing else 'input_budget' if len(old) + len(after) > INPUT_LIMIT
                  else 'line_budget' if old.count(b'\n') + after.count(b'\n') + int(bool(old) and not old.endswith(b'\n')) + int(bool(after) and not after.endswith(b'\n')) > 10000
                  else 'queue_full' if self.active >= 2 and self.waiting >= 4 else None)
        ticket = DiffTicket(metadata)
        if reason:
            ticket.unavailable(reason)
            return ticket
        ticket.pool, ticket.before, ticket.after = self, old, after
        ticket.state = 'reserved' if self.active < 2 else 'waiting'
        ticket.queued = ticket.state == 'waiting'
        self.retained_bytes += len(old) + len(after)
        self.tickets.add(ticket)
        if ticket.state == 'reserved':
            self.active += 1
            ticket.ready.set_result(None)
        else:
            self.queue.append(ticket)
        return ticket

    async def close(self) -> None:
        """关闭准入，取消并等待本池计算完成真实回收。"""
        self.closing = True
        tasks = []
        for ticket in list(self.tickets):
            if ticket.task is not None and not ticket.task.done():
                ticket.task.cancel()
                tasks.append(ticket.task)
            else:
                ticket.unavailable('shutdown')
                ticket.release()
        await asyncio.gather(*tasks, return_exceptions=True)
        for process in list(self.processes):
            try:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await asyncio.wait_for(process.wait(), 1)
                self.processes.discard(process)
            except Exception:
                pass
        if self.processes:
            raise RuntimeError('Diff workers not reaped')


_pools: WeakKeyDictionary = WeakKeyDictionary()


def current_pool() -> DiffPool:
    """池绑定当前事件循环；不同测试/应用生命周期不复用已关闭池。"""
    loop = asyncio.get_running_loop()
    if loop not in _pools:
        _pools[loop] = DiffPool()
    return _pools[loop]


async def close_current_pool() -> None:
    """应用关闭时只回收当前循环的技术计算进程。"""
    pool = _pools.get(asyncio.get_running_loop())
    if pool is not None:
        await pool.close()


def initialize_current_pool() -> None:
    """仅应用新生命周期可替换已完全回收的旧池；未确认资源保持阻断。"""
    loop = asyncio.get_running_loop()
    previous = _pools.get(loop)
    if previous is not None and (previous.processes or previous.tickets):
        raise RuntimeError('Diff resources remain from previous lifecycle')
    _pools[loop] = DiffPool()
