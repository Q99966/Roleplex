# Roleplex 接口协议索引与总则

本文档是 Roleplex 协议文档体系的稳定入口，维护协议范围、通用约定、当前实现状态和领域索引。项目规模增长后，具体 REST、WebSocket、消息、资源和内部协议应拆分到 `docs/protocol/` 对应领域目录，不应继续全部堆叠在本文件中。

复核日期：2026-09-22。具体协议按领域维护；本文只保留总则、状态和入口。历史计划见[阶段索引](plan/README.md)，不作为现行接口约束。

> API 已实现表示存在对应服务端处理与验证；完整产品能力还需对应客户端行为。仅有 schema、数据表或风险验证的内容单独标为原型/预留。

## 文档状态与拆分规则

协议领域文档使用以下状态：

- **已实现**：对应处理逻辑及测试已存在；若缺少产品入口，应在领域文档说明。
- **原型**：存在部分代码或风险验证，但尚未形成完整可用链路。
- **预留**：只有 schema、计划或协议设计，当前不可调用。
- **废弃**：不再建议使用，并提供替代方案或迁移说明。

协议目录按领域组织：

```text
docs/protocol/
├── public/       # 客户端可依赖的 REST、消息、WebSocket 和资源协议
└── internal/     # Agent、MCP、调度、领域事件和审计等内部约定
```

领域文档标明受众、状态、适用版本、事实来源与复核日期，并链接相关实现和测试；仅说明本领域适用的鉴权、并发、错误和降级规则，不机械复制无关模板。协议与厂商验证入口在 [docs/testing/](testing/README.md)，不维护空的 `protocol/testing/` 目录。公开协议的兼容性按下文处理，内部约定不自动成为客户端可依赖的 wire contract。

## 当前领域索引

| 领域 | 当前状态 | 权威位置 |
|---|---|---|
| REST 认证、密码策略与强制重置 | 已实现 | [public/rest/auth.md](protocol/public/rest/auth.md) |
| REST 角色管理与墓碑 | 已实现 | [public/rest/roles.md](protocol/public/rest/roles.md) |
| 会话压缩、自动策略及版本回退 | 已实现；Owner 配置，自动维护共用原任务预算 | [public/rest/context-compression.md](protocol/public/rest/context-compression.md) |
| 主动历史检索与原文回读 | 已实现，共享范围与执行授权交集 | [public/rest/memory.md](protocol/public/rest/memory.md) |
| 持久会话上下文与按角色输入占用 | 已实现，Owner 查看共享来源及调用级估算 | [public/rest/conversation-context.md](protocol/public/rest/conversation-context.md) |
| 平台/世界/会话提示词与来源预览 | 已实现，仅 Owner 配置和查看 | [public/rest/prompt-settings.md](protocol/public/rest/prompt-settings.md) |
| REST 会话工作流 | 已实现独立规划、图读写/编辑、并行循环、运行修订与节点控制 | [public/rest/workflows.md](protocol/public/rest/workflows.md) |
| REST 会话管理与回收站 | 已实现（单聊与串行群聊） | [public/rest/conversations.md](protocol/public/rest/conversations.md) |
| REST 世界存档与切换 | 已实现 | [public/rest/worlds.md](protocol/public/rest/worlds.md) |
| REST 当前 World 工作区 | 已实现（Owner 单聊及群聊文件工具；含批量修改与多片段编辑） | [public/rest/workspaces.md](protocol/public/rest/workspaces.md) |
| 工作区搜索与范围读取 | 内部/已实现 | [internal/workspace-search-read.md](protocol/internal/workspace-search-read.md) |
| 工作区结构化命令 | 内部/已实现 | [internal/workspace-commands.md](protocol/internal/workspace-commands.md) |
| 消息发送、历史与停止生成 | 已实现（含历史窗口与多行输入） | [public/messaging/messages.md](protocol/public/messaging/messages.md) |
| 工具执行顺序与 Owner 详情 | 已实现（含 diff、批次结果和拒绝诊断） | [public/messaging/tool-details.md](protocol/public/messaging/tool-details.md) |
| 旧系统执行摘要 | 废弃，仅旧记录兼容；停止原因见消息协议 | [public/messaging/execution-summary.md](protocol/public/messaging/execution-summary.md) |
| Shell 逐次审批 | 已实现；逐次审批 | [public/messaging/shell-approvals.md](protocol/public/messaging/shell-approvals.md) |
| 会话运行实例、服务与回收 | 已实现（后台服务仅 Linux） | [public/messaging/runtime-services.md](protocol/public/messaging/runtime-services.md) |
| WebSocket 连接、订阅与恢复 | 已实现（含登录会话级连接与最近窗口快照） | [public/websocket/conversation-stream.md](protocol/public/websocket/conversation-stream.md) |
| 邀请兑换 | 预留，无可调用产品接口 | [尚未实现的范围](#尚未实现的范围) |
| Artifact 原始内容读取与 iframe 隔离 | 原型 | [public/resources/artifact-raw.md](protocol/public/resources/artifact-raw.md) |
| Artifact 创建与版本更新 | 预留，无可调用产品接口 | [尚未实现的范围](#尚未实现的范围) |
| 其他列表分页与兼容性 | 未统一；各已实现领域自行定义 | [尚未实现的范围](#尚未实现的范围) |
| 数据模型（表与字段） | 内部 | [internal/data-model.md](protocol/internal/data-model.md) |
| Agent 运行时（领域事件、E0 execution、工具安全、MCP） | 内部/部分已实现 | [internal/agent-runtime.md](protocol/internal/agent-runtime.md) |
| 角色执行用量 | 已实现，仅 Owner 观测 | [public/rest/execution-usage.md](protocol/public/rest/execution-usage.md) |
| World Agent 默认预算 | 已实现，v2 自定义与不限模式 | [public/rest/agent-budget.md](protocol/public/rest/agent-budget.md) |
| 中断执行事实交接 | 内部/已实现 | [internal/interruption-context.md](protocol/internal/interruption-context.md) |
| Agent 执行预算与计数 | 内部/已实现；无统一时间截止 | [internal/agent-budget.md](protocol/internal/agent-budget.md) |
| 执行事实与可信收尾 | 内部/已实现 | [internal/execution-facts.md](protocol/internal/execution-facts.md) |
| 日志、请求关联与异步链路观测 | 内部/已实现 | [internal/observability.md](protocol/internal/observability.md) |

## 认证

REST 使用 `Authorization: Bearer <访问令牌>`，WebSocket 通过首帧传递令牌，不得放入查询参数。令牌有效期七天；Token 版本变化后旧令牌立即失效。

密码策略、弱口令强制重置的拦截规则、改密接口和认证错误码的权威文档见 [public/rest/auth.md](protocol/public/rest/auth.md)。WebSocket 首帧的帧格式见 [WebSocket 会话事件流](protocol/public/websocket/conversation-stream.md)。

## 错误信封

错误码名称、含义、终态和重试语义的权威注册表见 [错误码设计与注册表](protocol/error-codes.md)。
本节只保留公开信封和 HTTP 分类总则，各领域接口返回范围见对应领域文档。

所有错误使用稳定的机器可读错误码：

```json
{"error":{"code":"CONVERSATION_NOT_FOUND","message":"会话不存在","request_id":"..."}}
```

状态码约定：

- `401`：`AUTH_REQUIRED`、`AUTH_INVALID`、`AUTH_REVOKED`
- `403`：`OWNER_REQUIRED`、`PASSWORD_RESET_REQUIRED`；`FORBIDDEN` 为后续通用授权预留码，当前未使用
- `404`：资源不存在或请求者无权访问；不得通过响应泄露资源是否存在
- `409`：重复、幂等冲突或版本冲突
- `422`：请求参数无效；策略类拒绝使用各自的稳定错误码而不是笼统的 `VALIDATION_ERROR`
- `429`：配额或速率限制

错误信封在 `code`、`message`、`request_id` 之外允许附带 `details`，用于逐条说明校验或策略未通过的原因。
服务端还会在所有 HTTP 响应返回同值的 `X-Request-ID`；调用方可以传入该请求头以复用自己的关联 ID，
未传时由服务端生成。

现有 REST 接口使用该错误信封；新接口沿用同一格式，具体码值在领域文档和错误码注册表同步。

## 消息

会话历史读取、用户消息发送（含 `client_message_id` 幂等键）和停止生成已实现，权威文档见 [public/messaging/messages.md](protocol/public/messaging/messages.md)。流式增量和终态不通过 REST 返回，客户端必须订阅 WebSocket 事件流。

群聊创建、成员管理和 `@` 串行调度已实现；群协调工作流通过独立入口规划、编辑和运行图，见[工作流协议](protocol/public/rest/workflows.md)，不改变普通消息路由。图管理权限只属于明确协调请求；附件和 Artifact part 尚未实现。

## WebSocket 订阅恢复

首帧认证、按 `after_event_seq` 的 backlog 回放、epoch 变化或超出回放上限时回落完整快照均已实现，权威文档见 [public/websocket/conversation-stream.md](protocol/public/websocket/conversation-stream.md)。

事件的可靠恢复来源是持久化事件日志：事件先落库再广播，内存广播只服务在线订阅者。客户端必须按 `event_seq` 与 `(message_id, revision, delta_seq)` 幂等应用事件，并对未知事件类型提供降级行为。

## 尚未实现的范围

- 邀请数据模型已预留，但邀请创建、兑换和 Guest 入群路由未实现。旧候选路径/字段不构成可调用接口或冻结设计；实施时按当前成员授权和原子兑换需求定稿。
- Artifact 数据表与原始内容读取/iframe 隔离原型已存在，创建、更新和前端预览未接入。现有原型见[原始内容协议](protocol/public/resources/artifact-raw.md)。
- 消息窗口、工作区/服务查询已各有自己的分页契约；角色、会话等列表尚未统一分页，不承诺全站固定游标格式。

原型与预留状态不产生当前客户端兼容承诺；新增产品链路时再更新领域协议、实现、测试和本索引。

## 兼容性与同步

当前 REST 使用 `/api` 前缀，没有统一的 `/api/v1` 或全局 payload 版本字段；WS 通过 `auth_ok.capabilities` 声明订阅/历史窗口能力。领域文档中的“协议版本”是该领域的契约修订号，只有明确列出的 `version` 字段才是 wire 字段，不能把文档版本当作请求参数。

已实现接口优先兼容新增字段与事件；客户端对未知事件忽略或按领域规则降级，对未知消息 part 显示占位。改变现有字段语义时说明兼容影响，按实际需要选择领域版本、迁移或前后端同步部署；例如 nullable 决策预算要求配套前端，见[预算 v2](protocol/public/rest/agent-budget.md)。不要求每次新增兼容字段都复制整套协议或新增版本路径。

修改协议时核对路由、schema、客户端类型、错误码和相关测试，在同一变更中同步权威领域文档；仅在索引/状态变化时修改本入口。示例只用占位数据，不包含真实凭据、用户隐私或危险执行内容。
