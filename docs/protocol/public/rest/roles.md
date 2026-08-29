# 角色管理与墓碑

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现 |
| 协议版本 | 2（兼容新增上下文窗口字段） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/roles.py`、`backend/app/schemas.py`、`backend/app/models.py` |
| 关联测试 | `backend/tests/test_delete_semantics.py`、`frontend/tests/recycle-and-tombstone.spec.ts` |
| 复核日期 | 2026-08-29 |

## 范围

Agent 角色的增删改查，以及删除后的墓碑语义。角色在会话中的成员表示见
[会话管理与回收站](conversations.md)。

## 鉴权与资源归属

全部接口要求 `Authorization: Bearer <访问令牌>`。读取要求角色属于请求者；
创建、修改与删除要求 Owner。不存在、非本人拥有和已删除一律返回
`404 ROLE_NOT_FOUND`，不区分这三种情况。

## 角色表示

```json
{
  "id": 1,
  "name": "占位角色",
  "avatar": null,
  "description": null,
  "tags": [],
  "system_prompt": "占位提示词",
  "model_config_id": 1,
  "model_name": "占位模型名",
  "context_window_tokens": 200000,
  "context_window_ceiling_tokens": 2000000,
  "effective_context_window_tokens": 200000,
  "params": {},
  "skills": [],
  "builtin_tools": [],
  "mcp_servers": [],
  "active": true,
  "deleted_at": null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

写入请求体（创建与修改）只接受配置字段，不接受 `active`、`deleted_at` 等由服务端管理的状态。
`context_window_tokens` 是 Owner 配置的模型输入+输出总窗口，默认 200K，允许 4K～2M；
`context_window_ceiling_tokens` 是服务端部署安全上限，`effective_context_window_tokens` 是两者较小值，
均只读。`params.max_tokens` 必须小于配置窗口，否则请求校验失败。设置过大可能仍被实际 Provider 拒绝，
设置过小只会让历史更早裁剪。

## 列表与读取

```http
GET /api/roles
GET /api/roles/{role_id}
```

**列表包含墓碑**：客户端渲染历史消息时需要按 `sender_id` 查出原名称与头像，
否则已删除角色的历史会退化成匿名 Agent。客户端应按 `deleted_at` 区分：

- `deleted_at` 为 `null`：存活角色，可出现在角色列表、成员选择器和统计中；
- `deleted_at` 非空：墓碑，**只能用于历史消息的发送者展示**，不得出现在任何选择入口。

## 创建与修改

```http
POST /api/roles
PUT /api/roles/{role_id}
```

`model_config_id` 必须指向同一 Owner 的模型配置，否则返回 `422 MODEL_CONFIG_NOT_FOUND`。

同一 Owner 下**未删除**角色的名称唯一。墓碑不占用名称，因此删除一个角色后可以
立刻创建同名角色。

## 删除（墓碑）

```http
DELETE /api/roles/{role_id}
```

**不做物理删除。** 消息只按 `sender_id` 记录发送者，角色行一旦消失，历史里就再也
查不出"谁说的"。删除会：

| 保留 | 清除 |
|---|---|
| `id`、`name`、`avatar` | `system_prompt`、`description`、`tags` |
| `deleted_at`（删除时间） | `model_config_id`（置为 `null`）、`model_name` |
| | `params`、`skills`、`builtin_tools`、`mcp_servers` 及 MCP 工具缓存 |

同时置 `active=false`，因此墓碑不能再被选进会话或触发生成。返回 `204`。

墓碑不可再次删除，也不可修改：两者都返回 `404 ROLE_NOT_FOUND`。

对已有会话的影响：墓碑从会话成员的 `role_ids` 中消失，但历史消息不变，
客户端应在发送者旁标注"已删除"。

## 错误

| 错误码 | 状态码 | 含义 |
|---|---|---|
| `ROLE_NOT_FOUND` | 404 | 角色不存在、不属于请求者，或已被删除（针对写操作） |
| `MODEL_CONFIG_NOT_FOUND` | 422 | 模型配置不存在或不属于同一 Owner |
| `OWNER_REQUIRED` | 403 | 写操作要求 Owner |

## 兼容性

`deleted_at` 是角色表示的新增字段，默认 `null`，属于兼容新增；`model_config_id`
从必填变为**可空**（仅墓碑为空），旧客户端若假设其非空需要调整。

`DELETE /api/roles/{id}` 的语义有变化：此前是物理删除，现在是墓碑。因此删除后角色
仍会出现在 `GET /api/roles` 中，客户端必须按 `deleted_at` 过滤，否则墓碑会出现在
角色列表里。
