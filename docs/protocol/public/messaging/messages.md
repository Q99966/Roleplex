# 消息发送与生成控制

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现（单聊；群聊调度未实现） |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/messages.py`、`backend/app/schemas.py`、`backend/app/services/chat.py` |
| 关联测试 | `backend/tests/test_chat_flow.py`、`backend/tests/test_delete_semantics.py`、`frontend/tests/recycle-and-tombstone.spec.ts` |
| 复核日期 | 2026-08-24 |

## 范围

会话内的消息历史读取、用户消息发送、生成排队与停止生成。消息的流式增量和终态不通过 REST 返回，客户端必须通过 [WebSocket 会话事件流](../websocket/conversation-stream.md) 接收。

## 鉴权与资源归属

所有接口要求 `Authorization: Bearer <访问令牌>`。请求者必须是该会话的 `user` 成员；非成员一律返回 `404 CONVERSATION_NOT_FOUND`，不区分“会话不存在”和“无权访问”。

## 读取历史

```http
GET /api/conversations/{conversation_id}/messages
```

响应同时给出事件游标，客户端据此建立 WebSocket 订阅位置：

```json
{
  "items": [],
  "event_seq": 12,
  "stream_epoch": "占位 epoch",
  "active_generation_id": null
}
```

`items` 按消息 `id` 升序返回，`id` 即稳定排序键。`active_generation_id` 非空表示该会话仍有排队中或运行中的生成，客户端应显示生成中状态。当前未实现游标分页。

`event_seq` 与 `items` 之间允许存在极短的读取时差：生成过程中读取历史时，游标可能比返回的消息稍旧，因此按该游标订阅可能重复收到已经体现在消息里的事件。客户端必须按 `event_seq` 幂等去重（见 [WebSocket 协议](../websocket/conversation-stream.md)）；反方向（游标比消息新导致漏事件）不会发生。

## 发送消息

```http
POST /api/conversations/{conversation_id}/messages
```

```json
{
  "client_message_id": "客户端生成的占位 uuid",
  "parts": [{"type": "text", "text": "你好"}],
  "mentions": [],
  "reply_to_id": null
}
```

`parts` 至少一项，part 信封允许未知字段以保持向前兼容；当前必须包含一个非空 `text` part，否则返回 `422 TEXT_PART_REQUIRED`。会话内没有存活且启用的可回复角色时返回 `422 CONVERSATION_HAS_NO_ROLE`；角色删除后成员关系仍为历史保留，但墓碑不能继续触发生成。该拒绝发生在用户消息与生成任务落库之前。

成功返回 `202`，表示消息已持久化、生成已排队：

```json
{"message": {}, "generation_id": 7, "duplicate": false}
```

### 幂等

`client_message_id` 是限定在“当前会话 + 当前发送者”范围内的幂等键（数据库唯一约束）。重复提交同一键时返回已有消息，`duplicate: true` 且 `generation_id: null`，不会创建第二条消息或第二次生成。省略该键时不提供幂等保证。

## 停止生成

```http
POST /api/conversations/{conversation_id}/stop
```

停止当前会话最新的排队/运行中生成，返回 `202`：

```json
{"stopped": true, "generation_id": 7}
```

`stopped: false` 表示没有正在运行的任务（包括已完成或已停止），按幂等成功处理。停止是取消语义而不是错误：已缓冲的内容会以 `stopped` 状态落库，并广播终态事件；上游模型请求和工具调用的实际中断属于尽力而为，不承诺计费立即停止。

## 消息结构与状态

事件与 REST 共用同一份消息结构：`id`、`conversation_id`、`sender_type`、`sender_id`、`parts_json`、`status`、`revision`、`chain_id`、`created_at`。

- `sender_type`：`user | role | orchestrator | system`
- `status`：`pending | generating | done | error | stopped | interrupted`
- `revision`：消息乐观锁版本，每次内容或状态变化递增，供客户端幂等应用事件
- `chain_id`：同一次触发链共用的标识，用于把用户消息、生成和角色回复关联起来

进程重启时遗留的 `pending`/`generating` 消息会被恢复为 `interrupted`。

## 降级与未实现

群聊 `@` 调度、Orchestrator 分派、附件、Artifact part 和重新生成尚未实现；`mentions`、`reply_to_id` 会被持久化但当前不影响回复对象（单聊固定由唯一角色成员回复）。客户端遇到未知 part 类型必须降级为占位展示，不得白屏。
