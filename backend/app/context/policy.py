"""世界默认与会话覆盖；阈值受所有有效角色窗口约束，不依赖本轮 @ 顺序。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..config import settings
from ..models import Conversation, ConversationMember, InstanceSettings, ModelConfig, Role
from .fingerprint import stable_hash


class Policy(BaseModel):
    """Token 值沿用保守估算；reserve 只用于推荐值，实际请求另做硬预算检查。"""
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    trigger_tokens: int | None = Field(default=None, ge=256, le=10_000_000)
    reserve_tokens: int = Field(default=50_000, ge=0, le=10_000_000)
    target_tokens: int | None = Field(default=None, ge=128, le=10_000_000)
    summary_tokens: int = Field(default=1024, ge=128, le=100_000)
    keep_recent: int = Field(default=6, ge=0, le=200)
    model_role_id: int | None = Field(default=None, gt=0)
    instructions: str = Field(default='保留用户目标、已确认决定、约束、未完成事项、冲突与来源；明确区分已完成、未执行和结果未知。', max_length=10_000)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    min_new_tokens: int = Field(default=1024, ge=0, le=1_000_000)

    @model_validator(mode='after')
    def target_below_trigger(self):
        if self.trigger_tokens is not None and self.target_tokens is not None and self.target_tokens >= self.trigger_tokens:
            raise ValueError('目标必须小于触发阈值')
        return self


async def world_view(session):
    row = await session.get(InstanceSettings, 1)
    return {'revision': row.context_policy_revision, 'policy': Policy.model_validate(row.context_policy_json or {}).model_dump()}


async def resolve(session, cid):
    """动态重算成员/模型变化；保留原配置并显式返回收紧后的生效值。"""
    conversation = await session.get(Conversation, cid)
    world = await world_view(session)
    inherited = conversation.context_policy_json is None
    policy = Policy.model_validate(world['policy'] if inherited else conversation.context_policy_json).model_dump()
    roles = (await session.scalars(select(Role).join(ConversationMember,
        (ConversationMember.member_type == 'role') & (ConversationMember.member_id == Role.id)).join(
        ModelConfig, ModelConfig.id == Role.model_config_id).where(ConversationMember.conversation_id == cid,
        Role.active.is_(True), Role.deleted_at.is_(None), Role.created_by == conversation.created_by,
        ModelConfig.created_by == conversation.created_by).order_by(Role.id))).all()
    windows = [{'role_id': r.id, 'name': r.name, 'window_tokens': min(r.context_window_tokens, settings.max_context_tokens)} for r in roles]
    cap = min((r['window_tokens'] for r in windows), default=0)
    # 小窗口无法预留 50k 时推荐保留一半，界面展示实际 reserve，公式仍然成立。
    reserve = policy['reserve_tokens'] if policy['reserve_tokens'] < cap else cap // 2
    recommended = cap - reserve
    threshold = min(policy['trigger_tokens'] or recommended, cap)
    target = min(policy['target_tokens'] or threshold // 2, max(0, threshold - 1))
    notices = []
    if reserve != policy['reserve_tokens']:
        notices.append('reserve_adjusted')
    if policy['trigger_tokens'] is not None and policy['trigger_tokens'] > cap:
        notices.append('threshold_clamped')
    if policy['target_tokens'] is not None and policy['target_tokens'] >= threshold:
        notices.append('target_clamped')
    if not roles:
        notices.append('no_available_model')
    if policy['model_role_id'] is not None and policy['model_role_id'] not in {r.id for r in roles}:
        notices.append('model_unavailable')
    limits = {'ceiling_tokens': cap, 'recommended_reserve_tokens': reserve,
        'recommended_trigger_tokens': recommended, 'roles': windows}
    effective = {**policy, 'trigger_tokens': threshold, 'target_tokens': target,
        'enabled': policy['enabled'] and bool(roles) and 'model_unavailable' not in notices}
    view = {'conversation_id': cid, 'revision': conversation.context_policy_revision, 'world_revision': world['revision'],
        'inherited': inherited, 'policy': policy, 'effective': effective, 'limits': limits, 'notices': notices}
    view['stamp'] = stable_hash([view['revision'], view['world_revision'] if inherited else None, effective, windows])
    return view
