"""共享材料的事务投影与升级回填。

消息仍由原 reducer 修改。本模块订阅 ORM flush，在同一事务更新来源版本和
稳定正文；失败会一并回滚。流式正文不更新材料，只有创建/终态/来源修订同步。
批量 SQL 修改消息须显式调用同步函数，不能绕开这一业务边界。
"""
from collections import defaultdict

from sqlalchemy import and_, event, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..db import ApplicationSession, SessionLocal, now_utc, with_locked_retry
from ..models import Conversation, ConversationContext, ConversationContextEntry, Message
from .projection import stable_message_text, parts_text, public_execution_facts
from .fingerprint import stable_hash

PROJECTION_VERSION = 3
_CHANGES = 'roleplex_context_changes'


def _values(message: Message) -> dict:
    from ..communication.service import sender_identity
    sender_type, sender_id = sender_identity(message)
    pending = message.status in {'pending', 'generating'}
    unpaired = any(p.get('type') == 'tool_call' and p.get('status') == 'running' for p in message.parts_json or [])
    text = '' if unpaired else stable_message_text(message)
    search_text = parts_text(message.parts_json or []) if not text and message.status in {'error', 'interrupted'} else None
    facts = public_execution_facts(message)
    reason = ('generating' if pending else 'tool_pending' if unpaired else
        'failed' if message.status == 'error' else 'interrupted' if message.status == 'interrupted' else 'empty')
    if (message.meta_json or {}).get('communication', {}).get('kind') == 'legacy_execution_input':
        search_text = None
        reason = 'execution_input'
    return dict(conversation_id=message.conversation_id, source_revision=message.revision,
        source_status=message.status, sender_type=sender_type, sender_id=sender_id,
        chain_id=message.chain_id, pinned=message.pinned, text=text, text_bytes=len(text.encode('utf-8')),
        state='pending' if pending else 'included' if text else 'excluded', reason=None if text else reason,
        created_at=message.created_at, search_text=search_text, execution_facts_json=facts,
        text_hash=stable_hash([text, search_text, facts, sender_type, sender_id, message.status,
            (message.meta_json or {})['communication']]) if (message.meta_json or {}).get('communication') else
            stable_hash([text, search_text, facts, sender_type, sender_id, message.status]),
        projection_version=PROJECTION_VERSION)


def sync_sources(session: Session, messages: list[Message], *, conversations=(), deleted=()) -> None:
    """在来源事务中同步已 flush 的消息，原子增加每个受影响会话的版本。

    Args:
        session：当前 ORM 同步 Session（AsyncSession.run_sync 或 flush 事件内部）。
        messages：当前数据库来源，不接受网络传来的投影正文。
        conversations：本事务新建的空会话 ID。
        deleted：本事务删除的消息所属会话 ID；条目由外键级联删除。
    """
    grouped = defaultdict(list)
    for cid in [*conversations, *deleted]:
        grouped[cid] = []
    for message in messages:
        grouped[message.conversation_id].append(message)
    for cid, sources in sorted(grouped.items()):
        # 与消息/事件写入使用同一会话行，保护旧库首建和并发终态版本。
        exists = session.execute(update(Conversation).where(Conversation.id == cid)
            .values(event_seq=Conversation.event_seq).returning(Conversation.id)).scalar_one_or_none()
        if exists is None:
            continue
        state = session.get(ConversationContext, cid)
        if state is None:
            # 此函数可能在 after_flush 中，直接插入父记录，不递归触发 flush。
            session.execute(ConversationContext.__table__.insert().values(conversation_id=cid,
                revision=0, projection_version=PROJECTION_VERSION, updated_at=now_utc()))
            state = session.get(ConversationContext, cid)
        changed = cid in deleted
        for message in sources:
            entry = session.get(ConversationContextEntry, message.id)
            if entry is not None and entry.projection_version == PROJECTION_VERSION and entry.state == 'pending' and message.status in {'pending', 'generating'}:
                continue
            values = _values(message)
            if entry is None:
                session.add(ConversationContextEntry(message_id=message.id, **values))
                changed = True
            elif any(getattr(entry, key) != value for key, value in values.items() if key != 'created_at'):
                for key, value in values.items():
                    setattr(entry, key, value)
                changed = True
        if changed:
            revision = session.execute(update(ConversationContext).where(ConversationContext.conversation_id == cid)
                .values(revision=ConversationContext.revision + 1, updated_at=now_utc(), projection_version=PROJECTION_VERSION)
                .returning(ConversationContext.revision), execution_options={'synchronize_session': False}).scalar_one()
            set_committed_value(state, 'revision', revision)
            session.expire(state, ['updated_at'])


def _collect(session, _flush_context, _instances):
    messages = [row for row in session.new | session.dirty if isinstance(row, Message)
        and (row in session.new or session.is_modified(row, include_collections=False))]
    conversations = [row for row in session.new if isinstance(row, Conversation)]
    deleted = [row.conversation_id for row in session.deleted if isinstance(row, Message)]
    if messages or conversations or deleted:
        session.info[_CHANGES] = (messages, conversations, deleted)


def _publish(session, _flush_context):
    changes = session.info.pop(_CHANGES, None)
    if changes:
        messages, conversations, deleted = changes
        sync_sources(session, messages, conversations=[row.id for row in conversations], deleted=deleted)


def _rollback(session, _previous_transaction):
    session.info.pop(_CHANGES, None)


def install_projection_hooks():
    """初始化时注册一次；包括普通消息、工作流输入和重启收口等所有 ORM 写入方。"""
    if not event.contains(ApplicationSession, 'before_flush', _collect):
        event.listen(ApplicationSession, 'before_flush', _collect)
        event.listen(ApplicationSession, 'after_flush_postexec', _publish)
        event.listen(ApplicationSession, 'after_soft_rollback', _rollback)


async def backfill_contexts(*, batch_size: int = 200):
    """分批修复旧库缺失/失效投影；每批提交，崩溃后只重做未提交批次。

    启动在接收请求前完成。查询只加载有差异的来源，重启不会复制整份聊天。
    """
    after = 0
    async def batch():
        async with SessionLocal() as session:
            rows = (await session.scalars(select(Message).outerjoin(ConversationContextEntry,
                ConversationContextEntry.message_id == Message.id).where(Message.id > after, or_(
                ConversationContextEntry.message_id.is_(None),
                ConversationContextEntry.projection_version != PROJECTION_VERSION,
                ConversationContextEntry.conversation_id != Message.conversation_id,
                ConversationContextEntry.source_status != Message.status,
                and_(Message.status.not_in(['pending', 'generating']),
                    ConversationContextEntry.source_revision != Message.revision)))
                .order_by(Message.id).limit(batch_size))).all()
            if not rows:
                return None
            await session.run_sync(lambda sync: sync_sources(sync, rows))
            await session.commit()
            return rows[-1].id
    while True:
        last = await with_locked_retry(batch)
        if last is None:
            break
        after = last
