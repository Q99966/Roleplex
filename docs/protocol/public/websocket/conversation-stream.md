# WebSocket 会话事件流

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 已实现（含 A 连接解耦；A 已完成人工验收） |
| 协议版本 | 2（兼容新增订阅控制能力协商、操作标识与同步确认） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/realtime/websocket.py`、`backend/app/realtime/events.py`、`backend/app/realtime/store.py` |
| 关联测试 | `backend/tests/test_ws_recovery.py`、`backend/tests/test_ws_session.py`、`frontend/tests/connection-session.spec.ts`、群聊与 managed-world E2E |
| 复核日期 | 2026-09-10 |

## 范围

```text
ws(s)://<host>/api/ws
```

单条连接同时承担认证、会话订阅和实时事件推送。消息的创建、流式增量和终态只通过本通道下发；REST 只提供历史与游标（见 [消息协议](../messaging/messages.md)）。

## 首帧认证

连接建立后客户端必须先发送认证帧，令牌不得出现在查询参数、URL 或日志中：

```json
{"type": "auth", "token": "占位访问令牌"}
```

- 成功：`{"type":"auth_ok","stream_epoch":"占位 epoch"}`
- 失败、首帧不是认证帧或超过服务端认证等待时间（当前 10 秒）：连接以关闭码 `1008` 关闭，不返回错误信封

`stream_epoch` 是后端进程的事件纪元，每次进程启动重新生成，用于识别“服务端重启过”。

## 订阅与恢复

```json
{"type": "subscribe", "conversation_id": 42, "stream_epoch": "上次收到的 epoch", "after_event_seq": 17}
```

请求者必须是该会话的 `user` 成员，否则返回错误信封并保持连接：

```json
{"type": "error", "payload": {"code": "CONVERSATION_NOT_FOUND"}}
```

服务端先注册实时订阅再读取历史，保证注册与回放之间不丢事件。随后返回两种结果之一：

1. **增量恢复**——`stream_epoch` 与服务端一致，且待补齐事件数不超过服务端回放上限（当前 300）：

   ```json
   {"type":"subscribed","stream_epoch":"占位 epoch","conversation_id":42,"event_seq":24}
   ```

   `event_seq` 是该会话当前最大序号；紧随其后按序发送所有 `event_seq > after_event_seq` 的事件，之后无缝进入实时流。

2. **快照恢复**——`stream_epoch` 变化或待补齐事件超过回放上限：

   ```json
   {"type":"snapshot","stream_epoch":"占位 epoch","payload":{"conversation_id":42,"event_seq":24,"messages":[],"active_generation_id":null,"active_generation_ids":[]}}
   ```

   客户端必须用快照整体替换本地会话状态，并把游标重置为快照的 `event_seq`；快照包含生成中消息的当前累积内容。

一条连接同一时刻只订阅一个会话，再次发送 `subscribe` 表示切换会话：旧订阅立即取消，之后不再收到旧会话事件。

其他控制帧：`{"type":"ping"}` → `{"type":"pong"}`；`{"type":"close"}` 由服务端关闭连接。

## 事件信封

```json
{
  "stream_epoch": "占位 epoch",
  "event_seq": 18,
  "conversation_id": 42,
  "type": "message_delta",
  "revision": 3,
  "delta_seq": 7,
  "generation_id": 7,
  "payload": {"message_id": 99, "text": "世界"}
}
```

- `event_seq`：会话内单调递增，由数据库事务分配；同一会话不重号、不跳号
- `revision`：事件对应的消息版本
- `delta_seq`：流式增量序号，从 1 开始连续递增；非增量事件为 `null`
- `generation_id`：关联的生成；与生成无关的事件为 `null`

事件先写入持久化事件日志再广播，因此恢复来源始终是事件日志而不是内存；在线推送队列满时会丢弃推送，客户端按游标重连即可补齐。

有序消息的 `message_delta` 新增 `part_id` 与 `part_index`，用于定位文本段；不得合并所有文本或删除工具 part。
具体兼容与 Owner 详情边界见 [工具执行详情](../messaging/tool-details.md)。

## 当前事件类型

### 登录会话级连接与订阅控制（A，已实现）

连接由登录会话管理，会话组件不拥有 socket；同一 socket 同时仅有一个会话订阅。首帧认证仍保持不变，
`auth_ok` 兼容新增 `capabilities: ["subscription_control_v1"]`。不支持该能力的旧后端不能被新客户端误判为
同步就绪；新客户端报告 WS_PROTOCOL_UNSUPPORTED。旧客户端不提交订阅标识时，服务端仍返回旧帧格式。

新客户端每次订阅提交 ASCII 字母/数字/下划线/连字符组成的 `subscription_id`（1..64 字符），与
conversation_id、after_event_seq、stream_epoch 一同发送。每次切换或重新同步使用新标识；它是控制操作身份，
不是业务 Trace。该订阅的 subscribed、snapshot、业务事件和订阅错误都回显 subscription_id 与 conversation_id。
客户端同时核对物理连接代次、当前操作标识和会话 ID，迟到帧不能改变新订阅。

服务端先登记实时队列，再发送恢复数据，最后发送
`{type:"sync_complete",subscription_id,conversation_id,stream_epoch,through_event_seq}`。
through_event_seq 是已经发送的恢复水位；客户端应用完相应事件/快照后才就绪。控制帧不占用业务 event_seq。
subscribed 仅表示受理，不表示 backlog 已完成。A 继续返回完整历史和快照，不提前实现分页。

`{type:"unsubscribe",subscription_id}` 只释放匹配的订阅，返回
`{type:"unsubscribed",subscription_id,removed:true|false}`；迟到的旧标识不能取消新订阅。连接保持认证待命。
没有标识的旧式 unsubscribe 清理当前订阅；新客户端始终提交标识。切换前取消未完成历史请求，不复用已失效的
pending 请求；成功/失败由实际响应决定，历史请求 30 秒超时只作为故障兜底并允许手动重试。

持久连接在控制请求、恢复发送和实时发送前复核 Token 与会话权限；新客户端每 15 秒 ping，认证失效以
AUTH_INVALID 错误和 1008 关闭，客户端清除登录上下文且不循环重试。成员授权失败为当前订阅的
CONVERSATION_NOT_FOUND，不泄漏资源存在性。未知/非法控制参数返回 VALIDATION_ERROR，不回显原始输入。
物理握手/认证的客户端兜底为 15 秒，同步及 pong 等待为 30 秒；超时不表示同步成功。真实掉线才进行退避重连，
重连只恢复最新选择。数据序号出现缺口时重新订阅补齐，不能跳过缺失事件直接前移游标。

连接状态（待命/连接/认证/重连/失败）与历史加载、订阅同步状态独立；正常切换不显示“连接已断开”。
退出登录、World 或 Token 上下文变化、应用会话结束时清除连接与未完成请求。A 不缓存历史或 Owner 私有详情。

### 业务事件

| 类型 | 时机 | payload |
|---|---|---|
| `message_created` | 用户消息落库、角色占位消息创建 | `{"message": {}}` |
| `message_delta` | 流式文本增量 | `{"message_id": 99, "text": "增量文本"}` |
| `message_done` | 生成完成、停止或失败 | `{"message": {}, "error_code": null}` |
| `message_part_update` | 工具过程 part 开始或结束 | `{"message": {}}` |
| `member_updated` | Owner 修改群聊角色成员 | `{"role_ids":[2,1],"revision":3}` |
| `conversation_updated` | Owner 绑定或解绑 single 会话工作区 | `{"workspace_binding_id":1,"revision":4}` |

`message_done` 的终态体现在消息 `status`（`done | stopped | error`）；`error_code` 仅在失败时非空。
`message_part_update` 携带完整消息，客户端按 message revision 替换；M4a 用它显示不含原始参数/输出的工具名、
`running/success/failed/rejected` 状态和可选耗时。`member_updated.revision` 是会话共享 revision，客户端可刷新
会话成员并忽略旧 revision。`conversation_updated` 使客户端刷新共享会话配置并忽略旧 revision。
`message_regenerated`、`conversation_state` 属于后续
里程碑预留，当前不会下发。

## 客户端幂等要求

- `event_seq` 不大于本地游标的事件必须丢弃
- 同一 `(message_id, revision, delta_seq)` 已应用过的事件必须丢弃
- 未知事件类型或未知 part 类型必须忽略或降级为占位，不得导致页面崩溃
- 收到的 `stream_epoch` 与本地记录不一致时，必须放弃本地游标并按快照重建
