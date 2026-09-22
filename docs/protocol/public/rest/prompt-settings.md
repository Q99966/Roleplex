# 提示词配置与生效来源

| 元数据 | 值 |
|---|---|
| 状态 | 已实现：平台/世界/会话配置、来源预览与实际采用版本；持久会话上下文和压缩仍待后续批次 |
| 协议版本 | 1；ContextBuilder 序列化版本 6 |
| 复核日期 | 2026-09-22 |
| 事实来源 | `routers/prompt_settings.py`、`context/prompts.py`、`agent/capabilities.py`、`services/chat.py`、`services/execution_usage.py` |
| 验证 | `test_prompt_settings.py`、`prompt-settings.spec.ts`、`prompt-worlds.spec.ts` |

## 归属与权限

全部接口要求当前 World 的 Owner Token，响应 `Cache-Control: no-store`。会话入口还检查 Owner 的实际成员关系和会话归属；已删除、无权访问或不存在的会话统一返回 `404 CONVERSATION_NOT_FOUND`。预览角色必须为本会话的存活、启用且归该 Owner 所有的成员，否则返回 `404 ROLE_NOT_FOUND`。Guest 返回 403，不读取私有提示词或来源配置。

平台规则分为软件固定执行规则和可配置协作规则。固定部分只展示；服务端鉴权、工具权限和人工审批不因提示词文字改变。平台协作规则使用软件默认模板，Owner 可以设置当前世界覆盖；没有跨 World 的写入参数或全局管理员入口。

## 世界配置

`GET /api/prompt-settings` 返回：

```json
{
  "world_name": "default",
  "revision": 0,
  "platform_override": null,
  "world_prompt": "",
  "platform_default": "软件提供的默认协作规则",
  "template_version": 1,
  "runtime_rules": "软件固定执行规则",
  "updated_at": null
}
```

`PUT /api/prompt-settings` 接受完整配置 `{expected_revision, platform_override, world_prompt}`。expected_revision 是 0–2,147,483,647 范围内的整数，其余两个字段必须明确提供。platform_override 为 null 时继承默认模板；字符串（包括空串）是显式覆盖。world_prompt 为空表示该层不追加指令。两个文本各最多 100,000 字符，保留原换行与空白；拒绝未知字段。

使用数据库 CAS 原子更新两个字段并递增独立的 prompt revision，返回新的完整表示。与世界决策预算版本分开；不修改已有角色、消息和运行。版本不匹配返回 `409 PROMPT_REVISION_CONFLICT`，客户端不能自动用新 revision 重发旧输入。

默认模板由软件版本维护，恢复默认提交 null。升级默认模板不覆盖已保存的自定义字符串；预览返回当前模板版本。保存只改变后续输入组装，已经发出的模型请求不重写。

## 会话配置

`GET /api/conversations/{conversation_id}/prompt-settings` 返回 `{conversation_id, revision, prompt, updated_at}`。

`PUT /api/conversations/{conversation_id}/prompt-settings` 接受 `{expected_revision, prompt}`，prompt 最多 100,000 字符，允许空串。Owner/成员检查后进行 CAS，成功递增会话独立的 prompt revision，返回新表示。普通会话成员、工作流图和消息 revision 不因此重排；世界和角色配置也不改变。

单聊、群聊使用相同入口。会话提示词附加在会话身份说明中，不替代世界或角色规则。读取和预览不调用模型、不创建执行，也不启动压缩。

## 按角色预览

`GET /api/conversations/{conversation_id}/prompt-preview?role_id=<id>` 返回：

- `world_name`、`conversation_id`、`role_id`、`role_name`：本次准确对象；`configured_tools` 为当前服务端角色配置，用于区分已配置与当前可用，不能从过期客户端列表推断。
- `revisions`：`{template, world, role, conversation}`。世界和会话是独立提示词版本，角色使用其配置 revision。
- `layers`：按顺序列出 runtime、platform、world、role、skills、conversation。每层含 `key/title/source/revision/text/characters/fingerprint`；source 区分 runtime、default、override、world、role、inline、conversation。fingerprint 是该层文本的 SHA-256，不是访问凭据；characters 不是厂商 Token 数。
- `capabilities`：`{fingerprint, tools}`。每个工具含 `name/description/parameters/source/danger`。这是普通角色在当前会话的可用定义，不授予工作流图管理权，不表示资源已执行或已通过 Shell 审批。工作流使用它自己的 execution allocation。
- `latest_execution`：最近有实际来源记录的该角色执行，或 null；包含 `execution_id/status/snapshot`。snapshot 是调用时的来源版本、无正文的层摘要和工具指纹，不从最新配置反推旧请求内容。

本入口不返回聊天历史，也不是完整请求的上下文占用预览。旧 inline Skills 仍按原规则注入，Skills/MCP 管理模块尚未开放；配置字段存在不表示对应工具可用。

## 实际生效与采用记录

预览与 ContextBuilder 共用提示词序列化。模型输入包含固定规则、有效平台协作规则、世界提示词、角色提示词与旧技能说明、会话元数据与会话提示词；空平台覆盖和空世界层不额外注入文本。当前消息、历史和工作流输入继续按原构建规则处理。

Context schema 6 兼容新增平台/世界指纹和配置版本诊断。实际工具集合由 `ExecutionCapabilities` 解析后交给工厂，名称和规范化参数 Schema 必须一致；实际调用仍逐次鉴权。身份、能力或定义变化使快照无法安全物化时返回生成错误 `AGENT_CAPABILITIES_CHANGED`，不降级绑定另一套未核对工具。

每次 execution 的来源记录在模型调用开始时，与既有用量记录的短事务一起持久化。完成事件可补齐缺失记录；同一 execution 已有来源不被覆盖。只有预检而没有调用模型的执行不生成“已采用”记录。内容仅含版本、来源类别、字符数、指纹及工具身份，不保存完整 Prompt，也不补造迁移前记录。Provider 是否接受请求或任务是否成功仍以调用/执行事实为准。

## 错误与客户端恢复

| 错误 | HTTP/生成状态 | 处理 |
|---|---|---|
| `PROMPT_REVISION_CONFLICT` | 409 | 保留输入，读取最新配置并由用户选择采用；不盲目覆盖 |
| `PROMPT_STORAGE_UNAVAILABLE` | 503 | 短事务重试后仍无法保存，返回安全错误，不输出 SQL 参数或提示词；刷新核对结果 |
| `AGENT_CAPABILITIES_CHANGED` | 生成 failed、context_rejected | 尚未调用模型，重新核对当前授权和工具定义后再发起 |
| `VALIDATION_ERROR` | 422 | 检查字段类型、长度和未知字段 |

网络丢失响应时，客户端先读取配置核对版本和值，不自动重复提交。当前值已经等于目标且版本前进时可显示已保存；不能仅凭请求失败宣称已回滚。

前端在当前登录期间按 World、Owner 和会话保留面板草稿，切换设置分类/详情模块不清空输入。认证/World 切换后不恢复上一身份的私有弹窗；未保存的提示词草稿不写入浏览器持久存储，刷新/退出登录后需以服务端已提交版本为准。该草稿机制与工作流 IndexedDB 恢复分开。

本批不新增 WS 消息类型。配置通过自己的读/写响应及显式刷新核对；保存不会广播私有正文，消息与运行事件保持原有契约。
