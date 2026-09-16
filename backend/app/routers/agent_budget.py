"""Owner 当前 World 的预算默认值；更新不改变既有消息链。"""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from ..config.decision_budget import MAX_DECISION_LIMIT
from ..db import SessionLocal, with_locked_retry
from ..models import InstanceSettings, User
from ..security import require_owner

router = APIRouter(prefix='/api/agent-budget', tags=['agent-budget'])


class BudgetConfiguration(BaseModel):
    """正整数或显式 null（不限）与乐观锁版本，不接受其他作用域。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    decision_limit: int | None = Field(..., ge=1, le=MAX_DECISION_LIMIT)
    expected_revision: int = Field(ge=0)


def view(row):
    """Args:
        row：当前 World 单例配置，仅返回预算白名单字段。
    """
    return {'decision_limit': row.decision_limit, 'effective_limit': row.decision_limit,
        'ceiling': None, 'revision': row.budget_revision}


@router.get('/config')
async def get_config(response: Response, user: Annotated[User, Depends(require_owner)]):
    """Args:
        response：禁止浏览器缓存账户级配置。
        user：服务端已认证 Owner。
    """
    response.headers['Cache-Control'] = 'no-store'
    async with SessionLocal() as session:
        return view(await session.get(InstanceSettings, 1))


@router.put('/config')
async def put_config(payload: BudgetConfiguration, response: Response, user: Annotated[User, Depends(require_owner)]):
    """Args:
        payload：额度与读取时的修订号。
        response：禁止缓存结果。
        user：仅 Owner 可修改，Guest 没有入口。
    """
    response.headers['Cache-Control'] = 'no-store'
    async def operation():
        """CAS 更新同 World 配置，暂时锁定可安全重试。"""
        async with SessionLocal() as session:
            result = await session.execute(update(InstanceSettings).where(InstanceSettings.id == 1,
                InstanceSettings.budget_revision == payload.expected_revision).values(
                decision_limit=payload.decision_limit, budget_revision=InstanceSettings.budget_revision + 1))
            if result.rowcount != 1:
                raise HTTPException(409, 'AGENT_BUDGET_REVISION_CONFLICT')
            row = await session.get(InstanceSettings, 1)
            value = view(row)
            await session.commit()
            return value
    return await with_locked_retry(operation)
