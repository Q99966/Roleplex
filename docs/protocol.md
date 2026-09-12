# Roleplex 接口协议索引与总则

本文档是 Roleplex 协议文档体系的稳定入口，维护协议范围、通用约定、当前实现状态和领域索引。项目规模增长后，具体 REST、WebSocket、消息、资源和内部协议应拆分到 `docs/protocol/` 对应领域目录，不应继续全部堆叠在本文件中。

消息与 WebSocket（含历史窗口分页）已经拆分到领域文档；本文件对这些领域只保留摘要、状态和链接。尚未实现的邀请、Artifact 和其他列表分页协议暂留在本文件，实现时按同样方式拆出。

> 只有已经存在对应后端处理和前端行为的内容，才能视为当前可用；其余内容属于原型或后续里程碑预留。

## 文档状态与拆分规则

协议领域文档使用以下状态：

- **已实现**：存在服务端处理、客户端行为和相应测试。
- **原型**：存在部分代码或风险验证，但尚未形成完整可用链路。
- **预留**：只有 schema、计划或协议设计，当前不可调用。
- **废弃**：不再建议使用，并提供替代方案或迁移说明。

协议目录按领域组织：

```text
docs/protocol/
├── public/       # 客户端可依赖的 REST、消息、WebSocket 和资源协议
├── internal/     # Agent、MCP、调度、领域事件和审计等内部约定
└── testing/      # 协议及模型厂商契约测试约定
```

领域文档至少标注受众、状态、协议版本、维护者、事实来源和复核日期，并链接关联的路由、schema、客户端类型和测试。公开协议承诺兼容性；内部协议不自动成为客户端可以依赖的 wire contract。

## 当前领域索引

| 领域 | 当前状态 | 权威位置 |
|---|---|---|
| REST 认证、密码策略与强制重置 | 已实现 | [public/rest/auth.md](protocol/public/rest/auth.md) |
| REST 角色管理与墓碑 | 已实现 | [public/rest/roles.md](protocol/public/rest/roles.md) |
| REST 会话管理与回收站 | 已实现（单聊与 M4a 群聊） | [public/rest/conversations.md](protocol/public/rest/conversations.md) |
| REST 世界存档与切换 | 已实现 | [public/rest/worlds.md](protocol/public/rest/worlds.md) |
| REST 当前 World 工作区 | E1 与 E2 统一文件操作已人工验收 | [public/rest/workspaces.md](protocol/public/rest/workspaces.md) |
| 工作区结构化命令 | 内部/已实现（W1b 已验收） | [internal/workspace-commands.md](protocol/internal/workspace-commands.md) |
| 消息发送、历史与停止生成 | 已实现（B 最近窗口与 M 多行输入均已人工验收） | [public/messaging/messages.md](protocol/public/messaging/messages.md) |
| 工具执行顺序与 Owner 详情 | D/E1、E2 探索与批量文件节点已人工验收 | [public/messaging/tool-details.md](protocol/public/messaging/tool-details.md) |
| Shell 逐次审批 | W1c 已实现，已人工验收 | [public/messaging/shell-approvals.md](protocol/public/messaging/shell-approvals.md) |
| 会话运行实例、服务与回收 | Linux W1d 与 G 授权复核补齐均已验收 | [public/messaging/runtime-services.md](protocol/public/messaging/runtime-services.md) |
| WebSocket 连接、订阅与恢复 | 已实现（含登录会话级连接与最近窗口快照） | [public/websocket/conversation-stream.md](protocol/public/websocket/conversation-stream.md) |
| 邀请兑换 | 预留 | 本文档（待实现时拆分） |
| Artifact 原始内容读取与 iframe 隔离 | 原型 | [public/resources/artifact-raw.md](protocol/public/resources/artifact-raw.md) |
| Artifact 创建与版本更新 | 预留 | 本文档（待实现时拆分） |
| 其他列表分页与兼容性 | 预留/总则；消息窗口见消息领域 | 本文档 |
| 数据模型（表与字段） | 内部 | [internal/data-model.md](protocol/internal/data-model.md) |
| Agent 运行时（领域事件、E0 execution、工具安全、MCP） | 内部/部分已实现 | [internal/agent-runtime.md](protocol/internal/agent-runtime.md) |
| 日志、请求关联与异步链路观测 | 内部/已实现 | [internal/observability.md](protocol/internal/observability.md) |

## 认证

REST 使用 `Authorization: Bearer <访问令牌>`，WebSocket 通过首帧传递令牌，不得放入查询参数。令牌有效期七天；Token 版本变化后旧令牌立即失效。

密码策略、弱口令强制重置的拦截规则、改密接口和认证错误码的权威文档见 [public/rest/auth.md](protocol/public/rest/auth.md)。WebSocket 首帧的帧格式见 [WebSocket 会话事件流](protocol/public/websocket/conversation-stream.md)。

## 错误信封

错误码名称、含义、终态和重试语义的权威注册表见 [错误码设计与注册表](protocol/error-codes.md)。
本节只保留公开信封和 HTTP 分类总则，各领域接口返回范围见对应领域文档。

所有错误使用稳定的机器可读错误码：

```json
{"error":{"code":"CONVERSATION_NOT_FOUND","message":"会话不存在","request_id":"..."}}
```

状态码约定：

- `401`：`AUTH_REQUIRED`、`AUTH_INVALID`、`AUTH_REVOKED`
- `403`：`OWNER_REQUIRED`、`PASSWORD_RESET_REQUIRED`；`FORBIDDEN` 为后续通用授权预留码，当前未使用
- `404`：资源不存在或请求者无权访问；不得通过响应泄露资源是否存在
- `409`：重复、幂等冲突或版本冲突
- `422`：请求参数无效；策略类拒绝使用各自的稳定错误码而不是笼统的 `VALIDATION_ERROR`
- `429`：配额或速率限制

错误信封在 `code`、`message`、`request_id` 之外允许附带 `details`，用于逐条说明校验或策略未通过的原因。
服务端还会在所有 HTTP 响应返回同值的 `X-Request-ID`；调用方可以传入该请求头以复用自己的关联 ID，
未传时由服务端生成。

当前状态：REST 认证和基础资源接口已使用错误信封；尚未实现的接口必须继续复用该格式。

## 消息

会话历史读取、用户消息发送（含 `client_message_id` 幂等键）和停止生成已实现，权威文档见 [public/messaging/messages.md](protocol/public/messaging/messages.md)。流式增量和终态不通过 REST 返回，客户端必须订阅 WebSocket 事件流。

M4a 群聊创建、成员管理和 `@` 串行调度已实现；当前 wire contract 以对应领域文档为准。Orchestrator
分派、附件和 Artifact part 仍属后续里程碑。

## WebSocket 订阅恢复

首帧认证、按 `after_event_seq` 的 backlog 回放、epoch 变化或超出回放上限时回落完整快照均已实现，权威文档见 [public/websocket/conversation-stream.md](protocol/public/websocket/conversation-stream.md)。

事件的可靠恢复来源是持久化事件日志：事件先落库再广播，内存广播只服务在线订阅者。客户端必须按 `event_seq` 与 `(message_id, revision, delta_seq)` 幂等应用事件，并对未知事件类型提供降级行为。

## 邀请兑换（预留）

```http
POST /api/invites/{code}/redeem
```

兑换操作必须是原子的：邀请已过期、已撤销或已达到使用次数时返回 `INVITE_INVALID`；成功时在同一事务中递增 `used_count` 并创建用户成员关系。已经加入的用户重复兑换应视为幂等成功。

当前状态：邀请数据模型已预留；邀请创建、兑换和 Guest 入群路由尚未实现。

## Artifact 更新（预留）

创建接口：

```http
POST /api/conversations/{conversation_id}/artifacts
```

创建产物时生成版本 `1`。更新必须使用不可变产物 ID 和乐观版本号：

```json
{
  "artifact_id":7,
  "expected_version":1,
  "content":"<html>...</html>"
}
```

版本不匹配时返回 `409 ARTIFACT_VERSION_CONFLICT`。客户端应重新读取最新版本后决定是否重试。消息 part 引用固定版本：

```json
{"type":"artifact","artifact_id":7,"version":2}
```

因此后续版本创建后，历史消息中的旧版本不会漂移。

当前状态：Artifact 数据模型已预留；创建、更新和前端预览尚未实现。原始内容读取端点与
iframe 隔离已作为风险验证实现，见 [产物原始内容读取](protocol/public/resources/artifact-raw.md)。

## 其他列表分页（预留）

消息最近窗口分页已实现，参数和体积预算只以[消息领域协议](protocol/public/messaging/messages.md)为准。
本节不覆盖消息窗口，仅记录其他列表的预留方向。

列表接口引入分页后使用游标：

```text
?cursor=<opaque>&limit=50
```

游标是不透明值，客户端不得解析其内部结构。响应格式为：

```json
{"items":[],"next_cursor":null}
```

`next_cursor: null` 表示列表已经结束。

当前状态：当前 M0/M1 列表接口尚未统一实现游标分页。

## 兼容性

REST 和 WebSocket payload 应在 API 根路径和握手元数据中携带协议版本。新增事件类型必须允许旧客户端忽略；新增消息 part 类型应由旧客户端渲染为未知类型占位，而不是导致页面白屏。

已有字段不得重命名或改变原有语义；新增字段优先保持向后兼容。协议示例只能使用占位值，不得包含真实密码、API Key、Token、用户隐私或可直接执行的危险命令。
