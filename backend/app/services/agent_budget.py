"""消息链决策预算，数据库原子消费与执行序号共同防止重试重复扣减。"""
import asyncio
from sqlalchemy import select, update
from ..config import settings
from ..models import AgentExecution, Generation, InstanceSettings, WorkflowBudget


async def freeze(session, message):
    """与用户消息同事务冻结快照，客户端幂等重发不会创建新预算。

    Args:
        session：消息创建事务。
        message：已 flush 的用户消息与既有 chain 标识。
    """
    config = await session.get(InstanceSettings, 1)
    session.add(WorkflowBudget(chain_id=message.chain_id, conversation_id=message.conversation_id,
        trigger_message_id=message.id, decision_limit=min(config.decision_limit, settings.agent_decision_ceiling),
        configuration_revision=config.budget_revision, created_at=message.created_at))


async def limit_for(session, chain_id: str, conversation_id: int) -> int:
    """Args:
        session：只读业务会话。
        chain_id：当前执行已有的消息链。
        conversation_id：防止快照归属串线。
    """
    budget = await session.get(WorkflowBudget, chain_id)
    if budget is None or budget.conversation_id != conversation_id:
        raise ValueError('WORKFLOW_BUDGET_NOT_FOUND')
    return budget.decision_limit


async def consume(execution_id: str, index: int) -> bool:
    """请求模型前原子扣减；成功返回只授权当前宿主此次模型决策。

    Args:
        execution_id：既有单所有者执行标识，不对外提供任意扣减入口。
        index：该 execution 内从 1 开始的决策序号；只用于数据库重试幂等。
    """
    from ..db import SessionLocal, with_locked_retry
    async def operation():
        """在一个短事务中更新共享额度和 execution 序号。"""
        async with SessionLocal() as session:
            execution = await session.scalar(select(AgentExecution).where(AgentExecution.execution_id == execution_id))
            if execution is None:
                raise ValueError('WORKFLOW_BUDGET_EXECUTION_INVALID')
            generation = await session.get(Generation, execution.generation_id)
            if execution.status != 'running' or generation is None or generation.stop_requested_at is not None:
                raise asyncio.CancelledError()
            if execution.decision_count == index:
                return True
            if execution.decision_count != index - 1:
                raise ValueError('WORKFLOW_BUDGET_SEQUENCE_INVALID')
            result = await session.execute(update(WorkflowBudget).where(
                WorkflowBudget.chain_id == execution.chain_id,
                WorkflowBudget.conversation_id == execution.conversation_id,
                WorkflowBudget.used_decisions < WorkflowBudget.decision_limit,
            ).values(used_decisions=WorkflowBudget.used_decisions + 1))
            if result.rowcount != 1:
                return False
            updated = await session.execute(update(AgentExecution).where(AgentExecution.id == execution.id,
                AgentExecution.decision_count == index - 1).values(decision_count=index))
            if updated.rowcount != 1:
                await session.rollback()
                raise ValueError('WORKFLOW_BUDGET_SEQUENCE_INVALID')
            await session.commit()
            return True
    return await with_locked_retry(operation)
