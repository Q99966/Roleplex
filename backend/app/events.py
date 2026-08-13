from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    """发送给客户端并为短时间断线重连保留的不可变事件。"""
    stream_epoch: str
    event_seq: int
    conversation_id: int
    type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """序列化事件信封，不暴露服务端内部状态。"""
        return {
            "stream_epoch": self.stream_epoch,
            "event_seq": self.event_seq,
            "conversation_id": self.conversation_id,
            "type": self.type,
            "payload": self.payload,
        }


class EventHub:
    """管理会话级事件序号和内存中的断线重连事件环。"""

    def __init__(self, buffer_size: int = 512) -> None:
        """为当前进程生命周期创建事件中心。

        Args:
            buffer_size：每个会话保留的最大事件 backlog，超过后必须使用完整快照。
        """
        self.stream_epoch = secrets.token_urlsafe(16)
        self._buffer_size = buffer_size
        self._seq: dict[int, int] = defaultdict(int)
        self._events: dict[int, deque[DomainEvent]] = defaultdict(lambda: deque(maxlen=self._buffer_size))
        self._subscribers: dict[int, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, conversation_id: int, event_type: str, payload: dict[str, Any]) -> DomainEvent:
        """追加并广播一个带序号的事件，不因慢客户端阻塞写入方。"""
        async with self._lock:
            self._seq[conversation_id] += 1
            event = DomainEvent(self.stream_epoch, self._seq[conversation_id], conversation_id, event_type, payload)
            self._events[conversation_id].append(event)
            subscribers = list(self._subscribers[conversation_id])
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢客户端应通过快照恢复，而不是阻塞事件写入方。
                pass
        return event

    async def subscribe(self, conversation_id: int, after_epoch: str | None, after_seq: int = 0) -> tuple[list[DomainEvent] | None, asyncio.Queue[DomainEvent]]:
        """原子完成订阅并返回 backlog；需要快照时返回 None。

        Args:
            conversation_id：要加入的会话事件流。
            after_epoch：客户端上次记录的进程 epoch，可为空。
            after_seq：客户端已经应用的最后一个事件序号。
        """
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=256)
        async with self._lock:
            events = self._events[conversation_id]
            latest = self._seq[conversation_id]
            needs_snapshot = after_epoch not in (None, self.stream_epoch)
            if not needs_snapshot and after_seq < latest:
                if events and after_seq < events[0].event_seq - 1:
                    needs_snapshot = True
                else:
                    backlog = [event for event in events if event.event_seq > after_seq]
            else:
                backlog = []
            self._subscribers[conversation_id].add(queue)
        return (None if needs_snapshot else backlog), queue

    async def unsubscribe(self, conversation_id: int, queue: asyncio.Queue[DomainEvent]) -> None:
        """从会话中移除一个订阅队列，不影响其他订阅者。"""
        async with self._lock:
            self._subscribers[conversation_id].discard(queue)


hub: EventHub | None = None
