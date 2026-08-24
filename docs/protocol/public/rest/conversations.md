# 会话管理与回收站

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现（单聊；群聊成员管理未实现） |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/conversations.py`、`backend/app/schemas.py`、`backend/app/services/retention.py` |
| 关联测试 | `backend/tests/test_delete_semantics.py`、`frontend/tests/recycle-and-tombstone.spec.ts` |
| 复核日期 | 2026-08-20 |

## 范围

会话的创建、列表、个人偏好，以及删除、回收站与恢复。会话内的消息见
[消息发送与生成控制](../messaging/messages.md)，事件订阅见
[WebSocket 会话事件流](../websocket/conversation-stream.md)。

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
  "role_ids": [1],
  "last_message_at": null,
  "pinned": false,
  "archived": false,
  "deleted_at": null
}
```

`role_ids` **不包含已删除的角色**：墓碑角色不应作为孤儿项出现在成员列表里，它只出现在
历史消息的发送者展示中（见 [角色管理与墓碑](roles.md)）。

`pinned` / `archived` 是请求者的个人偏好，不是会话的共享状态。

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
{"type":"single","title":"占位会话","role_ids":[1],"orchestrator_enabled":false,"orchestrator_role_id":null}
```

单聊必须且只能绑定一个角色。绑定的角色必须属于同一 Owner 且处于启用状态；
已删除的角色是停用的，因此不能被选进新会话。

| 错误码 | 状态码 | 含义 |
|---|---|---|
| `SINGLE_CHAT_REQUIRES_ONE_ROLE` | 422 | 单聊的 `role_ids` 数量不等于 1 |
| `ROLE_REQUIRED` | 422 | `role_ids` 为空 |
| `ROLE_NOT_AVAILABLE` | 422 | 角色不存在、不属于本 Owner，或已停用/已删除 |
| `ORCHESTRATOR_MUST_BE_MEMBER` | 422 | 编排角色不在成员列表中 |

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
