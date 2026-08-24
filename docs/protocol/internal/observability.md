# 日志、请求关联与异步链路观测

- 受众：内部开发与运维
- 状态：已实现
- 协议版本：1
- 维护者：Roleplex
- 事实来源：`backend/app/logging_config.py`、`backend/app/main.py`、`backend/app/services/chat.py`、`backend/app/ws.py`
- 复核日期：2026-08-24

## 输出与轮转

开发终端使用带本地时区时间戳的可读单行日志；`logs/roleplex.jsonl` 使用 UTC ISO 8601 时间戳和
UTF-8 单行 JSON。应用、Uvicorn error 与 Uvicorn access logger 共用同一套 handler 和格式器。
JSONL 默认达到 10 MiB 后轮转，保留 5 份，可通过 `LOG_*` 环境变量调整。

## 关联字段

稳定公共字段为 `timestamp`、`level`、`logger`、`event`。下列链路字段在缺失时显式为 `null`：

- `request_id`：一次 HTTP 请求；响应头 `X-Request-ID` 与错误信封复用该值。
- `user_id`、`conversation_id`、`message_id`、`generation_id`：业务资源关联。
- `chain_id`、`execution_id`、`parent_execution_id`：消息和 Agent 执行链。
- `role_id`、`tool_call_id`：角色与工具执行关联。
- `ws_connection_id`：单条 WebSocket 连接；订阅成功后同时带 `conversation_id`。

HTTP、依赖、路由与 `asyncio.create_task` 通过 `contextvars` 继承上下文。后台生成任务在创建后立即记录
`generation.task_created`，进入协程先记录 `generation.task_started`，即使 provider 构建前失败也不会
留下只有 `202 Accepted` 的黑盒。

## 稳定事件

- HTTP：`http.request_started`、`http.request_completed`、`http.request_failed`。完成或失败事件带
  `status_code`（适用时）和 `duration_ms`。
- 认证：`auth.login_succeeded`、`auth.login_failed`。失败事件记录 `username` 与稳定原因，不记录口令。
- 消息与生成：`message.queued`、`generation.task_created`、`generation.task_started`、
  `generation.provider_ready`、`generation.started`、`generation.completed`、`generation.failed`、
  `generation.stopped`、`generation.task_unhandled`。
- WebSocket：`ws.accepted`、`ws.authenticated`、`ws.auth_rejected`、`ws.subscribed`、
  `ws.subscription_rejected`、`ws.closed_before_auth`、`ws.disconnected`。

流式过程不逐 token 记录。开始和终态日志使用 `stream_epoch`、`event_seq`、`message_revision`、
`delta_seq`/`delta_count` 形成摘要；订阅日志记录恢复方式、客户端游标、最新事件序号和 backlog 数量。

## 敏感数据边界

格式器会按键名再次过滤 `password`、`secret`、`token`、`authorization`、`api_key` 等字段，作为业务
代码约束之外的最后防线。任何日志调用仍不得传入密码、API Key、Token、Authorization 头、完整用户
输入、完整模型输出或 MCP 敏感参数。认证失败允许记录用户名，因为它是定位登录尝试所需的账号标识。

## 关联代码与测试

- 格式、过滤与配置：`backend/app/logging_config.py`
- HTTP middleware 与响应头：`backend/app/main.py`
- 后台任务与流式摘要：`backend/app/services/chat.py`
- WebSocket 生命周期：`backend/app/ws.py`
- 回归测试：`backend/tests/test_logging.py`、`backend/tests/test_chat_flow.py`、
  `backend/tests/test_ws_recovery.py`、`frontend/tests/m2-chat.spec.ts`
