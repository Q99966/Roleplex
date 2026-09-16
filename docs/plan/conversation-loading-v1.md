# 会话连接与历史加载阶段记录

状态：A 连接解耦、B 按需历史/缓存/阅读位置已完成；单条超长消息分块尚未实施。整理日期：2026-09-16。

[历史原文](../archive/plan/conversation-loading-v1.md)保留实现前的问题、分阶段方案和当时的验证结果。

当前登录会话持有 WebSocket；会话切换复用连接，以真实订阅/同步响应驱动界面。历史使用最近窗口与游标补齐，并保留有界内存缓存和阅读位置。契约见[WS 会话流](../protocol/public/websocket/conversation-stream.md)与[消息历史](../protocol/public/messaging/messages.md)。

相关回归为 `connection-session.spec.ts`、`history-window.spec.ts`、`test_ws_session.py` 与 `test_history_window.py`，运行入口见[测试指南](../testing/README.md)。

旧 C 单条消息分块仍是待选设计；A→B→C 的人工确认顺序不约束后续维护，不把消息分页误写成单条内容分块已实现。
