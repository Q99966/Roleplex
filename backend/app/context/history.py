"""从共享投影按预算选材；统计使用 SQL 聚合，正文只加载被采用的来源。"""
from dataclasses import dataclass

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConversationContextEntry as Entry, Message
from .budget import token_estimate
from .domain import ContextBuildError
from .projection import project_text


@dataclass(frozen=True)
class HistorySelection:
    messages: tuple
    sources: list[dict]
    total_tokens: int
    selected_tokens: int
    total_count: int


async def select_history(session: AsyncSession, *, conversation_id: int, role_id: int,
                         boundary, fixed_tokens: int, input_budget: int, pinned_budget: int,
                         summary_id: str | None = None) -> HistorySelection:
    """相对身份只计本轮开销；分页选择连续近期后缀和预算内的置顶来源。"""
    prefix = case(((Entry.sender_type == 'role') & (Entry.sender_id == role_id), 0),
        (Entry.sender_id.is_(None), func.length(Entry.sender_type) + 3),
        else_=func.length(Entry.sender_type) + func.length(cast(Entry.sender_id, String)) + 4)
    tokens = Entry.text_bytes + prefix + 8
    filters = [Entry.conversation_id == conversation_id, Entry.state == 'included', boundary]
    if summary_id:
        from ..models import ContextCompressionSource
        filters.append(~select(ContextCompressionSource.message_id).where(
            ContextCompressionSource.compression_id == summary_id, ContextCompressionSource.message_id == Entry.message_id).exists())
    count, total = (await session.execute(select(func.count(), func.sum(tokens)).where(*filters))).one()
    picked = {}
    used, pinned_used = 0, 0
    for pinned in [True, False]:
        before = None
        exhausted = False
        while not exhausted:
            query = select(Entry.message_id, Entry.source_revision, Entry.source_status, tokens.label('tokens')).where(
                *filters, Entry.pinned.is_(pinned))
            if before is not None:
                query = query.where(Entry.message_id < before)
            rows = (await session.execute(query.order_by(Entry.message_id.desc()).limit(200))).all()
            if not rows:
                break
            for row in rows:
                before = row.message_id
                if pinned and pinned_used + row.tokens > pinned_budget:
                    continue
                estimate = token_estimate(fixed_tokens + used + row.tokens)
                if estimate.estimated_tokens + estimate.safety_margin_tokens > input_budget:
                    if not pinned:
                        exhausted = True
                        break
                    continue
                picked[row.message_id] = row
                used += row.tokens
                if pinned:
                    pinned_used += row.tokens
    messages, sources = [], []
    # SQLite 参数数量有上限，正文读取也分批。来源版本复核防止缓存或修订混入。
    ids = sorted(picked)
    for start in range(0, len(ids), 200):
        entries = (await session.scalars(select(Entry).join(Message, Message.id == Entry.message_id).where(
            Entry.message_id.in_(ids[start:start + 200]), Entry.source_revision == Message.revision,
            Entry.source_status == Message.status, Message.conversation_id == conversation_id).order_by(Entry.message_id))).all()
        if len(entries) != len(ids[start:start + 200]):
            raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
        for entry in entries:
            if entry.source_revision != picked[entry.message_id].source_revision:
                raise ContextBuildError('CONTEXT_SOURCE_CHANGED')
            messages.append(project_text(entry.text, entry.sender_type, entry.sender_id, role_id))
            sources.append({'message_id': entry.message_id, 'revision': entry.source_revision, 'status': entry.source_status})
    return HistorySelection(tuple(messages), sources, int(total or 0), used, count)
