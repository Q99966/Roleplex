"""模型调用用量的业务记录和汇总，日志不作为数据源，缺失不估算。"""
import logging
from datetime import datetime,timezone
from sqlalchemy import select,update,func,case
from ..models import AgentExecution,Generation,Message,ModelCallUsage

logger=logging.getLogger('roleplex.agent.usage')
METRICS=('input_tokens','output_tokens','cache_hit_tokens','cache_write_tokens')
SUMMARY_METRICS={**{name:name for name in METRICS},'model_duration_ms':'duration_ms'}


def _number(value):
    """Args:
        value：厂商归一化数值，异常范围按未知处理，不溢出数据库整数。
    """
    return value if type(value) is int and 0<=value<=2**63-1 else None


async def record(execution_id:str,event,provider_mode:str,model_name:str,*,completed:bool=False,context_snapshot:dict|None=None):
    """开始/完成更新同一记录；采集失败不终止已授权任务。

    Args:
        execution_id：既有执行标识。
        event：领域 ProviderCallStarted/Completed。
        provider_mode：宿主明确的 real/fake，不来自模型参数。
        model_name：本次实际配置模型名快照。
        completed：是否收到了完整调用结束事件。
        context_snapshot：ContextBuilder 生成的版本/指纹白名单记录，不包含 Prompt 正文。
    """
    from ..db import SessionLocal,with_locked_retry
    from ..agent.argument_errors import safe_exception_type
    async def operation():
        """只写白名单数值；同一消息所有者顺序更新，重试不重复累计。"""
        async with SessionLocal() as session:
            execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==execution_id))
            if execution is None:return
            if context_snapshot is not None and execution.context_snapshot_json is None:
                execution.context_snapshot_json=context_snapshot
            row=await session.scalar(select(ModelCallUsage).where(ModelCallUsage.execution_id==execution_id,ModelCallUsage.call_index==event.call_index))
            if row is None:
                row=ModelCallUsage(execution_id=execution_id,call_index=event.call_index,
                    provider_mode=provider_mode,model_name=model_name,status='started',recorded_at=datetime.now(timezone.utc))
                session.add(row)
            if event.call_index==1 and execution.decision_count<=1:
                execution.usage_tracked=True
            if not completed and row.input_estimate_json is None:
                row.input_estimate_json = getattr(event, 'input_estimate', None)
            if completed and row.status!='completed':
                row.status='completed'
                row.duration_ms=_number(event.duration_ms)
                for name in METRICS:
                    setattr(row,name,_number(getattr(event,name,None)) if provider_mode=='real' else None)
            await session.commit()
    try:
        await with_locked_retry(operation)
    except Exception as exc:
        logger.warning('usage.record_failed',extra={'execution_id':execution_id,'call_index':event.call_index,'error_type':safe_exception_type(exc)})


async def close_pending(session,generation_id:int|None=None):
    """终态或重启后将未完整结束的观测标为未知，保留已完成数值。

    Args:
        session：现有短事务。
        generation_id：指定收口的生成，重启时省略并收口全部残留。
    """
    ids=select(AgentExecution.execution_id)
    if generation_id is not None:ids=ids.where(AgentExecution.generation_id==generation_id)
    await session.execute(update(ModelCallUsage).where(ModelCallUsage.execution_id.in_(ids),ModelCallUsage.status=='started').values(status='unconfirmed'))


async def finish(generation_id:int):
    """在主生命周期之外收口观测，失败不回滚已经结束的消息。

    Args:
        generation_id：已收口或取消的生成。
    """
    from ..db import SessionLocal,with_locked_retry
    from ..agent.argument_errors import safe_exception_type
    async def operation():
        """用独立短事务标记未完整记录的调用。"""
        async with SessionLocal() as session:
            await close_pending(session,generation_id)
            await session.commit()
    try:
        await with_locked_retry(operation)
    except Exception as exc:
        logger.warning('usage.record_failed',extra={'generation_id':generation_id,'error_type':safe_exception_type(exc)})


async def summary(session,conversation_id:int,role_id:int,execution_id:str|None=None,*,chains=None):
    """用可迁移聚合查询汇总，不扫描日志或加载历史正文。

    Args:
        session：已完成归属校验的只读会话。
        conversation_id：会话范围。
        role_id：角色范围。
        execution_id：可选单次执行范围。
        chains：可选宿主已鉴权的世界任务链集合；提供时替代会话/角色筛选，不是公开查询参数。
    """
    filters=[AgentExecution.chain_id.in_(chains)] if chains is not None else [AgentExecution.conversation_id==conversation_id,AgentExecution.role_id==role_id]
    if execution_id is not None:filters.append(AgentExecution.execution_id==execution_id)
    per_execution=(select(ModelCallUsage.execution_id,func.count(ModelCallUsage.id).label('calls'))
        .join(AgentExecution,AgentExecution.execution_id==ModelCallUsage.execution_id).where(*filters)
        .group_by(ModelCallUsage.execution_id).subquery())
    missing=AgentExecution.decision_count-func.coalesce(per_execution.c.calls,0)
    row=(await session.execute(select(func.count(AgentExecution.id),
        func.sum(case((AgentExecution.usage_tracked.is_(False),1),else_=0)),
        func.sum(case((missing>0,missing),else_=0))).outerjoin(per_execution,per_execution.c.execution_id==AgentExecution.execution_id)
        .where(*filters))).one()
    executions,untracked,gap=int(row[0]),int(row[1] or 0),int(row[2] or 0)
    columns=[func.count(ModelCallUsage.id),func.sum(case((ModelCallUsage.status=='completed',1),else_=0))]
    for column in SUMMARY_METRICS.values():columns.extend([func.sum(getattr(ModelCallUsage,column)),func.count(getattr(ModelCallUsage,column))])
    # 只有同一次调用的输入与缓存命中均有效，才参与差值和比例，避免跨调用错配。
    paired=(ModelCallUsage.input_tokens.is_not(None) & ModelCallUsage.cache_hit_tokens.is_not(None)
        & (ModelCallUsage.cache_hit_tokens>=0) & (ModelCallUsage.cache_hit_tokens<=ModelCallUsage.input_tokens))
    columns.extend([func.count(case((paired,1))),
        func.sum(case((paired,ModelCallUsage.input_tokens))),
        func.sum(case((paired,ModelCallUsage.cache_hit_tokens)))])
    values=(await session.execute(select(*columns).join(AgentExecution,AgentExecution.execution_id==ModelCallUsage.execution_id).where(*filters))).one()
    calls,completed=int(values[0]),int(values[1] or 0)
    untracked_messages=0
    if execution_id is None and chains is None:
        linked=select(AgentExecution.id).join(Generation,Generation.id==AgentExecution.generation_id).where(Generation.assistant_message_id==Message.id).exists()
        untracked_messages=int(await session.scalar(select(func.count(Message.id)).where(
            Message.conversation_id==conversation_id,Message.sender_type=='role',Message.sender_id==role_id,~linked)) or 0)
    metrics={}
    for i,name in enumerate(SUMMARY_METRICS):
        known,count=values[2+2*i],int(values[3+2*i])
        complete=untracked==0 and untracked_messages==0 and gap==0 and count==calls
        no_calls=untracked_messages==0 and (executions==0 or (untracked==0 and gap==0 and calls==0))
        metrics[name]={'total':int(known or 0) if complete else None,
            'known':int(known) if count else 0 if no_calls else None,'missing_calls':calls-count+gap}
    count,paired_input,paired_hit=int(values[-3]),int(values[-2] or 0),int(values[-1] or 0)
    complete=untracked==0 and untracked_messages==0 and gap==0 and count==calls
    known_miss=paired_input-paired_hit if count else 0 if no_calls else None
    known_ratio=paired_hit/paired_input if paired_input>0 else None
    metrics['cache_miss_tokens']={'total':known_miss if complete else None,
        'known':known_miss,'missing_calls':calls-count+gap}
    metrics['input_cache_hit_ratio']={'total':known_ratio if complete else None,
        'known':known_ratio,'missing_calls':calls-count+gap}
    return {'executions':executions,'untracked_executions':untracked,'untracked_messages':untracked_messages,'recorded_calls':calls,
        'completed_calls':completed,'missing_call_records':gap,'metrics':metrics}


async def role_usage(session,conversation_id:int,role_id:int):
    """Args:
        session：已鉴权的只读会话。
        conversation_id：当前会话。
        role_id：当前角色。
    """
    row=(await session.execute(select(AgentExecution.execution_id,AgentExecution.execution_kind,Generation,Message.meta_json)
        .join(Generation,Generation.id==AgentExecution.generation_id)
        .outerjoin(Message,Message.id==Generation.assistant_message_id)
        .where(AgentExecution.conversation_id==conversation_id,AgentExecution.role_id==role_id)
        .order_by(AgentExecution.id.desc()).limit(1))).first()
    latest=None
    if row:
        current_execution_id,execution_kind,generation,message_meta=row
        model=await session.scalar(select(ModelCallUsage.model_name).where(ModelCallUsage.execution_id==current_execution_id).order_by(ModelCallUsage.call_index.desc()).limit(1))
        duration=None
        if generation.started_at and generation.ended_at:
            elapsed=(generation.ended_at-generation.started_at).total_seconds()*1000
            duration=int(elapsed) if elapsed>=0 else None
        latest={'execution_id':current_execution_id,'execution_kind':execution_kind,'message_id':generation.assistant_message_id,
            'status':generation.status,'error_code':generation.error_code,'stop_reason':(message_meta or {}).get('stop_reason'),
            'model_name':model,'duration_ms':duration,
            'summary':await summary(session,conversation_id,role_id,current_execution_id)}
    return {'latest':latest,'cumulative':await summary(session,conversation_id,role_id)}
