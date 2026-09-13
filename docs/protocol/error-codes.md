# Roleplex 错误码设计与注册表

| 元数据 | 值 |
|---|---|
| 受众 | 公开协议消费者、后端开发、测试与日志消费者 |
| 状态 | 已建立注册表；含明确标注的预留/部分实现项 |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/errors.py`、`backend/app/routers/`、`backend/app/security/`、`backend/app/agent/loop.py`、`backend/app/services/chat.py`、`backend/app/scheduling/` |
| 复核日期 | 2026-09-12 |

本文是稳定 `error_code` 的跨领域权威注册表。各领域协议负责说明“哪个接口或事件会返回哪些错误码”；
错误码本身的名称、含义、终态和重试语义只在本文定义，避免同一含义散落后漂移。

## 一、适用边界

`error_code` 用于机器可判断的稳定失败原因，可能出现在：

- REST `error.code`；
- WebSocket error frame；
- `message_done.payload.error_code`；
- `generations.error_code`；
- Agent 领域 `ProviderError.code`；
- 结构化日志中的 `error_code`。

异常类名、异常消息、HTTP 状态、日志级别和 `status` 都不能替代 `error_code`。并非每个 ERROR 都必须
有稳定错误码：没有业务语义的未知异常可以只记录 `error_type` 和 traceback，不能临时把异常文本加工成
一个未登记的错误码。

## 二、命名与兼容规则

1. 错误码使用大写 ASCII snake case：`^[A-Z][A-Z0-9_]{2,63}$`。
2. 前缀表达领域，如 `AUTH_`、`PASSWORD_`、`PROVIDER_`、`WORLD_`。
3. 客户端只能比较完整错误码，不得根据前缀推断权限或重试策略。
4. 已公开错误码不得改名或改变既有含义。需要新语义时新增错误码，并提供迁移/兼容说明。
5. 新错误码必须先登记本文，再加入代码常量、领域协议和测试；禁止继续散落新的字符串字面量。
6. 预留码不是当前可依赖行为；只有状态为“已实现”的错误码才能视为当前协议事实。

## 三、错误信封

REST 使用：

```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "CONVERSATION_NOT_FOUND",
    "request_id": "请求关联标识"
  }
}
```

当前 `message` 多数与 `code` 相同；消费者必须以 `code` 判断，不得比较 message。后续 message 可以本地化，
不构成兼容性承诺。策略校验可以附带安全的 `details`，但 details 不得包含密码、Token、Key 或资源存在性。

WebSocket error frame 使用：

```json
{"type":"error","payload":{"code":"CONVERSATION_NOT_FOUND"}}
```

生成终态使用：

```json
{"type":"message_done","payload":{"message":{},"error_code":"PROVIDER_TIMEOUT"}}
```

## 四、`status`、日志级别与重试

错误码不直接等于日志 ERROR。日志 v2 的终态建议固定为：

- `failed`：操作已失败；是否可重试见注册表。
- `timeout`：超时终止，通常可以有限重试。
- `rejected`：输入、权限、策略或当前状态拒绝；相同条件立即重试没有意义。
- `cancelled`：用户或上游主动取消；通常不带 error_code，不记 ERROR。

注册表的重试值：

- `no`：相同请求立即重试不会成功。
- `yes`：属于临时故障，可以在退避/限次约束下重试。
- `conditional`：必须修改输入、权限、配置、软件版本或资源状态后再尝试。

预期 4xx、策略拒绝和用户停止通常是 INFO/WARNING。只有未处理异常、系统不变量破坏或无法完成的内部
故障进入 ERROR/CRITICAL；provider 返回业务失败可以是 WARNING，同时在 Agent 终态携带 error_code。

## 五、通用、认证与账号错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `VALIDATION_ERROR` | 已实现 | REST/WS | 422/— | rejected | conditional | 请求结构或订阅控制参数无效；WS 不回显原始参数 |
| `HISTORY_CURSOR_INVALID` | 已实现 | REST | 422 | rejected | no | 历史窗口游标无效或属于其他会话；不回显游标 |
| `SHELL_APPROVAL_NOT_FOUND` | 已实现 | REST | 404 | rejected | no | 审批不存在或不属于当前会话 |
| `SHELL_APPROVAL_MISMATCH` | 已实现 | REST/工具 | 409/— | rejected | no | 审批摘要或加密身份不匹配，不执行 |
| `SHELL_ARGUMENT_INVALID` | 已实现 | 工具 | — | rejected | no | 脚本无效、超限或多余参数 |
| `SHELL_NOT_SUPPORTED` | 已实现 | REST/工具 | 409/— | rejected | no | 本机未解析到允许的 Shell |
| `SHELL_REJECTED` | 已实现 | 工具 | — | rejected | no | Owner 拒绝，不创建进程 |
| `SHELL_APPROVAL_EXPIRED` | 已实现 | 工具 | — | rejected | no | 等待过期、取消或重启，不执行 |
| `SHELL_REQUEST_CONFLICT` | 已实现 | 工具 | — | rejected | no | 同一调用身份已有记录，禁止重放 |
| `RUNTIME_WORLD_LIMIT` / `RUNTIME_WORKSPACE_LIMIT` / `RUNTIME_CONVERSATION_LIMIT` | 已实现 | REST/工具 | 409/— | rejected | conditional | 指定层的名额不足，不能静默扩大配额 |
| `RUNTIME_SCOPE_INVALID` / `RUNTIME_LIMIT_INVALID` | 已实现 | REST/工具 | 422/— | rejected | no | 范围或限额参数无效 |
| `RUNTIME_NOT_FOUND` | 已实现 | REST/工具 | 404/— | rejected | no | 实例/配置不存在或无权访问 |
| `RUNTIME_SCOPE_CLOSING` | 已实现 | REST/工具 | 409/— | rejected | conditional | 删除/退出门槛已关闭新启动 |
| `RUNTIME_REVISION_CONFLICT` | 已实现 | REST/工具 | 409/— | rejected | conditional | 配置或实例版本竞争，不覆盖新值 |
| `RUNTIME_REQUEST_CONFLICT` / `RUNTIME_NOT_STARTABLE` | 已实现 | REST/工具 | 409/— | rejected | no | 重复消费调用或状态不允许启动 |
| `RUNTIME_PORT_BUSY` | 已实现 | REST/工具 | 409/— | rejected | conditional | 端口已预留/占用，不停止陌生进程 |
| `RUNTIME_CLEANUP_UNCONFIRMED` | 已实现 | REST/工具 | 409/— | failed | conditional | 未确认回收，不释放占用或完成删除 |
| `RUNTIME_CLEANUP_CONFIRM_REQUIRED` | 已实现 | REST | 409 | rejected | conditional | 需明确确认范围回收，尚未停止资源 |
| `RUNTIME_NOT_SUPPORTED` | 已实现 | REST/工具 | 409/— | rejected | no | 当前平台的后台服务运行器未验证/未开放 |
| `RUNTIME_ARGUMENT_INVALID` | 已实现 | REST/工具 | 422/— | rejected | no | 服务参数无效 |
| `RUNTIME_START_FAILED` / `RUNTIME_START_CANCELLED` | 已实现 | 工具 | — | failed/cancelled | conditional | 启动失败或在移交前取消 |
| `RUNTIME_READY_TIMEOUT` | 已实现 | 工具 | — | timeout | conditional | 尚未取得就绪证据即到期，必须收口进程 |
| `RUNTIME_LISTENER_MISMATCH` / `RUNTIME_LISTENER_UNVERIFIED` | 已实现 | 工具 | — | rejected | conditional | 监听不符合声明或无法证实其归属 |
| `RUNTIME_STATE_UNAVAILABLE` / `RUNTIME_LOG_UNAVAILABLE` | 已实现 | 工具 | — | failed | conditional | 状态/日志保存失败，仍需回收且不伪造结果 |
| `RUNTIME_AUDIT_UNAVAILABLE` | 已实现 | 运行实例清理 | 409 | rejected | conditional | 回收审计记录不可用；保留失败门槛，不伪造完成 |
| `WORLD_OPERATION_REQUIRES_MANAGED` | 已实现 | REST | 409 | rejected | no | 当前操作需要物理 World 模式 |
| `WORLD_OPERATION_IN_PROGRESS` | 已实现 | REST | 409 | rejected | conditional | World 已接受切换，不允许覆盖或继续备份 |
| `WORLD_OPERATION_FAILED` | 已实现 | REST | 503 | failed | conditional | World 导出/控制文件操作失败；不回显磁盘路径或异常原文 |
| `WORLD_ACTIVE` | 已实现 | REST | 409 | rejected | conditional | 目标世界仍被后端使用，不能直接切入或破坏其租约 |
| `HISTORY_CURSOR_EXPIRED` | 已实现 | REST | 409 | rejected | conditional | epoch 已改变；作废缓存后从最近窗口重新加载 |
| `REQUEST_FAILED` | 已实现（兜底） | REST/日志 | 通常 500 | failed | conditional | 无法从异常 detail 提取稳定码时的最后兜底，不应用于已知业务分支 |
| `AUTH_REQUIRED` | 已实现 | REST | 401 | rejected | conditional | 缺少 Bearer 凭据 |
| `AUTH_INVALID` | 已实现 | REST/WS | 401/— | rejected | conditional | 登录凭据、旧密码或 Token 无效；持久 WS 对过期/撤销统一拒绝并关闭 |
| `AUTH_REVOKED` | 已实现 | REST | 401 | rejected | conditional | Token 版本与用户当前版本不一致，旧 Token 已失效 |
| `OWNER_REQUIRED` | 已实现 | REST | 403 | rejected | conditional | 操作仅允许当前世界 Owner |
| `PASSWORD_RESET_REQUIRED` | 已实现 | REST | 403 | rejected | conditional | 当前 Token 被标记为必须先修改弱密码 |
| `PASSWORD_POLICY_VIOLATION` | 已实现 | REST | 422 | rejected | conditional | 新密码不满足统一策略；details 列出安全原因 |
| `PASSWORD_UNCHANGED` | 已实现 | REST | 409 | rejected | conditional | 新密码与当前密码相同，不能完成强制重置 |
| `USERNAME_TAKEN` | 已实现 | REST | 409 | rejected | conditional | 用户名已存在 |
| `REGISTRATION_CONFLICT` | 已实现 | REST | 409 | failed | yes | 并发注册提交发生完整性冲突，可重新读取状态后有限重试 |

认证领域的 endpoint 适用范围见 [REST 认证、密码策略与强制重置](public/rest/auth.md)。

### 客户端连接与历史加载诊断

以下代码属于客户端状态，不作为后端 HTTP 错误返回：HISTORY_LOAD_TIMEOUT 表示本次历史请求超时；
WS_PROTOCOL_UNSUPPORTED 表示认证响应未声明所需订阅控制能力；WS_CONNECTION_FAILED 表示物理连接失败；
WS_SYNC_TIMEOUT 表示未按期收到当前订阅同步完成确认；WS_SYNC_FAILED 表示恢复水位或事件序列无法收敛。
均不得当成成功状态；前端提供明确重试入口，协议不兼容必须更新后端。正常切换不产生这些错误。

## 六、模型配置与角色错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `MODEL_CONFIG_NOT_FOUND` | 已实现 | REST | 404/422 | rejected | conditional | 模型配置不存在、不可见，或不属于当前 Owner；具体 HTTP 由调用接口决定 |
| `MODEL_CONFIG_IN_USE` | 已实现 | REST | 409 | rejected | conditional | 模型配置仍被角色引用，删除被外键约束拒绝 |
| `ROLE_NOT_FOUND` | 已实现 | REST | 404 | rejected | conditional | 角色不存在、已墓碑或不属于请求 Owner |
| `ROLE_NOT_AVAILABLE` | 已实现 | REST | 422 | rejected | conditional | 创建会话引用了不存在、停用、墓碑或非当前 Owner 的角色 |
| `ROLE_REQUIRED` | 已实现 | REST | 422 | rejected | conditional | 创建会话没有提供任何角色 |
| `SINGLE_CHAT_REQUIRES_ONE_ROLE` | 已实现 | REST | 422 | rejected | conditional | 单聊必须且只能包含一个角色 |
| `GROUP_CHAT_REQUIRES_MULTIPLE_ROLES` | 已实现 | REST | 422 | rejected | conditional | 群聊创建或成员更新后少于两个角色 |
| `ORCHESTRATOR_MUST_BE_MEMBER` | 已实现 | REST | 422 | rejected | conditional | 编排角色不在会话角色成员中 |

领域适用范围见 [角色管理与墓碑](public/rest/roles.md) 和
[会话管理与回收站](public/rest/conversations.md)。

## 七、会话、消息与 Artifact 错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `CONVERSATION_NOT_FOUND` | 已实现 | REST/WS | 404/— | rejected | conditional | 会话不存在、已进回收站或请求者不是成员；不得区分三种情况 |
| `CONVERSATION_HAS_NO_ROLE` | 已实现 | REST/WS/生成 | 422/— | rejected | conditional | 会话没有存活且启用的可回复角色；墓碑会话只读 |
| `GROUP_CHAT_REQUIRED` | 已实现 | REST | 422 | rejected | conditional | 群聊成员管理端点被用于单聊 |
| `CONVERSATION_REVISION_CONFLICT` | 已实现 | REST | 409 | rejected | yes | 群聊成员更新的 expected revision 已过期，应刷新后重试 |
| `CHAIN_LIMIT_EXCEEDED` | 已实现 | REST | 422 | rejected | conditional | 一条 mentions chain 展开后超过 20 个角色 |
| `TEXT_PART_REQUIRED` | 已实现 | REST | 422 | rejected | conditional | 当前消息发送接口要求至少一个非空 text part |
| `ARTIFACT_NOT_FOUND` | 已实现（读取原型） | REST | 404 | rejected | conditional | Artifact、版本不存在或请求者不是会话成员；不得泄露差异 |
| `SINGLE_CHAT_REQUIRED` | 已实现 | REST | 422 | rejected | conditional | 工作区绑定或工具能力只允许 single 会话，群聊不得提交预留字段 |

`CONVERSATION_HAS_NO_ROLE` 在消息落库前的 REST 拒绝和生成期防御检查中复用同一语义。
消息与 WS 适用范围见 [消息协议](public/messaging/messages.md) 与
[WebSocket 会话事件流](public/websocket/conversation-stream.md)。

## 八、当前 World 工作区错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `WORKSPACE_NOT_FOUND` | 已实现 | REST | 404 | rejected | conditional | 工作区不存在或不属于当前 Owner；不得泄露差异 |
| `WORKSPACE_ROOT_PATH_INVALID` | 已实现 | REST | 422 | rejected | conditional | Owner 提交的 Workspace 根不是合法绝对路径，或包含未展开变量/glob |
| `WORKSPACE_ROOT_NOT_AVAILABLE` | 已实现 | REST/工具 | 409/— | rejected | conditional | 已登记的规范绝对根不存在、类型错误或当前不可访问 |
| `WORKSPACE_PATH_INVALID` | 已实现 | REST/工具 | 422/— | rejected | conditional | 路径不是允许的规范 UTF-8 相对路径或超过固定边界 |
| `WORKSPACE_PATH_OUTSIDE_ROOT` | 已实现 | 工具 | — | rejected | no | 工具参数 canonicalize 后越出本 execution 的 Workspace 根 |
| `WORKSPACE_PATH_SENSITIVE` | 已实现 | REST/工具 | 422/— | rejected | no | 路径命中系统敏感文件或目录拒绝集 |
| `WORKSPACE_DIRECTORY_NOT_FOUND` | 已实现 | REST | 404 | rejected | conditional | 登记既有目录时目标不存在或不是目录 |
| `WORKSPACE_DIRECTORY_EXISTS` | 已实现 | REST | 409 | rejected | conditional | 请求创建空目录时精确目标已经存在 |
| `WORKSPACE_EXISTING_CONTENT_ACK_REQUIRED` | 已实现 | REST | 422 | rejected | conditional | 登记既有目录前未确认文件内容可能发送给模型 |
| `WORKSPACE_NAME_CONFLICT` | 已实现 | REST | 409 | rejected | conditional | 当前 Owner 已有同名工作区 |
| `WORKSPACE_PATH_CONFLICT` | 已实现 | REST | 409 | rejected | conditional | 当前 World 已登记相同 canonical Workspace 根 |
| `WORKSPACE_UNAVAILABLE` | 已实现 | REST/工具 | 409/— | rejected | conditional | 工作区被禁用、目录复核失败或当前状态不可租用 |
| `WORKSPACE_SERVICE_ACTIVE` | 已实现 | 工具 | — | rejected/failed | conditional | 同工作区有尚未结束服务，当前写入项未执行，先协调停服 |
| `WORKSPACE_SERVICE_STOPPING` | 已实现 | 工具 | — | rejected/failed | conditional | 服务正在停止/回收，不能按请求已发出或固定等待时长视为完成 |
| `WORKSPACE_CLEANUP_REQUIRED` | 已实现 | 工具 | — | rejected/failed | conditional | 有回收未确认服务或匹配范围的失败清理操作，优先核查回收证明 |
| `WORKSPACE_SCOPE_CLOSING` | 已实现 | 工具 | — | rejected/failed | conditional | World 关闭门槛阻止当前写入项 |
| `WORKSPACE_SCOPE_CLEANUP` | 已实现 | 工具 | — | rejected/failed | conditional | 匹配范围的清理正在进行，当前写入项未执行 |
| `WORKSPACE_TOOL_CAPABILITY_CHANGED` | 已实现 | 工具 | — | rejected/failed | conditional | 身份/归属有效，但角色工具权限或工作区能力开关已变化 |
| `WORKSPACE_BINDING_CHANGED` | 已实现 | 工具 | — | rejected/failed | conditional | 会话绑定或根与当前执行快照不一致，不使用旧快照写入 |
| `WORKSPACE_LEASE_UNAVAILABLE` | 已实现 | 工具 | — | rejected/failed | conditional | 当前执行租用缺失/失效或租用快照已不匹配 |
| `WORKSPACE_BUSY` | 已实现 | REST/工具 | 409/— | rejected | yes | 同一 managed directory 已被另一个写 execution 租用 |
| `WORKSPACE_FILE_NOT_FOUND` | 已实现 | 工具 | — | rejected | conditional | 目标普通文件不存在 |
| `WORKSPACE_FILE_NOT_TEXT` | 已实现 | 工具 | — | rejected | no | 文件不是合法 UTF-8 普通文本或目标类型不支持 |
| `WORKSPACE_FILE_TOO_LARGE` | 已实现 | 工具 | — | rejected | conditional | 文件或待写内容超过 W1a 固定 1 MiB 上限 |
| `WORKSPACE_FILE_REVISION_CONFLICT` | 已实现 | 工具 | — | rejected | yes | 目标已存在但未提供匹配 hash，或并发更新后 hash 已变化 |
| `WORKSPACE_EDIT_ARGUMENT_INVALID` | 已实现 | 工具 | — | rejected | conditional | edit 缺少/非法参数、空 old_text、超字符护栏或额外参数；不回显片段 |
| `WORKSPACE_BATCH_ARGUMENT_INVALID` | 已实现 | 工具 | — | rejected | conditional | 批次形态、数量、字段或类型非法；不回显原文 |
| `WORKSPACE_READ_ARGUMENT_INVALID` | 已实现 | 工具 | — | rejected | conditional | 统一读取缺失参数、非法字段或混用 items 与顶层单文件字段（含 null/默认值） |
| `RUNTIME_QUERY_CURSOR_INVALID` | 已实现 | 工具 | — | rejected | conditional | 服务列表游标形态或所属会话/Owner 不符，不回显游标 |
| `RUNTIME_QUERY_CURSOR_EXPIRED` | 已实现 | 工具 | — | rejected | conditional | 服务列表游标所属运行 epoch 已变化，从首页重新查询 |
| `RUNTIME_QUERY_FAILED` | 已实现 | 工具 | — | failed | conditional | 状态查询数据库/内部故障或返回超预算，不伪装空列表，不公开异常对象 |
| `WORKSPACE_WRITE_ARGUMENT_INVALID` | 已实现 | 工具 | — | rejected | conditional | write 缺少/非法参数、混用 items 与顶层字段；不回显源码 |
| `WORKSPACE_BATCH_TARGET_CONFLICT` | 已实现 | 工具 | — | rejected | conditional | 全批预检发现重复规范目标或硬链接别名，未写入任何项 |
| `WORKSPACE_BATCH_WRITE_UNCONFIRMED` | 已实现 | 工具 | — | failed | conditional | 文件操作结果无法确认，可能已部分/全部落地；先核查，禁止盲目重放 |
| `WORKSPACE_BATCH_PRECHECK_FAILED` | 已实现 | 工具 | — | rejected/failed | conditional | 写前校验或授权发生内部/I/O 故障；逐项状态区分已有成功，异常原文不公开 |
| `WORKSPACE_BATCH_INPUT_TOO_LARGE` | 已实现 | 工具 | — | rejected | conditional | 批次输入、请求读取字节或修改结果元数据预留超预算，缩小批次 |
| `WORKSPACE_BATCH_BUSY` | 已实现 | 工具 | — | rejected/failed | conditional | 批次准入已满或等待原工作区命令锁超时，对应批次/子项未开始；先前提交以逐项结果为准 |
| `WORKSPACE_BATCH_PARTIAL` | 已实现 | 工具 | — | failed | conditional | 部分读取/修改成功；保留逐项结果，修改批次停止后续项，不自动回滚 |
| `WORKSPACE_BATCH_FAILED` | 已实现 | 工具 | — | failed | conditional | 本批没有成功读取；具体原因仅在 Owner/模型逐项结果中 |
| `WORKSPACE_READ_FAILED` | 已实现 | 工具 | — | failed | conditional | 已授权读取遇到 I/O 或内部故障；不公开宿主异常原文 |
| `WORKSPACE_EDIT_INPUT_TOO_LARGE` | 已实现 | 工具 | — | rejected | conditional | old_text 与 new_text 的 UTF-8 字节数合计超过 64 KiB |
| `WORKSPACE_EDIT_MATCH_NOT_FOUND` | 已实现 | 工具 | — | rejected | conditional | 当前匹配版本内找不到精确旧片段；需重新读取并调整片段 |
| `WORKSPACE_EDIT_MATCH_AMBIGUOUS` | 已实现 | 工具 | — | rejected | conditional | 旧片段多处匹配，包含重叠；需增加明确上下文，不自动全部替换 |
| `WORKSPACE_PARENT_NOT_FOUND` | 已实现 | 工具 | — | rejected | conditional | 写入目标的父目录不存在；W1a 不自动创建父目录 |
| `WORKSPACE_TOOL_NOT_AVAILABLE` | 已实现 | 工具/审批 REST | —/409 | rejected | conditional | execution、Owner、角色、会话、绑定或能力的二次授权失败 |
| `COMMAND_NOT_ALLOWED` | 已实现（W1b） | 工具 | — | rejected | no | command 不是登记的稳定 ID |
| `COMMAND_ARGUMENT_INVALID` | 已实现（W1b） | 工具 | — | rejected | conditional | 参数不符合该命令 schema 或含禁止语法 |
| `COMMAND_NOT_SUPPORTED` | 已实现（W1b） | 工具 | — | rejected | conditional | 当前平台无法提供受控命令 adapter |
| `COMMAND_TIMEOUT` | 已实现（W1b） | 工具 | — | timeout | conditional | 命令超过部署超时，进程树已回收 |
| `COMMAND_FAILED` | 已实现（W1b） | 工具 | — | failed | conditional | 受控进程启动失败或非零退出；不暴露宿主异常 |
| `TOOL_DETAILS_NOT_FOUND` | 已实现 | REST | 404 | rejected | no | 消息、会话、调用不匹配或不属于当前 Owner |

适用接口和路径隐藏规则见 [当前 World 工作区](public/rest/workspaces.md)。

## 九、Provider 与生成错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `PROVIDER_RATE_LIMITED` | 已实现 | Agent/WS/数据库/日志 | — | failed | yes | 厂商返回 429 或限流类异常，应退避后有限重试 |
| `PROVIDER_AUTH_FAILED` | 已实现 | Agent/WS/数据库/日志 | — | rejected | conditional | 厂商凭据无效或没有权限，必须修正模型配置 |
| `PROVIDER_BAD_REQUEST` | 已实现 | Agent/WS/数据库/日志 | — | rejected | conditional | 厂商拒绝请求结构或参数，原请求不应原样重试 |
| `PROVIDER_TIMEOUT` | 已实现 | Agent/WS/数据库/日志 | — | timeout | yes | 模型厂商调用超时，可按预算有限重试 |
| `PROVIDER_ERROR` | 已实现（兜底） | Agent/WS/数据库/日志 | — | failed | conditional | 无法映射到已知厂商类型的失败；需先检查错误类型再决定重试 |
| `CONTEXT_BUDGET_EXCEEDED` | 已实现 | WS/数据库/日志 | — | rejected | conditional | 可裁剪历史全部移除后，必要规则、当前可见工具、当前消息与输出预留仍超过角色有效窗口；不调用 Provider |
| `EXECUTION_INTERRUPTED` | 已实现（内部） | 数据库/日志 | — | cancelled | conditional | 服务重启前 execution 未到终态；不自动重放 Provider，用户可重新发起任务 |

Provider 映射条件的权威说明见 [Agent 运行时](internal/agent-runtime.md)。原始厂商错误不得回显给客户端，
因为异常文本可能包含打码 Key、URL query 或 SDK 请求信息。

## 十、世界错误码

| 错误码 | 状态 | 传输 | HTTP | 终态 | 重试 | 含义 |
|---|---|---|---:|---|---|---|
| `WORLD_NOT_FOUND` | 已实现 | REST | 404 | rejected | conditional | 目标世界不存在或元数据无效 |
| `WORLD_ALREADY_ACTIVE` | 已实现 | REST | 409 | rejected | no | 目标就是当前世界，无需重启 |
| `WORLD_SWITCH_REQUIRES_WRAPPER` | 已实现 | REST | 409 | rejected | conditional | 后端不是由世界包装器启动，不能安全退出并重启 |
| `WORLD_REQUIRES_NEWER_ROLEPLEX` | 部分实现 | startup | — | rejected | conditional | 世界格式或 Alembic revision 更新；当前已有可读阻断异常，但尚未输出独立机器码字段 |

世界目录、包装器和启动兼容规则见 [世界存档与切换](public/rest/worlds.md)。

## 十一、预留错误码

以下名称存在于总体协议或后续里程碑计划，但当前没有完整服务端处理和客户端行为，不得当作已实现：

| 错误码 | 目标领域 | 计划语义 | 当前状态 |
|---|---|---|---|
| `FORBIDDEN` | 通用授权 | 已认证但缺少某项资源权限的通用拒绝 | 预留；当前优先使用领域 404 或 `OWNER_REQUIRED` |
| `INVITE_INVALID` | 邀请 | 邀请不存在、过期、撤销或用尽，且不泄露具体原因 | 预留（M6） |
| `ARTIFACT_VERSION_CONFLICT` | Artifact | 更新时 expected_version 与当前版本不一致 | 预留（M3） |

新增预留项不能仅靠计划文本进入“已实现”表，必须等路由、事件、客户端降级和测试全部存在。

## 十二、日志 v2 使用规则

日志设计稿见 [日志目录与字段规范 v2](../design/logging-v2.md)。日志中的 `error_code`：

1. 必须来自本文已实现表，或来自未来登记为 internal 的稳定码。
2. 必须保持与 REST/WS/数据库终态相同的大写值，不另造小写别名。
3. 正常取消、用户停止、策略拒绝可以用 `status`/`reason` 表达，不应为了“字段非空”伪造 error_code。
4. 未知异常只记录 `error_type`、脱敏 message 和 traceback；确认稳定语义后再登记新码。
5. `errors.jsonl` 是原事件副本，保持同一 `event_id` 和 error_code。

建议 status 示例：

```json
{"event":"generation.completed","status":"success"}
{"event":"generation.failed","status":"failed","error_code":"PROVIDER_ERROR"}
{"event":"generation.cancelled","status":"cancelled","reason":"user_stop"}
{"event":"provider.call_failed","status":"timeout","error_code":"PROVIDER_TIMEOUT"}
```

## 十三、代码集中化要求（待实现）

当前错误码仍以字符串分散在 router、security、Agent 和服务层。后续实现应增加单一代码注册入口，例如：

```python
from enum import StrEnum

class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
```

迁移要求：

1. 先用源码扫描测试收集现有字符串，与本文“已实现”集合比对。
2. 再逐模块替换为常量，不在同一改动中改变 HTTP 状态或公开 payload。
3. 新错误码必须有领域测试，证明触发条件、状态码、信息隐藏与重试语义。
4. CI/pytest 增加注册表一致性检查，防止未登记字符串重新进入代码。

## 十四、当前已知缺口

1. 尚无代码级 `ErrorCode`/registry，本文先统一协议语义。
2. `MODEL_CONFIG_NOT_FOUND` 在不同 endpoint 使用 404/422；这是现状，是否统一需独立兼容评审。
3. `WORLD_REQUIRES_NEWER_ROLEPLEX` 只有可读 startup 异常，缺少结构化机器码输出。
4. `REQUEST_FAILED` 是宽泛兜底；已知业务分支继续使用它应视为缺陷。
5. 部分领域文档仍复制错误码含义，后续应改为“适用范围 + 本文链接”，避免双重权威。
