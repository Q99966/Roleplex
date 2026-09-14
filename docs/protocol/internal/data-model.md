# 数据模型：表与字段说明

| 元数据 | 值 |
|---|---|
| 受众 | 内部 |
| 状态 | 部分已实现（未接线的表在下文标注为"预留"） |
| 协议版本 | 不适用（内部实现，不承诺客户端兼容性） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/models.py`、`backend/alembic/versions/` |
| 复核日期 | 2026-09-11 |

本文用于直接查看数据库时理解每张表和字段的用途。**字段的权威定义仍在 `models.py` 和迁移文件中**：类型、长度、约束以代码为准，本文只解释语义、取值范围和为什么这样设计。字段增删时同步更新本文。

数据库连接方式见 [README](../../../README.md)。表结构只由 Alembic 迁移创建，不要用客户端工具直接改表。
正常世界模式下，每个 `worlds/{世界名}/` 目录拥有独立数据库、JWT 密钥、模型 Key 加密密钥和附件目录；
表内 id 只在该世界内有意义。显式 `DATABASE_URL` 时进入兼容模式，仍使用原有 `data/` 密钥目录。

## 通用约定

W1d 增量（已实现，迁移 `0009_runtime_services`）：instance_settings/workspace_bindings/conversations
分别新增 process_limit（20/5/3）和 process_limit_version；工作区新增默认 false 的 services_enabled。
runtime_gate 单行保存短事务序列和关闭门槛；runtime_entries 保存顶层运行实例 ID、稳定排序号、来源
Owner/会话/工作区/execution/chain/角色/调用身份、kind/state/revision、日志进程身份、PID/出生身份、声明与租用
端口、审批引用、错误/退出/健康结果、生命周期时间及加密日志尾部。leased_port 唯一且确认回收后置空，
(execution_id,tool_call_id) 唯一。来源身份为不可变快照，不因会话/工作区删除而抹掉历史回收证据。
runtime_cleanup_operations 保存 scope/scope_id、原因/触发者、进程身份、冻结数量及操作状态/时间；
runtime_cleanup_items 保存操作与实例关系、固定 ordinal、逐项状态/结果/错误及起止时间，每操作每实例唯一。
迁移 `0011_cleanup_failure_links` 为 runtime_cleanup_operations 增加 error_code（可空固定失败原因）和
superseded_by_id（可空后续核查批次身份）；旧数据保持空，不猜测历史原因。机器日志写失败时仍在原批次保留失败状态，
成功重试保留旧条目事实并关联新批次，不把原失败改写为原时刻成功。
迁移 `0012_runtime_boot_identity` 增加 runtime_entries.host_boot_id（可空内核启动 UUID）；仅实际登记时读取，
旧数据不回填。主机启动身份已改变可以证明旧内核中的实例不再存活；单纯墙钟变化或后端进程重启不是此证明。
迁移 `0013_runtime_recovery_receipts` 增加 runtime_entries.recovery_token_hash（可空一次性回收令牌 SHA-256）。
监管器通过独立管道接收随机令牌，确认后代回收后原子写入当前 World 的 `.runtime-receipts/` 私有凭据；
内容只含运行身份、PID/出生身份、退出码和证明令牌，不含脚本/输出，也不进入日志或 API。终态落库后清理匹配凭据。
兼容数据库模式按连接配置不可逆标识隔离目录；旧数据无证明时不回填，不把根 PID 消失当作服务后代已回收。
新 Linux birth 使用 `linux:<内核 starttime tick>`，不受墙钟调整影响；读取旧 epoch 字符串时保持兼容并保守核查。
所有查询仍按当前 World 与当前资源权限鉴权，来源快照不授予访问已删除资源的权利；日志尾部另行到期清除。
迁移 `0010_runtime_conversation_ref` 增加 conversation_ref_id（可空，引用 conversations.id，ON DELETE SET NULL）：
当前 API/模型授权与列表以该引用为准；conversation_id 仅保留历史来源。物理删除后即便整数 ID 被复用也不会
重新关联旧实例。旧数据仅在 execution 仍能证明会话归属时补齐引用，不能凭快照 ID 猜测授权。

- **主键 `id`**：多数业务表是自增整数；运行实例及回收批次使用随机字符串身份与独立 sequence/ordinal 排序。
  `messages` 的时间顺序按 `id` 排，不依赖 `created_at`。
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
| `agent_executions` | Agent 执行身份、generation 一对一关系及后续父子树 | E0 已实现 |
| `workspace_bindings` | 当前 World Owner 从前端登记的绝对根工作目录 | W1a 已实现 |
| `execution_workspaces` | execution 对 managed directory 的租用与路径快照 | W1a 已实现 |
| `runtime_gate` | 当前 World 的短事务配额序列及关闭门槛 | W1d 已实现 |
| `runtime_entries` | 顶层命令/后台服务、来源与独立生命周期 | W1d 已实现 |
| `runtime_cleanup_operations` | 范围冻结与回收批次对账 | W1d 已实现 |
| `runtime_cleanup_items` | 固定回收顺序和逐项结果 | W1d 已实现 |
| `event_log` | 持久化事件流，断线恢复的唯一可靠来源 | 已实现 |
| `queue_jobs` | 会话串行队列的持久化任务 | M4a 已实现 |
| `invites` | 邀请码与使用次数 | 预留 |
| `artifacts` / `artifact_versions` | 产物身份与不可变版本内容 | 预留（读取端点已实现，创建/更新未实现） |
| `attachments` | 上传文件元数据 | 预留 |
| `tool_calls` | 工具调用审计 | 已实现（每次工具调用结束时写入） |
| `tool_execution_details` | Owner 私有加密执行详情 | 已实现 |
| `tool_approval_requests` | Owner 逐次 Shell 审批与加密请求 | W1c 已实现 |

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

**删除采用墓碑，永不物理删除**：消息只按 `sender_id` 记录发送者，角色行一旦消失，
历史里就再也查不出"谁说的"。删除时保留 `id` / `name` / `avatar` / `deleted_at`，
清空全部可用配置并置 `active=false`。

| 字段 | 含义 |
|---|---|
| `created_by` | 所属 Owner；与 `name` 组成部分唯一索引 `uq_role_owner_name_active`（仅约束 `deleted_at IS NULL` 的行） |
| `name` / `avatar` / `description` | 展示信息；墓碑保留 `name` 与 `avatar`，清空 `description` |
| `tags_json` | 能力标签数组，用于联系人列表展示；墓碑清空 |
| `system_prompt` | 角色的 System Prompt；墓碑清空 |
| `model_config_id` | 使用哪份厂商配置；保存时校验属于同一 Owner。**可空**：墓碑要清除模型绑定 |
| `model_name` | 具体模型名，如占位的 `fake-model`；墓碑清空 |
| `context_window_tokens` | Owner 为角色所选模型配置的输入+输出总窗口，默认 200K；墓碑重置为默认值 |
| `params_json` | 采样参数（temperature、max_tokens 等）。运行时按模型能力剔除不兼容项；墓碑清空 |
| `skills_json` | 追加到 System Prompt 的技能片段；墓碑清空 |
| `builtin_tools_json` | 启用的内置工具名列表；墓碑清空 |
| `mcp_servers_json` | MCP server 配置列表（预留，M5 接入）；墓碑清空 |
| `mcp_tools_cache_json` | 测试连接时缓存的 MCP 工具清单，兼作能力标签来源（预留）；墓碑清空 |
| `active` | 是否可用；停用的角色不参与回复。墓碑一律为 `false` |
| `deleted_at` | 墓碑时间；非空表示该角色已删除，只保留身份信息 |
| `created_at` / `updated_at` | 创建与最后修改时间 |

重名约束之所以改成部分唯一索引，是为了让墓碑保留原名的同时不挡住新建同名角色。
部分索引在 SQLite 与 PostgreSQL 上都支持，不依赖单一数据库的专属特性。

## conversations

会话元数据，同时是**该会话事件序号的分配器**。

**删除采用回收站**：只写 `deleted_at`，会话立即从列表消失但数据完整保留，
保留期（7 天）内可以恢复；超期后由服务启动时的清理任务真正级联删除
（见 `backend/app/services/retention.py`）。没有常驻定时清理任务。

| 字段 | 含义 |
|---|---|
| `type` | `single`（单聊）或 `group`（群聊，M4 接入） |
| `title` | 会话标题 |
| `orchestrator_enabled` / `orchestrator_role_id` | 是否启用编排主 Agent 及其角色（预留，M4b） |
| `workspace_binding_id` | 当前 World 可空工作区绑定；删除 binding 时置空，W1a 只允许 single 会话使用 |
| `created_by` | 创建者 |
| `last_message_at` | 最后一条消息时间，用于会话列表排序 |
| `revision` | 会话级版本，发送消息时递增。当前只作为变更计数，尚未参与冲突检测 |
| `event_seq` | **该会话已分配的最大事件序号**。每写一条事件就在同一事务内 `+1`，是 `event_log.event_seq` 的唯一来源，保证不重号 |
| `deleted_at` | 进入回收站的时间；非空表示已删除。索引 `ix_conversations_deleted_at` 供列表过滤与过期扫描 |
| `created_at` | 创建时间 |

注意"置顶/归档"不在本表：多人会话下它们是个人偏好，存在成员表里。

回收站中的会话对消息链路和 WebSocket 订阅等同于不存在：不能读历史、不能发言、
不能订阅事件，否则被删除的会话仍会产生新消息和新事件。

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
| `mentions_json` | M4a `@` 到的稳定角色 ID 或 `"all"`；群聊按该顺序去重调度，单聊忽略 |
| `parts_json` | 消息内容，part 数组。当前支持 `text`、不含原始参数/输出的 `tool_call` 过程卡；旧 `execution_summary` 只保留兼容（见[旧执行摘要](../public/messaging/execution-summary.md)）；后续扩展 code / image / file / artifact，客户端对未知类型降级展示 |
| `status` | `pending`（已建未开始）/ `generating`（流式中）/ `done`（完成）/ `error`（失败）/ `stopped`（用户停止）/ `interrupted`（进程重启时遗留的未完成生成，启动时自动改写） |
| `revision` | 消息乐观锁版本。每次内容或状态变化 `+1`，客户端据此幂等应用事件；**一条消息只由其生成任务这一个 reducer 修改** |
| `pinned` | 是否 pin（预留，M3） |
| `chain_id` | 一次触发链的标识，与 `generations.run_id` 相同值，用来把用户消息、生成和角色回复串起来 |
| `meta_json` | 附加元数据，默认 `{}`；`timeline_version=1` 标记有序展示，`stop_reason` 保存服务器停止原因；旧 `execution_summary_version=1` 仅用于识别旧摘要记录，不再为新消息设置；其他用途预留 |
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

## agent_executions

E0 起的 Agent 执行身份权威。`generations` 负责消息流生命周期，execution 负责执行身份、角色、类型、chain、
attempt 和后续 M4b 父子关系；queue payload 和日志都不能替代本表。

| 字段 | 含义 |
|---|---|
| `execution_id` | 最长 64 字符的唯一链路标识；日志使用该值，不使用自增主键冒充执行身份 |
| `parent_execution_id` | 可空的自引用；E0 single/group_role 为空，M4b 子执行才使用 |
| `conversation_id` | 所属会话；会话硬删除时级联删除执行树 |
| `generation_id` | 非空且唯一；一次实际 execution 对应一个 generation，generation 删除时级联删除 |
| `chain_id` | 真人消息触发的 chain，等于 generation `run_id` |
| `role_id` | 本次实际执行角色；角色真正硬删除时置空，墓碑不改历史执行身份 |
| `execution_kind` | E0 已使用 `single/group_role`；`orchestrator/subagent` 为 M4b 预留值，不代表已实现 |
| `dispatch_order` | 子执行在父 execution 内的稳定序号；E0 为空 |
| `attempt` | 从 1 开始；E0 固定为 1，M4b 重试才递增 |
| `task_text/context_hint_text` | M4b 子任务输入预留；E0 必须为空，不进入日志 |
| `status` | `queued/running/completed/failed/stopped/interrupted` |
| `error_code` | E0 启动/未收口关闭降级使用 `EXECUTION_INTERRUPTED`；其他终态与 generation 稳定错误一致 |
| `created_at/started_at/ended_at` | execution 生命周期时间，不能从日志时间反推 |

约束：`generation_id` 唯一；`(parent_execution_id, dispatch_order, attempt)` 唯一；按
`(conversation_id,status)`、`(parent_execution_id,dispatch_order,attempt)` 和 `chain_id` 建索引。E0 不反向解析
历史 `queue_jobs.payload_json` 来伪造旧 execution；迁移后的新 generation 才保证一对一完整。

## workspace_bindings

当前 World Owner 从前端登记的工作目录。数据库保存后端 canonicalize 后的绝对根，仅通过 Owner-only 接口
返回；产品没有部署级 allowed-root 白名单。一个 World 可以登记多个根目录各不相同的 Workspace。

| 字段 | 含义 |
|---|---|
| `created_by` | 当前 World Owner；删除 Owner 时级联清除绑定 |
| `display_name` | Owner 可读名称；active 行按 Owner 唯一 |
| `root_path` | 当前主机的规范绝对根；当前 World 内唯一，不能进入日志或 Guest 响应 |
| `workspace_kind` | W1a 固定 `managed_directory` |
| `file_tools_enabled` | 是否允许满足完整执行授权矩阵的原生文件工具，含 E1 局部编辑和 T2 搜索/范围读取；角色仍须逐工具显式启用 |
| `basic_commands_enabled` | 默认 false，W1b 起可单独启用结构化命令，与文件能力独立 |
| `shell_enabled` | 默认 false；W1c 可启用，仍要求每次脚本独立审批 |
| `active` | 停用后不能绑定新会话或继续执行工具调用 |
| `last_validated_at` | 后端最近一次成功 canonical 复核目录的时间 |
| `created_at` / `updated_at` | 生命周期时间 |

解除登记只删除本行并让 `conversations.workspace_binding_id` 置空，不删除物理目录。

## execution_workspaces

一次 execution 对 binding 的持久租用。W1a 同一 binding 同时最多有一个 `ready` 写执行；进程重启不恢复
工具调用，遗留 `creating/ready` 统一变为 `retained + EXECUTION_INTERRUPTED`。

| 字段 | 含义 |
|---|---|
| `execution_id` | 唯一外键到 `agent_executions.execution_id` |
| `workspace_binding_id` | 当前租用的 Workspace Binding |
| `workspace_kind` | W1a 固定 `managed_directory` |
| `root_path_snapshot` | execution 开始时捕获的规范绝对根；每次调用必须与当前 binding 一致且不得进入日志 |
| `status` | `creating/ready/retained/cleaned/failed`；W1a 正常终态保留为 retained |
| `error_code` | 创建、重启中断或后续清理失败的稳定错误码 |
| `created_at/ended_at/cleaned_at` | 租用生命周期时间 |

## tool_approval_requests（W1c）

迁移 `0008_shell_approvals` 新增审批表，脚本及执行上下文只在密文内，不新增明文脚本或输出列。

| 字段 | 类型与含义 |
|---|---|
| `id` | 整数主键 |
| `execution_id` | String(64)，引用 execution，删除级联 |
| `workspace_binding_id` | 可空工作区外键，删除置空，原始绑定身份仍在密文内 |
| `tool_call_id` / `tool_name` | String(128)/String(256)，宿主实际调用身份与工具名，不接受模型覆盖 |
| `request_encrypted` | Text，World 用途隔离密文，包含冻结脚本与执行上下文 |
| `request_digest` | String(64)，冻结请求的 SHA-256，批准必须匹配 |
| `status` | pending/approved/rejected/expired；approved 是决定，不是可重放的执行任务 |
| `requested_at` / `expires_at` / `resolved_at` | UTC 时间，resolved_at 未决定时为空 |
| `resolved_by` | 可空 Owner 用户外键，系统到期/取消/重启为空，用户删除置空 |

唯一约束 `(execution_id,tool_call_id)`，索引 `(status,expires_at)`。状态与幂等语义见
[Shell 审批](../public/messaging/shell-approvals.md)。

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

## queue_jobs

M4a 单进程会话串行队列的持久化任务状态。内存队列负责当前进程内唤醒和顺序，表只记录唤醒参数与任务终态；
execution 身份、目标角色、chain 和 execution kind 必须通过 generation 关联 `agent_executions` 读取。重启不恢复
调用 Provider，遗留 queued/running 任务统一降级为取消状态，对应 execution 变为 interrupted。

| 字段 | 含义 |
|---|---|
| `conversation_id` / `generation_id` | 任务归属 |
| `status` | `queued` / `running` / `completed` / `failed` / `cancelled` |
| `payload_json` | 只保存 `current_message_id`、`triggered_by_user_id`、权限类别和 `request_id`；不复制 execution/role/chain，不保存 Prompt 或工具原始参数 |
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

## tool_execution_details

S2 在既有 write-v1 / write-batch-v1 加密输出中兼容保存有界 diagnostic，分别属于单次调用或失败子项；
不新增表或字段，不从当前状态重建旧拒绝原因，详情与隐私边界以工具详情协议为准。

S1 workspace_service_status 使用原 input_encrypted/output_encrypted 的通用有界文本采集，保存查询时输入和结果；
不新增表/字段，不保存原脚本或日志，不以当前登记重建历史列表，具体边界见工具详情协议。

D 复用既有 output_encrypted 存储 write-v1 私有记录，包含原始有界结果和独立有界 write 差异对象；
不新增字段，详情读取时拆分为原 output 与兼容 write 扩展。差异生命周期与原记录相同，具体结构以工具详情协议为准。
E2 多文件读取复用 output_encrypted 的 read-batch-v1 格式，保存本调用的有界逐项结果；接口拆出 read_batch，
不复制到共享消息，不新增子项执行表。崩溃前未落库的结果不补造，生命周期仍以工具详情协议为准。
E2 多文件修改复用 write-batch-v1，包含已观察逐项状态/提交结果与有界嵌入 diff；输入只保留目标/版本/长度摘要，
无新表或字段，不保证崩溃前未落库的内存事实恢复；接口及失败降级以工具详情协议为准。

Owner 专属的有界执行内容，与共享消息和机器审计分开。具体接口、权限、截断和保留语义见
[工具执行详情](../public/messaging/tool-details.md)，迁移为 `0007_tool_execution_details`。

| 字段 | 含义 |
|---|---|
| `id` | 标准整数主键 |
| `message_id` | 所属消息 FK，物理删除级联 |
| `execution_id` | 持久 execution FK，物理删除级联 |
| `call_id` | 本消息内调用身份；与 message_id 联合唯一 |
| `tool_name` | 白名单工具名 |
| `status` | 与工具 part 同一所有者事务维护的运行/终态；启动遗留 running 改为 interrupted，工具卡通过 EXECUTION_INTERRUPTED 标记降级 |
| `input_encrypted` / `output_encrypted` | 当前 World 独立用途派生密钥的密文；可空，明文绑定 message_id/call_id |
| `started_at` / `ended_at` | 实际观察的开始/结束 UTC 时间；未知结束保持空 |
| `expires_at` | 开始后 7 天；带清理索引，过期不可读，启动清空密文 |

消息 `meta_json.timeline_version=1` 标记有序展示；文本 part_id 只是展示定位字段，不改变模型历史正文。
W1c Shell 详情的 input_encrypted 为空，脚本关联 tool_approval_requests 解密展示；output_encrypted 保存
shell-v1 结构的有界 stdout/stderr 与实测执行元数据。仅增加密文内部表示，不新增列或复制脚本。

## tool_calls

工具调用审计，用于回答"谁在什么时候通过哪个角色调了什么工具"。每次工具调用结束时写入一行，
包括被执行层拒绝的调用。权限判定与审计的约定见 [Agent 运行时](agent-runtime.md)。

| 字段 | 含义 |
|---|---|
| `conversation_id` / `message_id` / `role_id` | 发生位置与执行角色 |
| `triggered_by_user_id` | 触发链路的真人，Guest 配额和危险工具拦截按它判定 |
| `execution_id` / `workspace_binding_id` | W1a 起关联实际 execution 与可选 workspace；解除登记后 workspace 可置空 |
| `tool_name` | 工具名 |
| `args_summary` | 参数摘要，**不得写入凭据或敏感参数原文** |
| `status` | 执行结果状态：`ok`（正常返回）、`rejected`（危险级别被执行层拒绝）、`error`（工具自身失败）、`cancelled`（W1b 命令因停止而取消，正常终态） |
| `duration_ms` | 耗时 |
| `created_at` | 记录时间 |

## 排查用查询

只读查询，可直接在数据库客户端里跑。

一次对话的完整链路（消息 + 生成 + 事件数）：

```sql
SELECT m.id, m.sender_type, m.status, m.revision, m.chain_id,
       g.status AS generation_status, g.error_code,
       x.execution_id, x.execution_kind, x.status AS execution_status,
       (SELECT COUNT(*) FROM event_log e WHERE e.generation_id = g.id) AS event_count
FROM messages m
LEFT JOIN generations g ON g.assistant_message_id = m.id
LEFT JOIN agent_executions x ON x.generation_id = g.id
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
