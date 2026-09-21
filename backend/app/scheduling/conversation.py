"""M4a 每会话串行调度器。

内存队列只负责当前进程内的唤醒与顺序；`queue_jobs` 保存唤醒参数和任务终态，
`agent_executions` 保存执行身份。二者都不会被误用成全库写锁，不同会话 worker 可以并行。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from ..db import SessionLocal, with_locked_retry
from ..config.logging import log_context
from ..models import AgentExecution, ExecutionWorkspace, Generation, QueueJob

logger = logging.getLogger("roleplex.scheduler.conversation")

GenerationRunner = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class _ActiveRun:
    """当前会话 worker 正在等待的单次角色执行。"""

    chain_id: str
    conversation_id: int
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
        self._parallel_queue = asyncio.Queue()
        self._parallel_workers = []
        self._parallel_pending = set()
        self._claims = set()

    async def start(self, runner: GenerationRunner) -> None:
        """绑定生成执行器，并降级上次进程遗留的任务。

        Args:
            runner：执行单个角色 generation 的异步函数。
        """
        await self.shutdown()
        self._runner = runner
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            stale_ids = list((await session.scalars(select(Generation.id).where(
                Generation.status.in_(["queued", "running"]),
            ))).all())
            # execution 有自己的终态事实；即使旧版本已先把 generation 写成 stopped，活跃 execution 也必须收口。
            stale_executions = (await session.scalars(select(AgentExecution).where(
                AgentExecution.status.in_(["queued", "running"]),
            ).order_by(AgentExecution.id))).all()
            stale_leases = (await session.scalars(select(ExecutionWorkspace).where(
                ExecutionWorkspace.status.in_(["creating", "ready"]),
            ))).all()
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
            if stale_executions:
                await session.execute(
                    update(AgentExecution)
                    .where(AgentExecution.id.in_([execution.id for execution in stale_executions]))
                    .values(status="interrupted", error_code="EXECUTION_INTERRUPTED", ended_at=now)
                )
            if stale_leases:
                await session.execute(
                    update(ExecutionWorkspace)
                    .where(ExecutionWorkspace.id.in_([lease.id for lease in stale_leases]))
                    .values(status="retained", error_code="EXECUTION_INTERRUPTED", ended_at=now)
                )
            if stale_ids or stale_executions or stale_leases:
                await session.commit()
            if stale_executions:
                for execution in stale_executions:
                    logger.info(
                        "execution.interrupted",
                        extra={
                            "conversation_id": execution.conversation_id,
                            "generation_id": execution.generation_id,
                            "chain_id": execution.chain_id,
                            "execution_id": execution.execution_id,
                            "role_id": execution.role_id,
                            "execution_kind": execution.execution_kind,
                            "status": "cancelled",
                            "error_code": "EXECUTION_INTERRUPTED",
                        },
                    )
        self._accepting = True
        from ..config import settings
        self._parallel_queue = asyncio.Queue()
        self._parallel_workers = [asyncio.create_task(self._parallel_worker()) for _ in range(settings.workflow_parallelism)]

    async def shutdown(self) -> None:
        """停止接收新任务，取消当前执行并回收全部会话 worker。"""
        self._accepting = False
        active_runs = list(self._active.values())
        for active in active_runs:
            if not active.task.cancelling(): active.task.cancel()
        for worker in [*self._workers.values(), *self._parallel_workers]:
            worker.cancel()
        if self._workers or self._parallel_workers:
            await asyncio.gather(*self._workers.values(), *self._parallel_workers, return_exceptions=True)
        self._parallel_workers.clear(); self._parallel_pending.clear(); self._claims.clear()
        if active_runs:
            await self._reconcile_shutdown_runs(active_runs)
        self._queues.clear()
        self._workers.clear()
        self._active.clear()
        self._runner = None

    async def _reconcile_shutdown_runs(self, active_runs: list[_ActiveRun]) -> None:
        """把服务关闭时被取消的 active run 收敛到 generation 对应终态。

        Args:
            active_runs：shutdown 开始前由本调度器实际持有的运行任务；不能全库扫描并影响其他调度器。
        """
        generation_ids = sorted({active.generation_id for active in active_runs})
        now = datetime.now(timezone.utc)
        interrupted: list[AgentExecution] = []
        async with SessionLocal() as session:
            generations = {
                generation.id: generation
                for generation in (await session.scalars(select(Generation).where(
                    Generation.id.in_(generation_ids),
                ))).all()
            }
            executions = (await session.scalars(select(AgentExecution).where(
                AgentExecution.generation_id.in_(generation_ids),
                AgentExecution.status.in_(["queued", "running"]),
            ))).all()
            execution_by_generation = {execution.generation_id: execution for execution in executions}
            jobs = (await session.scalars(select(QueueJob).where(
                QueueJob.generation_id.in_(generation_ids),
                QueueJob.status.in_(["queued", "running"]),
            ))).all()
            for generation_id in generation_ids:
                generation = generations.get(generation_id)
                execution = execution_by_generation.get(generation_id)
                if generation is None or execution is None:
                    continue
                if generation.status == "completed":
                    execution.status = "completed"
                    execution.error_code = None
                elif generation.status == "failed":
                    execution.status = "failed"
                    execution.error_code = generation.error_code or "REQUEST_FAILED"
                elif generation.status == "stopped":
                    execution.status = "stopped"
                    execution.error_code = None
                else:
                    # runner 未能在取消边界写终态时，不伪造 stopped 成功收口，明确记录进程中断。
                    generation.status = "stopped"
                    generation.ended_at = now
                    execution.status = "interrupted"
                    execution.error_code = "EXECUTION_INTERRUPTED"
                    interrupted.append(execution)
                execution.ended_at = generation.ended_at or now
            for job in jobs:
                generation = generations.get(int(job.generation_id)) if job.generation_id else None
                job.status = {
                    "completed": "completed",
                    "failed": "failed",
                }.get(generation.status if generation else "", "cancelled")
                job.cancel_requested = job.status == "cancelled"
                job.ended_at = now
            await session.commit()

        for execution in interrupted:
            logger.info(
                "execution.interrupted",
                extra={
                    "conversation_id": execution.conversation_id,
                    "generation_id": execution.generation_id,
                    "chain_id": execution.chain_id,
                    "execution_id": execution.execution_id,
                    "role_id": execution.role_id,
                    "execution_kind": execution.execution_kind,
                    "status": "cancelled",
                    "error_code": "EXECUTION_INTERRUPTED",
                },
            )

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

    async def enqueue_parallel(self, conversation_id: int, job_id: int):
        """已持久化工作流任务进入全局公平队列，容量与文件资源占用分离。"""
        if not self._accepting or self._runner is None:
            raise RuntimeError('CONVERSATION_SCHEDULER_NOT_RUNNING')
        if job_id not in self._parallel_pending:
            self._parallel_pending.add(job_id)
            await self._parallel_queue.put((conversation_id, job_id))

    async def _parallel_worker(self):
        """多 worker 只是同进程内执行槽，不扩展 Uvicorn 进程数量。"""
        while True:
            cid, jid = await self._parallel_queue.get()
            try:
                try:
                    await self._run_job(cid, jid)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning('generation.queue_job_failed', extra={'conversation_id': cid, 'queue_job_id': jid, 'status': 'failed'})
                    await self._fail_job(jid)
            finally:
                self._parallel_pending.discard(jid)
                self._parallel_queue.task_done()

    async def stop_chain(self, conversation_id: int, chain_id: str) -> list[int]:
        """停止指定链路的所有活跃/排队节点，普通 @ 仍沿用原入口。"""
        async with SessionLocal() as session:
            ids = list((await session.scalars(select(Generation.id).where(Generation.conversation_id == conversation_id,
                Generation.run_id == chain_id, Generation.status.in_(['queued', 'running'])))).all())
        return await self.stop_generations(conversation_id, ids)

    async def stop_generations(self, conversation_id: int, requested_ids: list[int]) -> list[int]:
        """取消明确的 generation 集合，不波及其他独立分支。

        Args:
            conversation_id：目标会话。
            requested_ids：已授权控制范围内的 generation ID。

        Returns:
            按 generation ID 排序的受影响任务。
        """
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            generations = (await session.scalars(
                select(Generation)
                .where(
                    Generation.conversation_id == conversation_id,
                    Generation.id.in_(requested_ids),
                    Generation.status.in_(["queued", "running"]),
                )
                .order_by(Generation.id.asc())
            )).all()
            generation_ids = [generation.id for generation in generations]
            if not generation_ids:
                return []
            for generation in generations:
                if generation.stop_requested_at is None: generation.stop_requested_at = now
                if generation.status == "queued":
                    generation.status = "stopped"
                    generation.ended_at = now
            await session.execute(
                update(AgentExecution)
                .where(
                    AgentExecution.generation_id.in_(generation_ids),
                    AgentExecution.status == "queued",
                )
                .values(status="stopped", error_code=None, ended_at=now)
            )
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

        for active in list(self._active.values()):
            if active.conversation_id == conversation_id and active.generation_id in generation_ids:
                if not active.task.cancelling(): active.task.cancel()
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
        """进程内去重与持久 CAS 共同认领，重复队列唤醒不会启动第二个 reducer。"""
        if job_id in self._claims: return
        self._claims.add(job_id)
        try:
            await self._run_claimed_job(conversation_id, job_id)
        finally:
            self._claims.discard(job_id)

    async def _run_claimed_job(self, conversation_id: int, job_id: int) -> None:
        """把一条 queued job 转成运行态，执行后写入对应终态。

        Args:
            conversation_id：任务所属会话。
            job_id：待执行的持久任务 ID。
        """
        if self._runner is None:
            return
        async def claim():
            """仅对认领短事务退避，绝不重跑已经调用模型或工具的 runner。"""
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
                execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == generation.id,
                ))
                if execution is None:
                    job.status = "failed"
                    job.ended_at = datetime.now(timezone.utc)
                    generation.status = "failed"
                    generation.error_code = "REQUEST_FAILED"
                    generation.ended_at = job.ended_at
                    await session.commit()
                    return
                payload = dict(job.payload_json or {})
                required = (
                    "current_message_id", "triggered_by_user_id", "allow_dangerous", "request_id",
                )
                execution_valid = (
                    execution.status == "queued"
                    and execution.conversation_id == conversation_id
                    and execution.chain_id == generation.run_id
                    and execution.role_id is not None
                )
                if not all(key in payload for key in required) or not execution_valid:
                    job.status = "failed"
                    job.ended_at = datetime.now(timezone.utc)
                    generation.status = "failed"
                    generation.error_code = "REQUEST_FAILED"
                    generation.ended_at = job.ended_at
                    execution.status = "failed"
                    execution.error_code = "REQUEST_FAILED"
                    execution.ended_at = job.ended_at
                    await session.commit()
                    return
                claimed = await session.scalar(update(QueueJob).where(QueueJob.id == job_id,
                    QueueJob.status == 'queued', QueueJob.cancel_requested.is_(False)).values(status='running').returning(QueueJob.id))
                if claimed is None:
                    await session.rollback()
                    return
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                job.attempts += 1
                execution.status = "running"
                execution.error_code = None
                execution.started_at = job.started_at
                await session.commit()
                return job, execution, payload
        claimed = await with_locked_retry(claim)
        if claimed is None: return
        job, execution, payload = claimed

        from ..workflows.service import permit_generation
        if not await permit_generation(int(job.generation_id)):
            await self._fail_job(job_id)
            return

        with log_context(
            request_id=payload.get("request_id"),
            user_id=payload["triggered_by_user_id"],
            conversation_id=conversation_id,
            generation_id=job.generation_id,
            chain_id=execution.chain_id,
            execution_id=execution.execution_id,
            role_id=execution.role_id,
        ):
            task = asyncio.create_task(
                self._runner(
                    generation_id=int(job.generation_id),
                    conversation_id=conversation_id,
                    current_message_id=int(payload["current_message_id"]),
                    target_role_id=int(execution.role_id),
                    triggered_by_user_id=int(payload["triggered_by_user_id"]),
                    allow_dangerous=bool(payload["allow_dangerous"]),
                    execution_id=execution.execution_id,
                    execution_kind=execution.execution_kind,
                )
            )
        active = _ActiveRun(
            conversation_id=conversation_id,
            chain_id=execution.chain_id,
            generation_id=int(job.generation_id),
            task=task,
        )
        self._active[int(job.generation_id)] = active
        try:
            await task
        except asyncio.CancelledError:
            # stop_chain 取消的是子执行；worker 本身继续消费并跳过已取消的后续 job。
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
        finally:
            if self._active.get(int(job.generation_id)) == active:
                self._active.pop(int(job.generation_id), None)

        async def finish():
            """并行完成只重试状态持久化，不重复模型调用与文件副作用。"""
            async with SessionLocal() as session:
                job = await session.get(QueueJob, job_id)
                generation = await session.get(Generation, job.generation_id) if job and job.generation_id else None
                execution = await session.scalar(select(AgentExecution).where(
                    AgentExecution.generation_id == job.generation_id,
                )) if job and job.generation_id else None
                if job is None:
                    return
                finished_at = datetime.now(timezone.utc)
                if generation is not None and generation.status not in {"completed", "failed", "stopped"}:
                    # runner 违反 reducer 契约直接返回时必须收敛三张状态表，不能留下 queued/running 假活跃记录。
                    generation.status = "failed"
                    generation.error_code = "REQUEST_FAILED"
                    generation.ended_at = finished_at
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
                job.ended_at = finished_at
                if execution is not None:
                    execution.status = {
                        "completed": "completed",
                        "failed": "failed",
                        "cancelled": "stopped",
                    }[final_status]
                    execution.error_code = (
                        generation.error_code or "REQUEST_FAILED"
                        if generation and final_status == "failed"
                        else None
                    )
                    execution.ended_at = job.ended_at
                await session.commit()
                logger.info(
                    "generation.queue_job_completed",
                    extra={
                        "request_id": payload.get("request_id"),
                        "conversation_id": conversation_id,
                        "generation_id": job.generation_id,
                        "chain_id": execution.chain_id if execution else None,
                        "execution_id": execution.execution_id if execution else None,
                        "role_id": execution.role_id if execution else None,
                        "status": "cancelled" if final_status == "cancelled" else (
                            "success" if final_status == "completed" else "failed"
                        ),
                    },
                )
        await with_locked_retry(finish)

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
            execution = await session.scalar(select(AgentExecution).where(
                AgentExecution.generation_id == job.generation_id,
            )) if job.generation_id else None
            if generation is not None and generation.status in {"queued", "running"}:
                generation.status = "failed"
                generation.error_code = "REQUEST_FAILED"
                generation.ended_at = now
            if execution is not None and execution.status in {"queued", "running"}:
                execution.status = "failed"
                execution.error_code = "REQUEST_FAILED"
                execution.ended_at = now
            await session.commit()


conversation_scheduler = ConversationScheduler()
