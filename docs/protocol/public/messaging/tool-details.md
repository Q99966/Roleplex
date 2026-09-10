# 工具执行详情

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner 接口；采集与存储为内部约定 |
| 状态 | 已实现，已人工验收 |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 事实来源 | `app/services/tool_details.py`、`app/routers/messages.py`、`ToolExecutionDetail` |
| 复核日期 | 2026-09-10 |
| 测试 | `backend/tests/test_tool_timeline.py`、`frontend/tests/commands/`、`frontend/tests/real-world/commands-provider.spec.ts` |

## 接口与资源归属

`GET /api/conversations/{conversation_id}/messages/{message_id}/tools/{call_id}`

需要有效 Owner Token、当前 World 会话归属和用户成员关系；回收站会话不可读，Guest 固定 403 OWNER_REQUIRED。
消息、会话或工具调用不匹配返回 404 TOOL_DETAILS_NOT_FOUND（会话不可见时 CONVERSATION_NOT_FOUND）。
响应 `Cache-Control: no-store`。客户端切换账号/World/会话时清除已加载详情，不使用 localStorage 缓存。
该只读接口幂等，不执行工具、不读取宿主文件，也不读取机器日志文件。

返回 availability 为 available/not_recorded/expired/unavailable。存在记录时还包含 tool_name、status、
started_at、ended_at（可空）、expires_at、input/output（可空）。input/output 各为
`{text, bytes, truncated}`：text 为有界文本，bytes 为截断前 UTF-8 字节数，truncated 表示截断。
status 为 running/success/failed/rejected/cancelled/interrupted；未记录输出不是空输出，工具未结束时 output=null。
expired 和 unavailable 不返回输入输出；解密失败为 unavailable，不回显密文、异常或密钥。

## 采集与保留

只采集已实现的 workspace_list/read/write/run_command；输入仅保留对应工具 schema 的字段，未知字段不保存。
未知/MCP/未接入工具仅有摘要，不自动采集原始对象。每份输入/结果最多 65536 UTF-8 字节，不截坏字符，
移除终端控制序列。内容按文本显示，禁止作为 HTML、脚本或终端转义执行。

详情是 Owner 私有业务数据，用当前 World 密钥按独立用途派生密钥加密；密文绑定 message_id 与 call_id，
不能跨调用替换。只有通过服务端资源鉴权后才解密；不进入共享消息/WS、机器日志、工具审计或失败报告。
开始时保存输入，结束时保存结果；由消息所有者在更新工具 part 的同一事务写入，无逐 token/输出块事务。
调用开始 7 天后接口立即视为 expired，启动清除过期密文，随消息物理删除级联删除。取消只记录已观察到的
状态，无结果时 output=null；重启将 running 变 interrupted，ended_at 为空，不推断工具退出结果。

旧调用返回 not_recorded，不从文件或日志补造详情；已有摘要不变。公开工具卡仍只含安全元数据。

## 消息顺序与兼容

新角色消息 `timeline_version=1`，parts_json 按事件顺序包含 text 和 tool_call；文本段有稳定 part_id。
`message_delta` 兼容新增 part_id/part_index，增量只追加目标文本段。工具开始固定前段，结束原位更新。
工具 part 的 detail_available 表示该调用支持 Owner 详情；这是安全能力标记，不包含私有正文。
旧消息 timeline_version=0，工具位置未记录，客户端降级显示历史执行记录，不能猜测真实位置。
客户端忽略旧 revision，重连/快照复用有序 parts。ContextBuilder 将新分段还原为原正文后投影，保持历史文本语义。
