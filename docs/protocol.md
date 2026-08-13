# Roleplex 接口协议索引与总则

本文档是 Roleplex 协议文档体系的稳定入口，维护协议范围、通用约定、当前实现状态和领域索引。项目规模增长后，具体 REST、WebSocket、消息、资源和内部协议应拆分到 `docs/protocol/` 对应领域目录，不应继续全部堆叠在本文件中。

当前 M0 阶段尚未建立领域子文档，因此本文件临时保留协议版本 `1` 的主链路契约。后续拆分时，字段与状态迁移到领域权威文档，本文件只保留摘要、状态和链接。

> 只有已经存在对应后端处理和前端行为的内容，才能视为当前可用；其余内容属于原型或后续里程碑预留。

## 文档状态与拆分规则

协议领域文档使用以下状态：

- **已实现**：存在服务端处理、客户端行为和相应测试。
- **原型**：存在部分代码或风险验证，但尚未形成完整可用链路。
- **预留**：只有 schema、计划或协议设计，当前不可调用。
- **废弃**：不再建议使用，并提供替代方案或迁移说明。

后续协议目录按领域组织，例如：

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
| REST 认证与基础错误 | 已实现 | 本文档（待拆分） |
| 消息发送与幂等 | 预留 | 本文档（待实现时拆分） |
| WebSocket 连接与恢复 | 原型/预留 | 本文档；事件中心原型见后端代码 |
| 邀请兑换 | 预留 | 本文档（待实现时拆分） |
| Artifact 版本更新 | 预留 | 本文档（待实现时拆分） |
| 分页与兼容性 | 预留/总则 | 本文档 |

## 认证

### REST

REST 请求使用以下请求头：

```http
Authorization: Bearer <访问令牌>
```

### WebSocket

WebSocket 必须通过首帧完成认证，不得把令牌放在查询参数中：

```json
{"type":"auth","token":"<访问令牌>"}
```

服务端认证成功时返回 `auth_ok`；认证失败时返回策略违规并关闭连接。令牌有效期为七天；用户的 Token 版本发生变化后，旧令牌立即失效。

当前状态：REST 认证已实现；WebSocket 认证帧属于协议预留，当前尚未提供对应 WebSocket 路由。

## 错误信封

所有错误使用稳定的机器可读错误码：

```json
{"error":{"code":"CONVERSATION_NOT_FOUND","message":"会话不存在","request_id":"..."}}
```

状态码约定：

- `401`：`AUTH_REQUIRED`、`AUTH_INVALID`、`AUTH_REVOKED`
- `403`：`OWNER_REQUIRED`、`FORBIDDEN`
- `404`：资源不存在或请求者无权访问；不得通过响应泄露资源是否存在
- `409`：重复、幂等冲突或版本冲突
- `422`：请求参数无效
- `429`：配额或速率限制

当前状态：REST 认证和基础资源接口已使用错误信封；尚未实现的接口必须继续复用该格式。

## 发送消息（预留）

```http
POST /api/conversations/{conversation_id}/messages
```

请求示例：

```json
{
  "client_message_id":"client-generated-uuid",
  "parts":[{"type":"text","text":"你好"}],
  "mentions":[12],
  "reply_to_id":null
}
```

`client_message_id` 是限定在“当前用户+当前会话”范围内的幂等键。客户端重试时，服务端应返回原有消息，而不是创建重复消息。响应应包含已持久化消息、`revision: 0` 和 `chain_id`。

当前状态：消息模型和请求 schema 已预留；消息发送路由、幂等处理和前端聊天行为尚未实现。

## WebSocket 订阅恢复（预留）

认证后，客户端使用以下消息订阅会话：

```json
{"type":"subscribe","conversation_id":42,"stream_epoch":"上次会话的 epoch","after_event_seq":17}
```

服务端应原子完成订阅，并返回以下两种结果之一：

- `subscribed`，随后发送所有 `event_seq > after_event_seq` 的 backlog 事件；
- `snapshot_required`，当 epoch 发生变化或事件环已经不包含请求序号时，随后发送完整会话快照。

事件示例：

```json
{
  "stream_epoch":"...",
  "event_seq":18,
  "conversation_id":42,
  "type":"message_delta",
  "payload":{"message_id":99,"revision":3,"delta_seq":7,"text":"世界"}
}
```

客户端必须幂等应用事件：

- 当 `event_seq` 不大于本地游标时丢弃事件；
- 当 `(message_id, revision, delta_seq)` 已经应用时丢弃重复事件；
- 未知事件类型不得导致页面崩溃，应降级为可忽略事件或占位提示。

所有状态变化都应进入事件流，包括：

`message_created`、`message_delta`、`message_part_update`、`message_done`、`message_regenerated`、`member_updated`、`conversation_updated`、`conversation_state` 和 `error`。

事件环只保存在内存中。`stream_epoch` 发生变化时，客户端必须请求完整快照；这是后端进程重启后的约定恢复行为。

当前状态：`backend/app/events.py` 已实现进程内事件中心原型；WebSocket 路由、订阅处理和快照接口尚未实现。

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

当前状态：Artifact 数据模型已预留；创建、更新、原始内容读取和前端预览尚未实现。

## 分页（预留）

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
