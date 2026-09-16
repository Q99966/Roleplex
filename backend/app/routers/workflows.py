"""Owner 会话工作流接口；公开 WS 仅提示版本，私有任务始终由已授权 REST 返回。"""
from typing import Annotated
from fastapi import APIRouter, Depends, Response, Path
from ..models import User
from ..security.tokens import require_owner
from ..workflows import service
from ..workflows.schemas import Save, Start, Control

router = APIRouter(prefix='/api/conversations/{conversation_id}/workflows', tags=['workflows'])
Owner = Annotated[User, Depends(require_owner)]
Id = Annotated[str, Path(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')]


@router.get('')
async def list_workflows(conversation_id: int, owner: Owner, response: Response):
    """完整恢复本会话定义与运行快照，不返回其他会话资源。"""
    response.headers['Cache-Control'] = 'no-store'
    return await service.listing(conversation_id, owner.id)


@router.put('/definitions/{definition_id}')
async def save_workflow(conversation_id: int, definition_id: Id, payload: Save, owner: Owner):
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
