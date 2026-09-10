# 消息发送与生成控制

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现（单聊与 M4a mentions 串行群聊；Orchestrator 未实现） |
| 协议版本 | 3（兼容新增群聊调度与队列字段） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/routers/messages.py`、`backend/app/schemas.py`、`backend/app/services/chat.py` |
| 关联测试 | `backend/tests/test_chat_flow.py`、`backend/tests/test_group_chat.py`、`backend/tests/test_context_builder.py`、`frontend/tests/m4-group-chat.spec.ts` |
| 复核日期 | 2026-08-30 |

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
  "active_generation_id": null,
  "active_generation_ids": []
}
```

`items` 按消息 `id` 升序返回，`id` 即稳定排序键。`active_generation_ids` 按 generation ID 返回全部排队中
或运行中的生成；`active_generation_id` 保留为其第一项供旧客户端兼容，无任务时两者分别为 `null` 和 `[]`。
当前未实现游标分页。

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

`parts` 至少一项，part 信封允许未知字段以保持向前兼容；当前必须包含一个非空 `text` part，否则返回
`422 TEXT_PART_REQUIRED`。

触发规则：

- 单聊忽略 mentions，继续触发唯一存活角色；没有可回复角色时在消息落库前返回
  `422 CONVERSATION_HAS_NO_ROLE`。
- 群聊只响应真人请求中的 mentions；`mentions=[]` 时只持久化真人消息，不创建 generation、不调用 Provider。
- 多个角色 ID 按请求顺序去重并串行回复；后一个角色在前一个 `message_done` 提交后才构建上下文。
- 只要包含 `"all"`，就忽略同时给出的显式 ID，并按当前持久化成员顺序展开全部角色。
- mentions 指向非成员、停用、墓碑或非当前 Owner 的角色时，在消息落库前返回 `422 ROLE_NOT_AVAILABLE`。
- 一条 chain 最多调度 20 个角色，超过返回 `422 CHAIN_LIMIT_EXCEEDED`。
- 角色消息即使包含 mentions 也不会通过本接口触发新 chain，结构性阻止 Agent 环。

成功返回 `202`，表示消息已持久化、生成已排队：

```json
{"message": {}, "generation_id": 7, "generation_ids": [7,8], "duplicate": false}
```

`generation_ids` 按实际回复顺序排列。`generation_id` 保留为第一项供旧客户端兼容；无 mentions 的群聊消息
分别返回 `null` 和 `[]`。

### 幂等

`client_message_id` 是限定在“当前会话 + 当前发送者”范围内的幂等键（数据库唯一约束）。重复提交同一键时返回已有消息，`duplicate: true` 且 `generation_id: null`，不会创建第二条消息或第二次生成。省略该键时不提供幂等保证。

## 停止生成

```http
POST /api/conversations/{conversation_id}/stop
```

停止当前会话最新 chain 的当前执行并取消该 chain 全部后续排队任务，返回 `202`：

```json
{"stopped": true, "generation_id": 7, "generation_ids": [7,8]}
```

`generation_ids` 列出本次停止覆盖的运行中和排队 generation。当前角色已缓冲内容以 `stopped` 落库；尚未
创建角色占位消息的后续任务直接转为 `stopped`，不会产生空角色消息。其他 chain 和其他会话不受影响。
上游模型请求和工具调用的实际中断属于尽力而为，不承诺计费立即停止。

## 模型历史与上下文预算

生成统一通过内部 ContextBuilder 读取当前消息之前的终态历史：目标角色自己的 `done` 回复作为 assistant，
其他真人/角色消息带稳定身份作为 user；非空 `stopped` 回复带停止标记，`error/interrupted` 不进入模型历史。
当前消息单独作为本轮输入，不会在 history 中重复出现。

M4a 群聊后续角色还会读取当前真人消息之后、同一 chain 已提交的前序角色终态；例如 @A @B 中，B 只有在
A 的 `message_done` 提交后才构造上下文，因此能看见 A，仍看不见其他 chain 或 generating 半成品。

ContextBuilder 按角色 `context_window_tokens` 和输出预留裁剪最旧历史。如果历史全部移除后，必要角色规则、
当前可见工具、当前消息和输出预留仍无法放入有效窗口，已经接受的生成以 `message_done` 失败终态返回：

```json
{"error_code":"CONTEXT_BUDGET_EXCEEDED"}
```

该路径不调用 Provider、不截断当前消息。Owner 可以调整消息、角色提示词、工具、上下文窗口或最大输出；
Guest 只能获得不暴露 Owner 私有配置的通用提示。

## 消息结构与状态

事件与 REST 共用同一份消息结构：`id`、`conversation_id`、`sender_type`、`sender_id`、`reply_to_id`、
`mentions`、`parts_json`、`status`、`revision`、`chain_id`、`created_at`。

- `sender_type`：`user | role | orchestrator | system`
- `status`：`pending | generating | done | error | stopped | interrupted`
- `revision`：消息乐观锁版本，每次内容或状态变化递增，供客户端幂等应用事件
- `chain_id`：同一次触发链共用的标识，用于把用户消息、生成和角色回复关联起来

当前公开 part：

新角色消息的有序文本段、工具位置、timeline_version 和 Owner 详情见 [工具执行详情](tool-details.md)。
该功能不向普通消息 payload 添加原始工具输入输出。

- `text`：`{"type":"text","text":"占位文本"}`。
- `tool_call`：`{"type":"tool_call","call_id":"占位调用","tool_name":"占位工具","status":"running","duration_ms":12}`。
  `status` 为 `running | success | failed | rejected | cancelled | interrupted`；`duration_ms` 只在观察到结束后出现。为避免泄密，公开 part
  不含原始参数或工具输出。W1b 兼容新增可选 `command`、`command_status`、`exit_code`、`truncated`、
  `error_code`，含义见 [结构化命令](../../internal/workspace-commands.md)；未知字段忽略。

进程重启时遗留的 `pending`/`generating` 消息会被恢复为 `interrupted`。

## 降级与未实现

Orchestrator 分派、附件、Artifact part 和重新生成尚未实现。`reply_to_id` 会持久化但当前不影响回复对象；
客户端遇到未知 part 类型必须降级为占位展示，不得白屏。
