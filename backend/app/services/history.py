"""按完整消息构造有界历史窗口；不参与模型上下文选择。"""
import base64
import binascii
import json

from fastapi import HTTPException
from sqlalchemy import select

from ..models import Conversation, Generation, Message
from ..realtime.events import current_epoch
from ..schemas import HistoryWindowMetadata
from .chat import message_payload

PAGE_BYTES = 64 * 1024
PAGE_MESSAGES = 50


def _cursor(conversation_id: int, before: int) -> str:
    """生成不授予权限的稳定位置标识。

    Args:
        conversation_id：已鉴权会话。
        before：下一页排他的消息 ID 上界。
    """
    raw = json.dumps([1, current_epoch(), conversation_id, before], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _decode(cursor: str, conversation_id: int) -> int:
    """验证位置所属会话和 epoch，错误不回显原始游标。

    Args:
        cursor：客户端不透明游标。
        conversation_id：当前已鉴权资源。
    """
    try:
        if len(cursor) > 512:
            raise ValueError()
        version, epoch, cid, before = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if version != 1 or type(cid) is not int or cid != conversation_id or type(before) is not int or not 0 < before <= 2**63 - 1:
            raise ValueError()
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise HTTPException(422, 'HISTORY_CURSOR_INVALID') from None
    if epoch != current_epoch():
        raise HTTPException(409, 'HISTORY_CURSOR_EXPIRED')
    return before


async def build_history_window(session, conversation_id: int, before: str | None = None,
                               *, snapshot: bool = False, subscription_id: str | None = None) -> dict:
    """从最近消息向前读取；最多读取一页及一个候选正文，不加载整个会话。

    Args:
        session：请求/恢复使用的数据库会话，调用者已完成资源授权。
        conversation_id：窗口所属会话。
        before：更早一页游标；None 表示最近页。
        snapshot：是否计量 WebSocket 完整信封。
        subscription_id：快照信封的控制操作身份。

    Returns:
        REST 响应或 snapshot payload，带实际序列化字节数。
    """
    upper = _decode(before, conversation_id) if before is not None else None
    # 水位必须先于正文读取，允许重复回放，不能让游标比正文更新而丢事件。
    event_seq = await session.scalar(select(Conversation.event_seq).where(Conversation.id == conversation_id)) or 0
    active_ids = list((await session.scalars(select(Generation.id).where(
        Generation.conversation_id == conversation_id, Generation.status.in_(['queued', 'running']),
    ).order_by(Generation.id))).all())
    key = 'messages' if snapshot else 'items'
    page = {key: [], 'event_seq': event_seq, 'stream_epoch': current_epoch(),
        'active_generation_id': active_ids[0] if active_ids else None, 'active_generation_ids': active_ids,
        **HistoryWindowMetadata().model_dump()}
    if snapshot:
        page['conversation_id'] = conversation_id

    def measure() -> int:
        """计入计数字段自身和完整公开信封，求稳定序列化长度。"""
        wire = page
        if snapshot:
            wire = {'type': 'snapshot', 'stream_epoch': current_epoch(), 'payload': page}
            if subscription_id:
                wire.update(conversation_id=conversation_id, subscription_id=subscription_id)
        while True:
            size = len(json.dumps(wire, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
            if size == page['page_bytes']:
                return size
            page['page_bytes'] = size

    items = []
    for _ in range(PAGE_MESSAGES):
        conditions = [Message.conversation_id == conversation_id]
        if upper is not None:
            conditions.append(Message.id < upper)
        # 一次只取一个完整候选，避免多条超大消息同时反序列化；已有复合索引支持 seek。
        row = await session.scalar(select(Message).where(*conditions).order_by(Message.id.desc()).limit(1))
        if row is None:
            break
        candidate = message_payload(row)
        page[key] = [candidate, *items]
        page.update(has_more=True, next_cursor=_cursor(conversation_id, row.id))
        size = measure()
        if size > PAGE_BYTES and items:
            break
        items = page[key]
        upper = row.id
        if size > PAGE_BYTES:
            page['oversized'] = True
            break
    page[key] = items
    older = await session.scalar(select(Message.id).where(Message.conversation_id == conversation_id,
        Message.id < items[0]['id']).limit(1)) if items else None
    page.update(has_more=older is not None, next_cursor=_cursor(conversation_id, items[0]['id']) if older is not None else None)
    measure()
    # 最后一条消息的游标元数据移除后可能不再超预算，以最终响应为准。
    page['oversized'] = page['page_bytes'] > PAGE_BYTES
    measure()
    return page
