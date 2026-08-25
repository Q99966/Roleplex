"""实时事件、持久化回放与 WebSocket 传输边界。"""

from .events import DomainEvent, EventHub, current_epoch

__all__ = ["DomainEvent", "EventHub", "current_epoch"]
