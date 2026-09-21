"""Owner 会话工作流接口；公开 WS 仅提示版本，私有任务始终由已授权 REST 返回。"""
from typing import Annotated
from fastapi import APIRouter, Depends, Response, Path
from ..models import User
from ..security.tokens import require_owner
from ..workflows import service
from ..workflows.schemas import Start, Control
from ..workflows.graph_schemas import SaveDraft, Coordinate, CancelCoordination, WriteGraph, EditGraph

router = APIRouter(prefix='/api/conversations/{conversation_id}/workflows', tags=['workflows'])
Owner = Annotated[User, Depends(require_owner)]
Id = Annotated[str, Path(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')]


@router.get('')
async def list_workflows(conversation_id: int, owner: Owner, response: Response):
    """完整恢复本会话定义与运行快照，不返回其他会话资源。"""
    response.headers['Cache-Control'] = 'no-store'
    return await service.listing(conversation_id, owner.id)


@router.put('/definitions/{definition_id}')
async def save_workflow(conversation_id: int, definition_id: Id, payload: SaveDraft, owner: Owner):
    """创建或修订定义；保存冲突需显式重新读取。"""
    return await service.save(conversation_id, owner.id, definition_id, payload)


@router.post('/runs', status_code=202)
async def start_workflow(conversation_id: int, payload: Start, owner: Owner):
    """幂等启动冻结版本，不扩展角色权限。"""
    return await service.start(conversation_id, owner.id, payload)


@router.post('/runs/{run_id}/control')
async def control_workflow(conversation_id: int, run_id: Id, payload: Control, owner: Owner):
    """精确控制选中运行和尝试；停止、确认与重试使用同一版本边界。"""
    return await service.control(conversation_id, owner.id, run_id, payload)


@router.get('/runs/{run_id}/attempts/{attempt_id}/facts')
async def workflow_facts(conversation_id: int, run_id: Id, attempt_id: Id, owner: Owner, response: Response):
    """Owner 核对选中尝试事实，不以模型宣称替代已提交证据。"""
    response.headers['Cache-Control'] = 'no-store'
    return await service.facts(conversation_id, owner.id, run_id, attempt_id)


@router.get('/runs/{run_id}/attempts/{attempt_id}/message')
async def workflow_message(conversation_id: int, run_id: Id, attempt_id: Id, owner: Owner, response: Response):
    """精确定位原消息；正文及工具卡仍由原消息 reducer 维护。"""
    from ..db import SessionLocal
    from ..models import WorkflowAttempt, Generation, Message
    from ..services.chat import message_payload
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        await service.get_run(session, conversation_id, owner.id, run_id)
        attempt = await session.get(WorkflowAttempt, attempt_id)
        if attempt is None or attempt.run_id != run_id:
            service.reject('WORKFLOW_ATTEMPT_NOT_FOUND', 404)
        generation = await session.get(Generation, attempt.generation_id) if attempt.generation_id else None
        message = await session.get(Message, generation.assistant_message_id) if generation and generation.assistant_message_id else None
        return message_payload(message) if message and message.conversation_id == conversation_id else None


@router.post('/coordination', status_code=202)
async def coordinate_workflow(conversation_id:int,payload:Coordinate,owner:Owner):
    """Owner 的明确规划/执行/重规划请求；普通 @ 不调用此入口。"""
    from ..workflows.planning import start
    return await start(conversation_id,owner.id,payload)


@router.post('/coordination/{coordination_id}/cancel')
async def cancel_coordination(conversation_id:int,coordination_id:Id,payload:CancelCoordination,owner:Owner):
    """取消指定协调请求；其已提交修改与历史执行保持可读。"""
    from ..workflows.planning import cancel
    return await cancel(conversation_id,owner.id,coordination_id,payload)


@router.get('/graphs/{kind}/{target_id}')
async def read_graph(conversation_id:int,kind:str,target_id:Id,owner:Owner,response:Response,graph_revision:int|None=None):
    """当前/历史图与精确版本，Owner 私有，不从最新图伪造历史。"""
    if kind not in ('definition','run'): service.reject('WORKFLOW_GRAPH_SCOPE',422)
    response.headers['Cache-Control']='no-store'
    from ..workflows.graph_service import read
    return await read(conversation_id,owner.id,kind,target_id,graph_revision=graph_revision)


@router.post('/graphs/{kind}/{target_id}/write')
async def write_graph(conversation_id:int,kind:str,target_id:Id,payload:WriteGraph,owner:Owner):
    """Owner 整图写入与模型工具共用提交服务。"""
    if kind not in ('definition','run'): service.reject('WORKFLOW_GRAPH_SCOPE',422)
    from ..workflows.graph_service import mutate
    return await mutate(conversation_id,owner.id,kind,target_id,payload)


@router.post('/graphs/{kind}/{target_id}/edit')
async def edit_graph(conversation_id:int,kind:str,target_id:Id,payload:EditGraph,owner:Owner):
    """按稳定 ID 原子修改；进度 revision 不作为图修改版本。"""
    if kind not in ('definition','run'): service.reject('WORKFLOW_GRAPH_SCOPE',422)
    from ..workflows.graph_service import mutate
    return await mutate(conversation_id,owner.id,kind,target_id,payload)


@router.get('/coordination/{coordination_id}/message')
async def coordination_message(conversation_id:int,coordination_id:Id,owner:Owner,response:Response):
    """Owner 查看准确协调执行的原始消息与工具卡，不依赖最近聊天窗口。"""
    from ..db import SessionLocal
    from ..models import CoordinationSession,AgentExecution,Generation,Message
    from sqlalchemy import select
    from ..services.chat import message_payload
    response.headers['Cache-Control']='no-store'
    async with SessionLocal() as session:
        await service.owned(session,conversation_id,owner.id)
        grant=await session.get(CoordinationSession,coordination_id)
        if not grant or grant.conversation_id!=conversation_id or grant.owner_id!=owner.id: service.reject('WORKFLOW_NOT_FOUND',404)
        execution=await session.scalar(select(AgentExecution).where(AgentExecution.execution_id==grant.execution_id))
        generation=await session.get(Generation,execution.generation_id) if execution else None
        message=await session.get(Message,generation.assistant_message_id) if generation and generation.assistant_message_id else None
        return message_payload(message) if message and message.conversation_id==conversation_id else None
