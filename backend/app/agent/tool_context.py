"""工具执行与领域事件共用的调用身份，不接受模型覆盖。"""
from contextvars import ContextVar

tool_call_id: ContextVar[str | None] = ContextVar('roleplex_tool_call_id', default=None)
