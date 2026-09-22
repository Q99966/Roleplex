"""可替换的关键词检索适配层：ORM 过滤与排序，中文词组/双字及代码标识符匹配。"""
import re
from sqlalchemy import String, case, cast, func, literal, or_, select, union_all

from ..models import ContextCompression, ContextSummary, Conversation, ConversationContextEntry as Entry, Message
from ..context.summaries import invalid_sources
from .access import readable_conversations, visible_messages


def query_terms(query):
    """首版是可解释关键词匹配；不把词面匹配分数宣称为语义向量相似度。"""
    parts = re.findall(r'[\u3400-\u9fff]+|[\w]+', query.casefold())
    terms = list(dict.fromkeys(parts))[:8]
    for part in parts:
        if re.fullmatch(r'[\u3400-\u9fff]+', part) and len(part) > 2:
            for index in range(len(part) - 1):
                word = part[index:index + 2]
                if word not in terms:
                    terms.append(word)
    return terms[:16]


def candidates(scope, payload, terms):
    """只在权限交集内查询；按匹配词数量、创建时间和稳定身份分页。"""
    source_ids = readable_conversations(scope, related=payload.scope == 'related')
    queries = []
    for kind in dict.fromkeys(payload.kinds):
        if kind == 'message':
            text = func.coalesce(Entry.search_text, Entry.text)
            key, cid, revision, created = cast(Entry.message_id, String), Entry.conversation_id, Entry.source_revision, Entry.created_at
            fields = [Entry.sender_type, Entry.sender_id, Entry.source_status.label('status')]
            query = select().select_from(Entry).join(Message, Message.id == Entry.message_id)
            conditions = [Entry.conversation_id.in_(source_ids), Message.conversation_id == Entry.conversation_id,
                visible_messages(scope, Entry.conversation_id, Entry.message_id, Entry.chain_id),
                Message.revision == Entry.source_revision, Message.status == Entry.source_status,
                Message.status.in_(['done', 'stopped', 'error', 'interrupted'])]
        else:
            text = ContextSummary.text
            key, cid, revision, created = ContextSummary.id, ContextSummary.conversation_id, ContextCompression.source_revision, ContextSummary.created_at
            fields = [literal('summary').label('sender_type'), literal(None).label('sender_id'), literal('done').label('status')]
            query = select().select_from(ContextSummary).join(ContextCompression, ContextCompression.id == ContextSummary.id)
            conditions = [ContextSummary.active_conversation_id == ContextSummary.conversation_id,
                ContextSummary.conversation_id.in_(source_ids),
                ~invalid_sources(ContextSummary.id, ContextSummary.conversation_id).correlate(ContextSummary).exists()]
            if scope.material:
                from ..models import ContextCompressionSource
                conditions.append(~select(ContextCompressionSource.message_id).join(Entry,
                    Entry.message_id == ContextCompressionSource.message_id).where(ContextCompressionSource.compression_id == ContextSummary.id,
                    ~visible_messages(scope, Entry.conversation_id, Entry.message_id, Entry.chain_id)).correlate(ContextSummary).exists())
        matches = [func.lower(text).contains(term, autoescape=True) for term in terms]
        score = sum(case((match, 1), else_=0) for match in matches)
        score += case((func.lower(text).contains(payload.query.strip().casefold(), autoescape=True), 4), else_=0)
        queries.append(query.with_only_columns(literal(kind).label('kind'), key.label('source_id'), cid.label('conversation_id'),
            revision.label('source_revision'), created.label('created_at'), *fields, score.label('score'))
            .where(*conditions, or_(*matches)))
    return union_all(*queries).subquery()
