# 数据模型：表与字段说明

| 元数据 | 值 |
|---|---|
| 受众 | 内部 |
| 状态 | 部分已实现（未接线的表在下文标注为"预留"） |
| 协议版本 | 不适用（内部实现，不承诺客户端兼容性） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/models.py`、`backend/alembic/versions/` |
| 复核日期 | 2026-08-17 |

本文用于直接查看数据库时理解每张表和字段的用途。**字段的权威定义仍在 `models.py` 和迁移文件中**：类型、长度、约束以代码为准，本文只解释语义、取值范围和为什么这样设计。字段增删时同步更新本文。

数据库连接方式见 [README](../../../README.md)。表结构只由 Alembic 迁移创建，不要用客户端工具直接改表。

## 通用约定

- **主键 `id`**：全部是自增整数，同时充当稳定排序键；`messages` 的时间顺序就按 `id` 排，不依赖 `created_at`。
- **时间字段**：带时区的 UTC 时间。SQLite 里以 ISO 字符串形式存储（如 `2026-08-17 05:35:10.606099`），显示为 UTC 而不是本地时间。
- **`*_json` 字段**：通用 JSON 类型，SQLite 里落成 TEXT，客户端工具中看到的是 JSON 字符串。
- **布尔字段**：SQLite 里是 `0`/`1`。
- **外键删除行为**：`CASCADE` 表示随父记录一起删除（会话删除时消息、成员、事件一并清理）；`SET NULL` 表示父记录消失后保留本记录、只把引用置空（历史仍可读）；`RESTRICT` 表示被引用时禁止删除（Owner 账号、被角色引用的模型配置）。
- **多态引用不建外键**：`conversation_members.member_id`、`messages.sender_id` 可能指向用户也可能指向角色，因此没有库级外键，归属由服务层校验。查询时必须结合 `member_type` / `sender_type` 判断该去哪张表关联。
- **状态类字段是字符串枚举**：取值在下文列出，库层不做约束，由服务端保证。

## 表总览

| 表 | 用途 | 当前状态 |
|---|---|---|
| `alembic_version` | Alembic 记录当前迁移版本，只有一行 | 已实现（由工具维护） |
| `instance_settings` | 实例单例，保存谁是 Owner | 已实现 |
| `users` | 本地账号 | 已实现 |
| `model_configs` | 模型厂商配置与加密后的 API Key | 已实现 |
| `roles` | Agent 角色定义 | 已实现 |
| `conversations` | 会话元数据、事件序号分配器 | 已实现 |
| `conversation_members` | 会话成员（用户/角色）与个人偏好 | 已实现 |
| `messages` | 会话消息 | 已实现 |
| `generations` | 一次 Agent 生成的状态机 | 已实现 |
| `event_log` | 持久化事件流，断线恢复的唯一可靠来源 | 已实现 |
| `queue_jobs` | 会话串行队列的持久化任务 | 预留，当前无代码写入 |
| `invites` | 邀请码与使用次数 | 预留 |
| `artifacts` / `artifact_versions` | 产物身份与不可变版本内容 | 预留（读取端点已实现，创建/更新未实现） |
| `attachments` | 上传文件元数据 | 预留 |
| `tool_calls` | 工具调用审计 | 已实现（每次工具调用结束时写入） |

"预留"表示表已经由迁移建好、但当前里程碑没有任何代码写入，看到空表是正常的。

## instance_settings

Owner 单例的载体。注册时用一条 `UPDATE ... WHERE id=1 AND owner_user_id IS NULL` 原子认领，保证并发注册只会产生一个 Owner。

| 字段 | 含义 |
|---|---|
| `id` | 恒为 `1`，单行表 |
| `owner_user_id` | Owner 的用户 ID；为空表示实例还没有 Owner。`RESTRICT` 保证 Owner 账号不能被删除 |
| `created_at` | 实例初始化时间 |

## users

本地账号。首个注册者成为 Owner，其余为 Guest。

| 字段 | 含义 |
|---|---|
| `username` | 登录名，全库唯一 |
| `password_hash` | bcrypt 哈希，不可逆，看到乱码是正常的 |
| `nickname` | 展示名，也用于群聊上下文里的发言人前缀 |
| `avatar` | 头像地址，可为空 |
| `is_owner` | 是否 Owner。**只由服务端在注册事务内写入**，任何接口都不接受客户端提交该字段 |
| `token_version` | Token 版本。登出全部设备或改密时递增，已签发的 Token 因版本不匹配立即失效 |
| `created_at` | 注册时间 |

## model_configs

模型厂商连接配置，属于 Owner。

| 字段 | 含义 |
|---|---|
| `created_by` | 所属用户，当前只有 Owner 能创建 |
| `name` | 配置显示名 |
| `provider_type` | `anthropic` 或 `openai_compatible`（后者覆盖 DeepSeek/智谱/Kimi/通义/Ollama/OpenRouter 等兼容 OpenAI 协议的服务） |
| `base_url` | 兼容协议服务的接入地址；官方默认地址时为空 |
| `api_key_encrypted` | 用实例密钥加密后的 API Key。**接口只返回打码尾号，明文不落库、不进日志** |
| `capability_overrides_json` | 按厂商覆盖内置能力表（是否支持视觉、并行工具调用、usage 口径等），默认 `{}` |
| `created_at` | 创建时间 |

被角色引用时不可删除（`RESTRICT`）。

## roles

Agent 角色定义，是"联系人"的数据来源。

| 字段 | 含义 |
|---|---|
| `created_by` | 所属 Owner；与 `name` 组成唯一约束 `uq_role_owner_name` |
| `name` / `avatar` / `description` | 展示信息 |
| `tags_json` | 能力标签数组，用于联系人列表展示 |
| `system_prompt` | 角色的 System Prompt |
| `model_config_id` | 使用哪份厂商配置；保存时校验属于同一 Owner |
| `model_name` | 具体模型名，如占位的 `fake-model` |
| `params_json` | 采样参数（temperature、max_tokens 等）。运行时按模型能力剔除不兼容项 |
| `skills_json` | 追加到 System Prompt 的技能片段 |
| `builtin_tools_json` | 启用的内置工具名列表 |
| `mcp_servers_json` | MCP server 配置列表（预留，M5 接入） |
| `mcp_tools_cache_json` | 测试连接时缓存的 MCP 工具清单，兼作能力标签来源（预留） |
| `active` | 是否可用；停用的角色不参与回复 |
| `created_at` / `updated_at` | 创建与最后修改时间 |

## conversations

会话元数据，同时是**该会话事件序号的分配器**。

| 字段 | 含义 |
|---|---|
| `type` | `single`（单聊）或 `group`（群聊，M4 接入） |
| `title` | 会话标题 |
| `orchestrator_enabled` / `orchestrator_role_id` | 是否启用编排主 Agent 及其角色（预留，M4b） |
| `created_by` | 创建者 |
| `last_message_at` | 最后一条消息时间，用于会话列表排序 |
| `revision` | 会话级版本，发送消息时递增。当前只作为变更计数，尚未参与冲突检测 |
| `event_seq` | **该会话已分配的最大事件序号**。每写一条事件就在同一事务内 `+1`，是 `event_log.event_seq` 的唯一来源，保证不重号 |
| `created_at` | 创建时间 |

注意"置顶/归档"不在本表：多人会话下它们是个人偏好，存在成员表里。

## conversation_members

会话成员，同时承载用户级偏好。

| 字段 | 含义 |
|---|---|
| `conversation_id` | 所属会话 |
| `member_type` | `user` 或 `role`，决定 `member_id` 指向哪张表 |
| `member_id` | 用户 ID 或角色 ID，**无库级外键**，由服务层校验存在性与归属 |
| `last_read_message_id` | 已读位置，用于未读计数（预留） |
| `pinned` / `archived` | 个人置顶、归档；只对 `member_type='user'` 有意义 |
| `joined_at` | 加入时间 |

唯一约束 `uq_conversation_member` 保证同一成员不会重复加入。**所有消息和会话接口的鉴权都查这张表**：请求者必须是该会话的 `user` 成员，否则一律按会话不存在处理。

## messages

会话消息。用户消息和角色回复都在这张表里。

| 字段 | 含义 |
|---|---|
| `id` | 自增主键，同时是时间排序键 |
| `conversation_id` | 所属会话 |
| `sender_type` | `user`（真人）/ `role`（Agent）/ `orchestrator` / `system` |
| `sender_id` | 用户 ID 或角色 ID，按 `sender_type` 解释；系统消息可为空 |
| `reply_to_id` | 引用的消息（预留，当前会持久化但不影响回复逻辑） |
| `client_message_id` | 客户端生成的幂等键。与 `(conversation_id, sender_id)` 组成唯一约束 `uq_message_client_key`，重复提交直接命中已有消息，不会重复生成 |
| `mentions_json` | `@` 到的角色 ID 或 `"all"`（预留，群聊调度时生效） |
| `parts_json` | 消息内容，part 数组。当前只有 `{"type":"text","text":"..."}`；后续扩展 code / image / file / artifact / tool_call，客户端对未知类型降级展示 |
| `status` | `pending`（已建未开始）/ `generating`（流式中）/ `done`（完成）/ `error`（失败）/ `stopped`（用户停止）/ `interrupted`（进程重启时遗留的未完成生成，启动时自动改写） |
| `revision` | 消息乐观锁版本。每次内容或状态变化 `+1`，客户端据此幂等应用事件；**一条消息只由其生成任务这一个 reducer 修改** |
| `pinned` | 是否 pin（预留，M3） |
| `chain_id` | 一次触发链的标识，与 `generations.run_id` 相同值，用来把用户消息、生成和角色回复串起来 |
| `meta_json` | 附加元数据（usage、重生成的旧版本等，预留），默认 `{}` |
| `created_at` | 创建时间 |

流式过程中 `parts_json` 不是逐片段写库的：增量先在内存累积并广播，按节流间隔和最终完成时落库，因此生成中途读到的文本可能落后于界面显示。

## generations

一次 Agent 生成的状态机，是"停止生成"和"恢复"的抓手。

| 字段 | 含义 |
|---|---|
| `conversation_id` | 所属会话 |
| `assistant_message_id` | 本次生成产出的角色消息；消息被删时置空 |
| `stream_epoch` | 排队时的进程事件纪元，可据此判断这次生成属于哪次进程生命周期 |
| `status` | `queued`（已排队）/ `running`（执行中）/ `completed` / `stopped`（用户停止）/ `failed` |
| `run_id` | 本次执行的链路标识，等于消息的 `chain_id`，日志按它串联 |
| `error_code` | 失败时的稳定错误码，成功或停止时为空 |
| `started_at` / `ended_at` | 实际开始与结束时间 |
| `stop_requested_at` | 收到停止请求的时间。它和 `ended_at` 的差值就是取消传播耗时 |

查"当前是否有生成在跑"就是查本表 `status IN ('queued','running')`，索引 `ix_generations_conversation_status` 就是为此建的。

## event_log

持久化的会话事件流。**断线重连的唯一可靠恢复来源**：事件先写本表并提交，再广播给在线连接；内存广播器只服务在线订阅者，不承诺补齐。

| 字段 | 含义 |
|---|---|
| `conversation_id` | 所属会话 |
| `event_seq` | 会话内单调递增序号，来自 `conversations.event_seq`。唯一约束 `uq_event_conversation_seq` 保证不重号，客户端按它去重和续传 |
| `event_type` | 事件名，当前有 `message_created` / `message_delta` / `message_done` |
| `stream_epoch` | 产生该事件的进程纪元；客户端发现 epoch 变化就放弃增量、改用完整快照 |
| `generation_id` | 关联的生成；与生成无关的事件为空 |
| `revision` | 事件对应的消息版本 |
| `delta_seq` | 流式增量序号，从 1 连续递增；非增量事件为空 |
| `payload_json` | 事件负载，只包含可公开字段，结构见 [WebSocket 协议](../public/websocket/conversation-stream.md) |
| `created_at` | 落库时间 |

一次完整回复的事件形态：用户消息 `message_created` → 角色占位消息 `message_created` → 若干 `message_delta` → `message_done`。

## queue_jobs（预留）

单进程会话串行队列的持久化任务状态，用于重启后恢复未完成的调度。当前调度直接用内存中的 asyncio 任务，本表没有写入。

| 字段 | 含义 |
|---|---|
| `conversation_id` / `generation_id` | 任务归属 |
| `status` | `queued` / `running` / 终态 |
| `payload_json` | 任务参数 |
| `attempts` | 已尝试次数，用于重试上限 |
| `cancel_requested` | 是否已请求取消 |
| `created_at` / `started_at` / `ended_at` | 生命周期时间戳 |

## invites（预留）

邀请码。兑换时用单条原子 `UPDATE ... WHERE code=? AND NOT revoked AND used_count<max_uses AND expires_at>now` 完成，影响行数为 1 才算成功。

| 字段 | 含义 |
|---|---|
| `conversation_id` | 邀请加入哪个会话 |
| `code` | 邀请码，全库唯一 |
| `created_by` | 签发人 |
| `expires_at` | 过期时间，为空表示不过期 |
| `max_uses` / `used_count` | 使用次数上限与已用次数 |
| `revoked` | 是否已撤销 |

## artifacts / artifact_versions（预留）

产物及其不可变版本。`artifacts` 是身份记录，内容全部在版本表里，消息 part 引用固定版本，因此产物更新后历史消息里的旧版本不会漂移。

| `artifacts` 字段 | 含义 |
|---|---|
| `conversation_id` | 所属会话 |
| `kind` | `html` / `markdown` / `code` / `svg` |
| `title` | 标题 |
| `language` | 代码类产物的语言 |
| `current_version` | 当前最新版本号，更新时作为乐观锁比对基准 |

| `artifact_versions` 字段 | 含义 |
|---|---|
| `artifact_id` / `version` | 归属与版本号，唯一约束 `uq_artifact_version` |
| `content` | 该版本的完整内容（当前设计为单文件字符串） |
| `created_by_message_id` | 由哪条消息产生 |
| `created_at` | 创建时间 |

## attachments（预留）

上传文件元数据，文件本体在磁盘上。

| 字段 | 含义 |
|---|---|
| `message_id` | 绑定的消息；先上传后绑定时为空，未绑定的记录属于待清理孤儿 |
| `uploader_id` | 上传者 |
| `filename` | 原始文件名（用户提供，仅用于展示） |
| `mime` / `size` | 类型与字节数，用于白名单和限额校验 |
| `path` | 服务端生成的存储路径，全库唯一；**不使用用户提供的文件名做路径** |

## tool_calls

工具调用审计，用于回答"谁在什么时候通过哪个角色调了什么工具"。每次工具调用结束时写入一行，
包括被执行层拒绝的调用。权限判定与审计的约定见 [Agent 运行时](agent-runtime.md)。

| 字段 | 含义 |
|---|---|
| `conversation_id` / `message_id` / `role_id` | 发生位置与执行角色 |
| `triggered_by_user_id` | 触发链路的真人，Guest 配额和危险工具拦截按它判定 |
| `tool_name` | 工具名 |
| `args_summary` | 参数摘要，**不得写入凭据或敏感参数原文** |
| `status` | 执行结果状态：`ok`（正常返回）、`rejected`（危险级别被执行层拒绝）、`error`（工具自身失败） |
| `duration_ms` | 耗时 |
| `created_at` | 记录时间 |

## 排查用查询

只读查询，可直接在数据库客户端里跑。

一次对话的完整链路（消息 + 生成 + 事件数）：

```sql
SELECT m.id, m.sender_type, m.status, m.revision, m.chain_id,
       g.status AS generation_status, g.error_code,
       (SELECT COUNT(*) FROM event_log e WHERE e.generation_id = g.id) AS event_count
FROM messages m
LEFT JOIN generations g ON g.assistant_message_id = m.id
WHERE m.conversation_id = 1
ORDER BY m.id;
```

某个会话的事件序列（确认是否有跳号或重复）：

```sql
SELECT event_seq, event_type, revision, delta_seq, generation_id
FROM event_log
WHERE conversation_id = 1
ORDER BY event_seq;
```

会话成员及其真实名称（多态成员需要按 `member_type` 分别关联）：

```sql
SELECT cm.member_type,
       CASE cm.member_type WHEN 'user' THEN u.nickname WHEN 'role' THEN r.name END AS display_name,
       cm.pinned, cm.archived
FROM conversation_members cm
LEFT JOIN users u ON cm.member_type = 'user' AND u.id = cm.member_id
LEFT JOIN roles r ON cm.member_type = 'role' AND r.id = cm.member_id
WHERE cm.conversation_id = 1;
```

卡住或异常结束的生成：

```sql
SELECT id, conversation_id, status, error_code, started_at, stop_requested_at, ended_at
FROM generations
WHERE status NOT IN ('completed')
ORDER BY id DESC;
```
