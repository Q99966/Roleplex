# 会话管理与回收站

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现（单聊与 M4a 群聊；工作区绑定支持单聊与群聊；群协调者任命已实现） |
| 协议版本 | 4（兼容新增群协调任命版本） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/conversations.py`、`backend/app/schemas.py`、`backend/app/services/retention.py` |
| 关联测试 | `backend/tests/test_delete_semantics.py`、`backend/tests/test_group_chat.py`、`frontend/tests/recycle-and-tombstone.spec.ts`、`frontend/tests/m4-group-chat.spec.ts` |
| 复核日期 | 2026-09-16 |

## 范围

会话的创建、列表、个人偏好，以及删除、回收站与恢复。会话内的消息见
[消息发送与生成控制](../messaging/messages.md)，事件订阅见
[WebSocket 会话事件流](../websocket/conversation-stream.md)。

全部单聊/群聊的持久共享材料及按角色输入占用见[会话上下文](conversation-context.md)。材料随业务消息事务更新；会话回收站保留来源，恢复后重新鉴权读取，不复制成另一个会话。

## 鉴权与资源归属

全部接口要求 `Authorization: Bearer <访问令牌>`。读取与偏好接口要求请求者是该会话的
`user` 成员；创建、删除与恢复要求 Owner。不存在、非成员和非本人创建一律返回
`404 CONVERSATION_NOT_FOUND`，不区分这三种情况。

## 会话表示

```json
{
  "id": 1,
  "type": "single",
  "title": "占位会话",
  "orchestrator_enabled": false,
  "orchestrator_role_id": null,
  "orchestrator_revision": 0,
  "workspace_binding_id": null,
  "role_ids": [1],
  "revision": 0,
  "last_message_at": null,
  "pinned": false,
  "archived": false,
  "deleted_at": null
}
```

`role_ids` **不包含已删除的角色**：墓碑角色不应作为孤儿项出现在成员列表里，它只出现在
历史消息的发送者展示中（见 [角色管理与墓碑](roles.md)）。

`pinned` / `archived` 是请求者的个人偏好，不是会话的共享状态。
`revision` 是共享会话配置的乐观锁版本；成员变化会递增，个人偏好不会递增。
`workspace_binding_id` 是当前 World 的可空 Workspace Binding；详细绑定规则见
[当前 World 工作区](workspaces.md)。

## 列表

```http
GET /api/conversations
```

按个人置顶、最后活跃时间倒序返回当前用户可见的会话。**回收站中的会话不出现在这里。**

## 创建

```http
POST /api/conversations
```

```json
{"type":"single","title":"占位会话","role_ids":[1],"orchestrator_enabled":false,"orchestrator_role_id":null,"workspace_binding_id":null}
```

单聊必须且只能绑定一个角色；群聊至少绑定两个角色。绑定的角色必须属于同一 Owner 且处于启用状态；
已删除的角色是停用的，因此不能被选进新会话。

| 错误码 | 状态码 | 含义 |
|---|---|---|
| `SINGLE_CHAT_REQUIRES_ONE_ROLE` | 422 | 单聊的 `role_ids` 数量不等于 1 |
| `GROUP_CHAT_REQUIRES_MULTIPLE_ROLES` | 422 | 群聊的 `role_ids` 少于 2 个 |
| `ROLE_REQUIRED` | 422 | `role_ids` 为空 |
| `ROLE_NOT_AVAILABLE` | 422 | 角色不存在、不属于本 Owner，或已停用/已删除 |
| `ORCHESTRATOR_MUST_BE_MEMBER` | 422 | 编排角色不在成员列表中 |

创建时的 `orchestrator_enabled` 与 `orchestrator_role_id` 保留旧字段兼容，`orchestrator_revision=0` 不激活协调权。当前已提供下文的显式任命入口；普通 mentions 调度保持不变，任命本身不直接授予模型图管理工具。

## 群聊角色成员管理

```http
PUT /api/conversations/{conversation_id}/members
```

```json
{"role_ids":[2,1],"expected_revision":0}
```

仅 Owner 可以修改自己创建的群聊，且结果必须仍有至少两个存活、启用、属于该 Owner 的角色。请求顺序成为
新的稳定成员顺序，`all` 会按此顺序展开。成功后会话 `revision + 1`，返回完整会话，并产生
`member_updated` 事件；历史消息和已移出角色的身份不会被删除。

`expected_revision` 必须等于当前会话 revision；不一致返回 `409 CONVERSATION_REVISION_CONFLICT`，禁止
静默覆盖另一个窗口刚完成的成员调整。单聊调用该端点返回 `422 GROUP_CHAT_REQUIRED`。

## 个人偏好

```http
PATCH /api/conversations/{conversation_id}/preferences?pinned=true&archived=false
```

只修改请求者自己的置顶与归档状态，不影响其他成员。

## 删除（进回收站）

```http
DELETE /api/conversations/{conversation_id}
```

**软删除**：只写入删除时间，会话立即从列表消失，但消息、成员、事件都完整保留。
返回 `204`。重复删除是幂等的，同样返回 `204`。

删除后该会话对消息链路和 WebSocket 等同于不存在：

- `GET/POST /api/conversations/{id}/messages` → `404 CONVERSATION_NOT_FOUND`
- WebSocket `subscribe` → `CONVERSATION_NOT_FOUND`

否则被删除的会话仍会产生新消息和新事件。

## 回收站列表

```http
GET /api/conversations/deleted
```

返回当前用户在回收站中的会话，按删除时间从新到旧排序。客户端用 `deleted_at`
计算剩余保留天数。

## 恢复

```http
POST /api/conversations/{conversation_id}/restore
```

清除删除时间，会话回到普通列表，消息历史原样可读——保留期内数据从未被物理删除，
因此恢复不需要重建任何内容。返回恢复后的会话表示。

已超过保留期并被清理的会话返回 `404 CONVERSATION_NOT_FOUND`。

## 保留期与清理

已删除的会话保留 **7 天**。超期后在**服务下次启动时**被物理删除，级联清除消息、成员、
事件日志、生成记录、队列、产物与工具审计，并删除关联的附件文件。

没有常驻定时清理任务：Roleplex 是随开随关的单机工具，启动清理已覆盖绝大多数场景；
定时器走单调时钟，Windows 休眠期间不推进会持续漂移。过期数据留在盘上不影响任何功能。

这意味着**长期不重启的实例中，超期会话可能仍存在于数据库里**，但它不会出现在回收站
列表中，也不能被恢复——`deleted_at` 早于保留期的会话对客户端一律不可见。

## 兼容性

`deleted_at` 是会话表示的新增字段，默认 `null`，属于兼容新增。旧客户端忽略它仍可工作，
但会失去回收站能力。`GET /api/conversations` 的语义有变化：此前返回全部会话，
现在排除回收站中的会话。

## 群协调者任命

`PUT /api/conversations/{id}/orchestrator`，仅当前 World Owner 且为本人创建的存活群成员可调用。请求 `{role_id,expected_revision}`；role_id 为有效现有群角色，null 表示取消。expected_revision 对应会话 revision；成功同时递增 revision 和 orchestrator_revision，广播 conversation_updated，返回完整会话。旧版本返回 409 CONVERSATION_REVISION_CONFLICT；非群返回 422 GROUP_CHAT_REQUIRED；角色不可用返回 422 WORKFLOW_ROLE_UNAVAILABLE。

旧创建字段保留兼容，但 orchestrator_revision=0 不激活协调权，须通过此入口显式任命。更换/取消、移除协调角色或停用/删除它封闭旧协调运行，旧任务不继承新任命。普通发送/@ 顺序、手动工作流和单聊不依赖任命。协调执行及任务权限见[工作流](workflows.md)。

## 受限岗位会话

世界管理者初始化服务在 Owner 就绪后创建 `purpose=world_coord` 的 single 会话，普通创建请求不能指定该用途。它不出现在普通列表/回收站，成员、改绑工作区、删除等普通管理操作返回 CONVERSATION_MANAGED；消息/WS/上下文只对岗位 Owner 及当前任职角色生效。管理者配置更新复用原会话并保留实际消息作者，具体生命周期见[世界协调协议](world-orchestrator.md)。
