from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    """提交后广播给客户端的不可变会话事件。

    事件序号由数据库事务分配，本类只负责在进程内传递已经持久化的事实。
    """

    stream_epoch: str
    event_seq: int
    conversation_id: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    revision: int = 0
    delta_seq: int | None = None
    generation_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """序列化事件信封，不暴露服务端内部状态。"""
        return {
            "stream_epoch": self.stream_epoch,
            "event_seq": self.event_seq,
            "conversation_id": self.conversation_id,
            "type": self.type,
            "revision": self.revision,
            "delta_seq": self.delta_seq,
            "generation_id": self.generation_id,
            "payload": self.payload,
        }


class EventHub:
    """进程内实时广播器。

    事件的可靠恢复来源是数据库事件日志；本类只在事务提交后把事件推给在线订阅者，
    因此不分配序号，也不承诺断线期间的补齐能力。
    """

    def __init__(self) -> None:
        """为当前进程生命周期创建广播器并生成新的 stream epoch。"""
        self.stream_epoch = secrets.token_urlsafe(16)
        self._subscribers: dict[int, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: DomainEvent) -> None:
        """把已持久化的事件推送给该会话的在线订阅者。

        Args:
            event：已经写入事件日志并提交的领域事件。
        """
        async with self._lock:
            subscribers = list(self._subscribers[event.conversation_id])
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢客户端由自身通过事件日志或快照恢复，不能阻塞写入方。
                pass

    async def subscribe(self, conversation_id: int) -> asyncio.Queue[DomainEvent]:
        """注册一个实时事件队列；历史事件由调用方从事件日志读取。"""
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subscribers[conversation_id].add(queue)
        return queue

    async def unsubscribe(self, conversation_id: int, queue: asyncio.Queue[DomainEvent]) -> None:
        """从会话中移除一个订阅队列，不影响其他订阅者。"""
        async with self._lock:
            self._subscribers[conversation_id].discard(queue)


hub: EventHub | None = None


def current_epoch() -> str:
    """返回当前进程的 stream epoch；未初始化时返回启动占位值。"""
    return hub.stream_epoch if hub else "starting"
