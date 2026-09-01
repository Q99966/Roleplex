"""M4a 每会话串行调度器。

内存队列只负责当前进程内的唤醒与顺序；`queue_jobs` 保存任务身份和终态，
不会被误用成全库写锁。不同会话拥有独立 worker，可以并行执行。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from ..db import SessionLocal
from ..config.logging import log_context
from ..models import Generation, QueueJob

logger = logging.getLogger("roleplex.scheduler.conversation")

GenerationRunner = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class _ActiveRun:
    """当前会话 worker 正在等待的单次角色执行。"""

    chain_id: str
    generation_id: int
    task: asyncio.Task[None]


class ConversationScheduler:
    """为每个会话维护一个串行 worker，并允许跨会话并行。"""

    def __init__(self) -> None:
        """创建尚未绑定应用生命周期的空调度器。"""
        self._runner: GenerationRunner | None = None
        self._queues: dict[int, asyncio.Queue[int]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._active: dict[int, _ActiveRun] = {}
        self._accepting = False

    async def start(self, runner: GenerationRunner) -> None:
        """绑定生成执行器，并降级上次进程遗留的任务。

        Args:
            runner：执行单个角色 generation 的异步函数。
        """
        await self.shutdown()
        self._runner = runner
        self._accepting = True
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            stale_ids = list((await session.scalars(select(Generation.id).where(
                Generation.status.in_(["queued", "running"]),
            ))).all())
            if stale_ids:
                await session.execute(
                    update(Generation)
                    .where(Generation.id.in_(stale_ids))
                    .values(status="stopped", ended_at=now)
                )
                await session.execute(
                    update(QueueJob)
                    .where(
                        QueueJob.generation_id.in_(stale_ids),
                        QueueJob.status.in_(["queued", "running"]),
                    )
                    .values(status="cancelled", cancel_requested=True, ended_at=now)
                )
                await session.commit()

    async def shutdown(self) -> None:
        """停止接收新任务，取消当前执行并回收全部会话 worker。"""
        self._accepting = False
        for active in list(self._active.values()):
            active.task.cancel()
        for worker in list(self._workers.values()):
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._queues.clear()
        self._workers.clear()
        self._active.clear()
        self._runner = None

    async def enqueue(self, conversation_id: int, job_id: int) -> None:
        """把已提交的持久任务放入所属会话队列。

        Args:
            conversation_id：任务所属会话。
            job_id：已经提交到 `queue_jobs` 的任务 ID。
        """
        if not self._accepting or self._runner is None:
            raise RuntimeError("CONVERSATION_SCHEDULER_NOT_RUNNING")
        queue = self._queues.setdefault(conversation_id, asyncio.Queue())
        await queue.put(job_id)
        worker = self._workers.get(conversation_id)
        if worker is None or worker.done():
            self._workers[conversation_id] = asyncio.create_task(self._worker(conversation_id, queue))

    async def stop_chain(self, conversation_id: int, chain_id: str) -> list[int]:
        """取消指定会话 chain 的当前执行和全部排队任务。

        Args:
            conversation_id：目标会话。
            chain_id：真人消息触发的共享链路 ID。

        Returns:
            按 generation ID 排序的受影响任务。
        """
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            generations = (await session.scalars(
                select(Generation)
                .where(
                    Generation.conversation_id == conversation_id,
                    Generation.run_id == chain_id,
                    Generation.status.in_(["queued", "running"]),
                )
                .order_by(Generation.id.asc())
            )).all()
            generation_ids = [generation.id for generation in generations]
            if not generation_ids:
                return []
            for generation in generations:
                generation.stop_requested_at = now
                if generation.status == "queued":
                    generation.status = "stopped"
                    generation.ended_at = now
            jobs = (await session.scalars(select(QueueJob).where(
                QueueJob.generation_id.in_(generation_ids),
                QueueJob.status.in_(["queued", "running"]),
            ))).all()
            for job in jobs:
                job.cancel_requested = True
                if job.status == "queued":
                    job.status = "cancelled"
                    job.ended_at = now
            await session.commit()

        active = self._active.get(conversation_id)
        if active is not None and active.chain_id == chain_id:
            active.task.cancel()
        return generation_ids

    async def _worker(self, conversation_id: int, queue: asyncio.Queue[int]) -> None:
        """顺序消费一个会话的持久任务 ID。

        Args:
            conversation_id：当前 worker 独占的会话 ID。
            queue：该会话的进程内唤醒队列。
        """
        try:
            while True:
                job_id = await queue.get()
                try:
                    try:
                        await self._run_job(conversation_id, job_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception(
                            "generation.queue_job_failed",
                            extra={"conversation_id": conversation_id, "queue_job_id": job_id, "status": "failed"},
                        )
                        await self._fail_job(job_id)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        finally:
            if self._workers.get(conversation_id) is asyncio.current_task():
                self._workers.pop(conversation_id, None)
                self._queues.pop(conversation_id, None)

    async def _run_job(self, conversation_id: int, job_id: int) -> None:
        """把一条 queued job 转成运行态，执行后写入对应终态。

        Args:
            conversation_id：任务所属会话。
            job_id：待执行的持久任务 ID。
        """
        if self._runner is None:
            return
        async with SessionLocal() as session:
            job = await session.get(QueueJob, job_id)
            if job is None or job.status != "queued" or job.cancel_requested:
                return
            generation = await session.get(Generation, job.generation_id) if job.generation_id else None
            if generation is None or generation.status != "queued":
                job.status = "cancelled"
                job.cancel_requested = True
                job.ended_at = datetime.now(timezone.utc)
                await session.commit()
                return
            payload = dict(job.payload_json or {})
            required = (
                "current_message_id", "target_role_id", "triggered_by_user_id",
                "execution_id", "chain_id", "allow_dangerous",
            )
            if not all(key in payload for key in required):
                job.status = "failed"
                job.ended_at = datetime.now(timezone.utc)
                generation.status = "failed"
                generation.error_code = "REQUEST_FAILED"
                generation.ended_at = job.ended_at
                await session.commit()
                return
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.attempts += 1
            await session.commit()

        with log_context(
            request_id=payload.get("request_id"),
            user_id=payload["triggered_by_user_id"],
            conversation_id=conversation_id,
            generation_id=job.generation_id,
            chain_id=payload["chain_id"],
            execution_id=payload["execution_id"],
            role_id=payload["target_role_id"],
        ):
            task = asyncio.create_task(
                self._runner(
                    generation_id=int(job.generation_id),
                    conversation_id=conversation_id,
                    current_message_id=int(payload["current_message_id"]),
                    target_role_id=int(payload["target_role_id"]),
                    triggered_by_user_id=int(payload["triggered_by_user_id"]),
                    allow_dangerous=bool(payload["allow_dangerous"]),
                    execution_id=str(payload["execution_id"]),
                    execution_kind=str(payload.get("execution_kind", "group_role")),
                )
            )
        active = _ActiveRun(
            chain_id=str(payload["chain_id"]),
            generation_id=int(job.generation_id),
            task=task,
        )
        self._active[conversation_id] = active
        try:
            await task
        except asyncio.CancelledError:
            # stop_chain 取消的是子执行；worker 本身继续消费并跳过已取消的后续 job。
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
        finally:
            if self._active.get(conversation_id) == active:
                self._active.pop(conversation_id, None)

        async with SessionLocal() as session:
            job = await session.get(QueueJob, job_id)
            generation = await session.get(Generation, job.generation_id) if job and job.generation_id else None
            if job is None:
                return
            if generation is None:
                final_status = "failed"
            else:
                final_status = {
                    "completed": "completed",
                    "failed": "failed",
                    "stopped": "cancelled",
                }.get(generation.status, "failed")
            job.status = final_status
            job.cancel_requested = job.cancel_requested or final_status == "cancelled"
            job.ended_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(
                "generation.queue_job_completed",
                extra={
                    "request_id": payload.get("request_id"),
                    "conversation_id": conversation_id,
                    "generation_id": job.generation_id,
                    "chain_id": payload["chain_id"],
                    "execution_id": payload["execution_id"],
                    "role_id": payload["target_role_id"],
                    "status": "cancelled" if final_status == "cancelled" else (
                        "success" if final_status == "completed" else "failed"
                    ),
                },
            )

    async def _fail_job(self, job_id: int) -> None:
        """把逃出调度边界的异常收敛为持久失败，避免 worker 静默死亡。

        Args:
            job_id：发生未处理异常的持久任务 ID。
        """
        async with SessionLocal() as session:
            job = await session.get(QueueJob, job_id)
            if job is None or job.status in {"completed", "failed", "cancelled"}:
                return
            now = datetime.now(timezone.utc)
            job.status = "failed"
            job.ended_at = now
            generation = await session.get(Generation, job.generation_id) if job.generation_id else None
            if generation is not None and generation.status in {"queued", "running"}:
                generation.status = "failed"
                generation.error_code = "REQUEST_FAILED"
                generation.ended_at = now
            await session.commit()


conversation_scheduler = ConversationScheduler()
