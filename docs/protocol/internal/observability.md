# 日志、请求关联与异步链路观测

- 受众：内部开发与运维
- 状态：已实现
- 协议版本：4
- 维护者：Roleplex
- 事实来源：`backend/app/config/logging.py`、`backend/app/main.py`、`backend/app/services/chat.py`、`backend/app/realtime/websocket.py`
- 复核日期：2026-08-24

## 输出与轮转

开发终端使用带本地时区时间戳的可读单行日志；持久化文件使用 UTC ISO 8601 时间戳和 UTF-8 单行
JSON。应用、Uvicorn error 与 Uvicorn access logger 共用同一套 handler 和格式器。

持久化路径为 `logs/{本地年月日}/{运行类型}/{片段起始年月日时分秒}.jsonl`。运行类型固定为：

- `runtime`：正常开发或产品运行，未显式设置时的默认值。
- `unit`：pytest，由 `backend/tests/conftest.py` 强制设置。
- `e2e-fake`：普通 Playwright，后端和 WebSocket 真实运行，只有模型 provider 是 fake。
- `e2e-real`：显式执行的真实 API Playwright，调用真实模型厂商并产生费用。

每次进程启动必须原子占用一个新文件，不得续写已有文件。同秒冲突使用递增数字后缀。每次写入前
同时检查三个边界：本地日期变化、当前片段持续满 3600 秒，或本条记录会使文件超过 10 MiB；任一成立
就先关闭旧文件，再按轮转时的本地日期和时间创建新片段。`LOG_MAX_BYTES` 和 `LOG_MAX_SECONDS`
允许调低边界用于诊断，但分别不得高于 10 MiB 和 3600 秒。

## 关联字段

稳定公共字段为 `timestamp`、`level`、`logger`、`event`、`run_kind`。`run_kind` 与所在目录一致，
即使跨目录汇总 JSONL 也能区分日志来源。下列链路字段在缺失时显式为 `null`：

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
  `generation.provider_ready`、`provider.call_completed`、`generation.started`、`generation.completed`、
  `generation.failed`、`generation.stopped`、`generation.task_unhandled`。
- WebSocket：`ws.accepted`、`ws.authenticated`、`ws.auth_rejected`、`ws.subscribed`、
  `ws.subscription_rejected`、`ws.closed_before_auth`、`ws.disconnected`。

流式过程不逐 token 记录。开始和终态日志使用 `stream_epoch`、`event_seq`、`message_revision`、
`delta_seq`/`delta_count` 形成摘要；订阅日志记录恢复方式、客户端游标、最新事件序号和 backlog 数量。

每次模型 API 调用结束写一条 `provider.call_completed`：`ttft_ms` 是模型调用开始到收到首个流式
分片的时间，`duration_ms` 是该次调用总耗时；`input_tokens`、`output_tokens`、`total_tokens`、
`cache_hit_tokens` 来自厂商 usage。多轮工具调用按 `provider_call_index` 分开记录。
`generation.completed`/`failed`/`stopped` 额外记录整轮 `provider_call_count`、第一次调用的 `ttft_ms`
和可完整汇总的 token 数。厂商或 fake provider 未报告的字段在 JSONL 中明确为 `null`，不得估算。
递归上限后的非流式收尾调用没有独立首分片，此时 `ttft_ms` 等于该次完整响应耗时。

## 敏感数据边界

格式器会按键名再次过滤 `password`、`secret`、凭据类 `token`、`authorization`、`api_key` 等字段，
但显式放行四个 token 用量字段。任何日志调用仍不得传入密码、API Key、Token、Authorization 头、完整用户
输入、完整模型输出或 MCP 敏感参数。认证失败允许记录用户名，因为它是定位登录尝试所需的账号标识。

## 关联代码与测试

- 格式、过滤与配置：`backend/app/config/logging.py`
- HTTP middleware 与响应头：`backend/app/main.py`
- 后台任务与流式摘要：`backend/app/services/chat.py`
- WebSocket 生命周期：`backend/app/realtime/websocket.py`
- 回归测试：`backend/tests/test_logging.py`、`backend/tests/test_chat_flow.py`、
  `backend/tests/test_ws_recovery.py`、`frontend/tests/m2-chat.spec.ts`
