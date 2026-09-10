"""运行期有界双流和终态加密快照；不写机器日志。"""
import base64
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update

from ..agent.tool_capture import bounded_text
from ..config import settings
from ..db import SessionLocal, with_locked_retry
from .models import RuntimeEntry

RING_BYTES = 1024 * 1024
PAGE_BYTES = 64 * 1024
WORLD_BYTES = 16 * 1024 * 1024


class LogRing:
    """按宿主观察顺序登记分片；截断和序号缺口必须显式呈现。"""
    def __init__(self):
        """创建单实例日志窗口。"""
        self.blocks = deque()
        self.sequence = 0
        self.bytes = 0

    def append(self, stream: str, text: str) -> None:
        """Args:
            stream：stdout 或 stderr。
            text：已经通过增量 UTF-8 解码的正文。
        """
        text = bounded_text(text)['text']
        if not text:
            return
        self.sequence += 1
        size = len(text.encode())
        self.blocks.append({'seq': self.sequence, 'stream': stream, 'text': text, 'bytes': size})
        self.bytes += size
        while self.bytes > RING_BYTES and self.blocks:
            self.bytes -= self.blocks.popleft()['bytes']

    def page(self, after: int) -> dict:
        """Args:
            after：客户端已读取的序号，未知/过旧时返回 gap。
        """
        first = self.blocks[0]['seq'] if self.blocks else self.sequence + 1
        items, total = [], 0
        for block in self.blocks:
            if block['seq'] <= after:
                continue
            if total + block['bytes'] > PAGE_BYTES:
                break
            items.append(block.copy())
            total += block['bytes']
        return {'items': items, 'next_seq': items[-1]['seq'] if items else min(after, self.sequence),
            'gap': after < first - 1 or after > self.sequence, 'latest_seq': self.sequence, 'availability': 'available'}


def _cipher() -> Fernet:
    """用 World 密钥派生运行日志专属加密器。"""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'roleplex-runtime-logs-v1\0' + settings.resolved_api_key_secret().encode()).digest()))


async def save(runtime_id: str, ring: LogRing) -> None:
    """终态只保存尾部快照，随后按真实密文字节做 World 总预算淘汰。

    Args:
        runtime_id：日志所属运行实例。
        ring：本宿主有界缓冲。
    """
    encrypted = _cipher().encrypt(json.dumps({'id': runtime_id, 'seq': ring.sequence, 'blocks': list(ring.blocks)}, ensure_ascii=False).encode()).decode()
    async def write():
        """加密后进入独立短事务，不逐输出块提交。"""
        async with SessionLocal() as session:
            from .registry import gate
            await gate(session)
            await session.execute(update(RuntimeEntry).where(RuntimeEntry.id == runtime_id).values(
                log_encrypted=encrypted, log_expires_at=datetime.now(timezone.utc) + timedelta(days=7)))
            await _prune(session)
            await session.commit()
    await with_locked_retry(write)


async def _prune(session):
    """在调用者持有的预算事务内移除过期和超额密文。

    Args:
        session：已获取运行时门槛的短事务。
    """
    rows = (await session.scalars(select(RuntimeEntry).where(RuntimeEntry.log_encrypted.is_not(None))
        .order_by(RuntimeEntry.sequence.desc()))).all()
    total = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        expired = row.log_expires_at is None or _utc(row.log_expires_at) <= now
        size = len(row.log_encrypted.encode())
        if expired or total + size > WORLD_BYTES:
            row.log_encrypted = None
        else:
            total += size


async def maintain():
    """启动时清理过期尾部，不能依赖未来还有服务结束才实际删除密文。"""
    async def write():
        """与终态保存共用同一预算门槛，锁冲突可有界重试。"""
        from .registry import gate
        async with SessionLocal() as session:
            await gate(session)
            await _prune(session)
            await session.commit()
    await with_locked_retry(write)


def _utc(value: datetime) -> datetime:
    """Args:
        value：跨数据库读取的 UTC 时间。
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def archived_page(row: RuntimeEntry, after: int) -> dict:
    """授权后恢复加密尾部；缺失、过期和损坏不伪造空日志。

    Args:
        row：已验证归属的实例。
        after：客户端游标。
    """
    unavailable = ('not_recorded' if not row.log_expires_at else
        'expired' if _utc(row.log_expires_at) <= datetime.now(timezone.utc) else
        'evicted' if not row.log_encrypted else None)
    if unavailable:
        return {'availability': unavailable, 'items': [], 'next_seq': after, 'gap': True}
    try:
        body = json.loads(_cipher().decrypt(row.log_encrypted.encode()))
        if body['id'] != row.id:
            raise ValueError()
        ring = LogRing()
        ring.blocks = deque(body['blocks'])
        ring.sequence = body['seq']
        return ring.page(after)
    except (InvalidToken, ValueError, KeyError, TypeError):
        return {'availability': 'unavailable', 'items': [], 'next_seq': after, 'gap': True}
