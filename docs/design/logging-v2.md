# Roleplex 日志目录与字段规范 v2

| 元数据 | 值 |
|---|---|
| 受众 | 内部开发、测试与本机运维 |
| 状态 | 已批准；已实现 |
| 设计版本 | 2 |
| 当前实现 | [日志、请求关联与异步链路观测](../protocol/internal/observability.md) |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-08 |

本文定义并约束当前日志 v2 的目录、文件职责、轮转方式和字段结构；现状摘要与事实来源见
`docs/protocol/internal/observability.md`。该版本已于 2026-08-27 经用户人工验收，后续兼容变更必须同步
更新本文、实现与回归测试。

## 一、已确认的设计原则

连接解耦阶段增加 `ws.unsubscribed`，仅在活动订阅实际释放时记录，沿用 ws_connection_id、conversation_id。
同步完成继续对应既有 ws.subscribed 事实，不另记重复 ready 事件；subscription_id 仅作 wire 控制标识，
不成为另一套 Trace。认证/成员拒绝复用现有事件，不记录 Token 或控制帧原文。

工具执行详情使用独立 World 加密业务表和 Owner 接口，不属于机器日志。领域事件中的 private_input/
private_output 禁止直接序列化进入任何日志、审计摘要或失败报告；详情 GET 只记录既有 HTTP 访问元数据，
不记录响应正文。工具过程的开始/完成事件继续使用现有目录，具体内容边界见
[工具执行详情](../protocol/public/messaging/tool-details.md)。

W1b 命令复用 tool.call_started/completed，不新增重复事实或逐输出日志。安全参数摘要只保留登记的
command ID；进程结果通过内部领域事件与工具卡传递有界安全元数据，禁止原始 stdout/stderr/cwd。
详细字段见 [结构化命令契约](../protocol/internal/workspace-commands.md)。

1. runtime 按本地自然日聚合，不因后端重启、世界切换或异常恢复创建新目录。
2. 每个后端进程使用 `process_instance_id` 和生命周期事件区分，不靠文件名猜测重启边界。
3. pytest 不持久化全部成功测试的应用日志；每轮只追加一条 summary，失败测试才单独保存详情。
4. E2E 每轮使用独立目录，因为它拥有独立后端、数据库/世界、浏览器产物和 provider 模式。
5. fake E2E 与 real E2E 物理分开；real 测试仍禁止 trace/video 默认持久化完整模型输出。
6. 所有机器日志使用 UTF-8 JSON/JSONL；access 也使用 JSONL，不保留自由文本 `.log`。
7. 密码、API Key、访问 Token、Authorization、完整用户输入、完整模型输出和 MCP 敏感参数永不落盘。
8. 每条事件拥有唯一 `event_id` 和进程内单调 `process_seq`；errors 副本保持同一身份。
9. category 只表达事实所属的 app/agent/access，不把“错误”当成第四种业务类别。
10. 可选关联字段无值时省略；核心身份字段和 `world_name` 始终存在。

## 二、目录结构

```text
logs/
│
├── runtime/
│   ├── 2026-08-25/
│   │   ├── app.jsonl
│   │   ├── app.001.jsonl
│   │   ├── agent.jsonl
│   │   ├── agent.001.jsonl
│   │   ├── errors.jsonl
│   │   ├── errors.001.jsonl
│   │   ├── access.jsonl
│   │   └── access.001.jsonl
│   └── 2026-08-26/
│
├── tests/
│   ├── unit/
│   │   ├── 2026-08-25/
│   │   │   ├── summary.jsonl
│   │   │   └── failures/
│   │   │       ├── 10-15-21_c8127a4f/
│   │   │       │   ├── world_backup.json
│   │   │       │   └── owner_bootstrap.json
│   │   │       └── 16-42-08_a31f09b2/
│   │   │           ├── provider_streaming_a81f3c2d.json
│   │   │           └── provider_streaming_c02814a1.json
│   │   └── 2026-08-26/
│   │       └── summary.jsonl
│   │
│   └── e2e/
│       ├── fake/
│       │   └── 2026-08-25/
│       │       └── 10-01-21_a8137c2f/
│       │           ├── events.jsonl
│       │           ├── errors.jsonl
│       │           ├── summary.json
│       │           ├── artifacts.json
│       │           └── artifacts/
│       │               ├── screenshots/
│       │               └── diagnostics/
│       └── real/
│           └── 2026-08-25/
│               └── 10-02-49_8d3d6e21/
│                   ├── events.jsonl
│                   ├── errors.jsonl
│                   ├── summary.json
│                   ├── artifacts.json
│                   └── artifacts/
│                       └── screenshots/
│
└── archive/
    └── 2026-08/
        ├── 2026-08-24_runtime.tar.gz
        ├── 2026-08-24_unit-tests.tar.gz
        ├── 2026-08-24_e2e-fake.tar.gz
        └── 2026-08-24_e2e-real.tar.gz
```

日期目录和 JSONL `timestamp` 统一使用北京时间 `Asia/Shanghai`（UTC+08:00）的 ISO 8601。Roleplex 是
Owner 本机应用，固定口径避免宿主系统时区、UTC 事件和目录日期互相错位；事件身份和顺序仍以
`event_id/process_seq` 为准，时间不能替代因果关系。测试 summary 同样保存明确时区偏移。

## 三、标识符和命名

### 3.1 进程与测试运行标识

- `event_id`：每条事件生成 `evt_` + UUID4 hex；用于副本去重、索引和未来导入，不承担排序。
- `process_instance_id`：每个后端进程启动时生成随机 8 位十六进制标识；世界切换重启后变化。
- `process_seq`：同一 process instance 中从 1 开始单调递增，在 LogRecord 统一入口分配；表示日志观察
  顺序，不宣称多个异步任务具有物理绝对顺序。
- `run_id`：每轮 pytest 或 Playwright 固定使用随机 8 位十六进制标识。
- `nodeid_hash`：完整 pytest nodeid 的 SHA-256 前 8 位，只用于区分重名和参数化实例，不是 Git ID。

同一事件进入 errors 副本时，`event_id`、`process_instance_id`、`process_seq` 和 category 全部保持不变。
不同分类文件合并时按 `(process_instance_id, process_seq)` 恢复该进程的观察顺序；跨进程不伪造全局序号。

### 3.2 源码状态元数据

Git 信息不属于每条事件的公共字段。runtime 只在 `process.started` 记录一次，pytest/Playwright 只在
summary 记录一次：

```json
{
  "backend_version": "0.1.0",
  "git_available": true,
  "git_head": "da1eeb1",
  "git_dirty": true,
  "working_tree_hash": "4f20e8c1"
}
```

干净工作区不计算 working-tree hash，保存 `null`；dirty 时 hash 必须覆盖 staged、unstaged 和 untracked
源码状态，不能只计算 `git diff HEAD`。hash 只能识别状态是否相同，不能恢复未提交代码。分发版没有
`.git` 时保存 `git_available=false` 和应用版本，不能因此阻断启动或测试。

### 3.3 测试失败目录

目录名：

```text
HH-MM-SS_<run_id>
```

例如 `10-15-21_c8127a4f`。时间用于人工定位，8 位 run ID 用于区分同一秒启动或并行的测试轮次，并与
`summary.jsonl` 关联。

### 3.4 单元测试失败文件

默认文件名取测试函数名并去掉开头 `test_`：

```text
world_backup.json
owner_bootstrap.json
```

如果两个 nodeid 得到相同可读名称，或测试是参数化测试，则追加 `nodeid_hash`：

```text
provider_streaming_a81f3c2d.json
provider_streaming_c02814a1.json
```

文件名只允许字母、数字、`_`、`-`，可读部分最长 80 个字符。raw nodeid、原始参数、参数 repr 和 pytest
自动生成的参数 ID 均不得持久化；完整 nodeid 只在内存中用于计算 nodeid hash。

参数实例只有显式声明为安全日志 case ID 时才保存，例如项目自定义
`@pytest.mark.log_case("deepseek")`。没有安全声明时 `parameter_ids` 省略，仅靠 nodeid hash 区分；
宁可降低可读性，也不自动 dump 参数。

## 四、runtime 文件规范

### 4.1 写入与进程重启

同一天的所有后端进程追加到当天文件。进程启动和停止分别写：

```json
{"event":"process.started","process_instance_id":"a813f021","process_seq":1,"pid":12345,"world_name":"default"}
{"event":"process.stopped","process_instance_id":"a813f021","process_seq":927,"reason":"world_switch"}
```

`reason` 允许：`normal`、`world_switch`、`keyboard_interrupt`、`service_stop`、`wrapper_shutdown`、`wrapper_lost`。进程崩溃时可能没有
`process.stopped`，这是异常退出的判断依据，不补写伪造终态。

当前部署约束仍是单进程单 worker。若以后允许多 worker，必须先增加跨进程文件锁或集中日志消费者，
不能假设多个 handler 同时轮转安全。

### 4.2 文件职责

| 文件 | 内容 | 是否与其他文件重复 |
|---|---|---|
| `app.jsonl` | 配置、启动、认证、世界、数据库、REST 业务、会话和 WebSocket 事件 | 否 |
| `agent.jsonl` | generation、provider、工具、模型耗时和 token usage | 否 |
| `access.jsonl` | HTTP 请求完成/失败、方法、路由、状态码和耗时 | 否 |
| `errors.jsonl` | app/agent/access 中 ERROR、CRITICAL、未处理异常的原样索引副本 | 是，原记录仍留在所属文件 |

默认 Uvicorn access 与自定义 HTTP middleware 日志不得重复持久化。`access.jsonl` 以自定义、带
`request_id` 和 `duration_ms` 的 HTTP 事件为唯一权威；Uvicorn 自由文本 access 可只输出终端或关闭。

### 4.3 轮转

每种文件独立轮转，满足任一条件时切片：

- 当前片段写入后将超过 10 MiB；
- 当前片段首条记录至下一条记录达到一小时；
- 本地日期变化。

活跃片段始终使用基础名，如 `app.jsonl`。轮转时旧片段改名为 `app.001.jsonl`、`app.002.jsonl`，
序号在当日目录内递增。后端重启时读取当前片段首条记录时间和大小：未触发条件则继续追加，不能仅因
重启生成新片段。单条日志理论上不得接近 10 MiB，因为完整输入/输出禁止落盘。

序号语义固定为：`.001` 是当天第一段已关闭文件，`.002` 是第二段，依次递增；基础名永远是当前活跃
片段，即 `001 < 002 < 003 < active`。已关闭片段不可再次打开或写入，可安全压缩、上传或建立索引；
不得采用 `.1` 永远表示最新、旧文件不断后移的 logrotate 语义。

## 五、公共 JSONL 字段

### 5.1 每条事件强制字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | integer | 日志字段版本，固定为 `2` |
| `timestamp` | string | 北京时间 ISO 8601，固定 `+08:00` |
| `level` | string | `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `logger` | string | Python logger 名称 |
| `event_id` | string | `evt_` + UUID4 hex，全局唯一事件身份 |
| `event` | string | 稳定、可检索的事件名 |
| `category` | string | 只允许 `app/agent/access` |
| `run_kind` | string | `runtime/e2e-fake/e2e-real`；unit 不写完整事件流 |
| `process_instance_id` | string | 当前后端进程 8 位随机标识 |
| `process_seq` | integer | 当前进程统一日志入口的观察序号，从 1 递增 |
| `world_name` | string | 当前世界；显式数据库兼容模式也保存展示名 |

`errors.jsonl` 不产生新事件：复制行必须保持这些字段逐项相同，category 不改为 error，也不增加
`source_category`。

### 5.2 进程级字段

`pid`、`backend_version`、`git_available`、`git_head`、`git_dirty`、`working_tree_hash` 只在
`process.started` 记录。其他事件通过 process_instance_id 关联，不重复 source 字段。

### 5.3 可选关联字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `request_id` | string | HTTP 请求关联 ID |
| `user_id` | integer | 已认证用户 ID |
| `conversation_id` | integer | 会话 ID |
| `message_id` | integer | 消息 ID |
| `generation_id` | integer | 生成 ID |
| `chain_id` | string | 消息处理链 ID |
| `execution_id` | string | Agent/工具执行 ID |
| `parent_execution_id` | string | 父执行 ID |
| `role_id` | integer | Agent 角色 ID |
| `tool_call_id` | string/integer | 工具调用标识或审计行 ID |
| `ws_connection_id` | string | WebSocket 连接 ID |

关联字段有值时才输出；无值时省略，消费者按“没有该上下文”处理。禁止伪造 ID，也不为固定 JSON 形状
填充 null。业务字段直接附加在公共字段后，但不得覆盖强制字段。

### 5.4 Trace 模型

Roleplex 不新增第二套 trace/span 标识：

```text
chain_id            ≈ trace_id
execution_id        ≈ span_id
parent_execution_id ≈ parent_span_id
```

未来接 OpenTelemetry 时只把现有字段映射到 OTEL，不同时保留两套业务追踪 ID。process_seq 只负责同一
进程的日志观察顺序，不能替代上述因果关系。

## 六、runtime 各文件字段

### 6.1 事件命名

事件统一使用 `<domain>.<action>`：domain 是稳定领域；action 使用 snake_case，表达已经发生的事实。
禁止同义词漂移。首批稳定目录：

```text
process.started              process.stopped
process.failed
http.completed               http.failed
ws.accepted                  ws.authenticated
ws.subscribed                ws.unsubscribed             ws.disconnected
generation.created           generation.started
generation.completed         generation.failed
generation.cancelled
generation.budget_stopped
generation.skipped           generation.queue_job_completed
generation.queue_job_failed  execution.interrupted
provider.built               provider.call_started
provider.call_completed      provider.call_failed
tool.call_started            tool.call_completed
tool.call_failed
tool.call_not_dispatched
tool.scan_completed
tool.write_wait_completed
tool.approval_requested      tool.approval_resolved
runtime.reserved            runtime.state_changed
runtime.quota_changed       runtime.cleanup_started
runtime.cleanup_target_selected  runtime.cleanup_target_started
runtime.stop_requested      runtime.force_requested
runtime.stop_dispatched
runtime.cleanup_target_completed runtime.cleanup_completed
runtime.cleanup_failed      runtime.recovery_checked
runtime.cleanup_reconciled
context.loaded               context.compressed
log.tail_recovered           log.retention_started
log.archive_created          log.archive_source_removed
log.archive_delete_planned   log.archive_deleted
log.retention_completed      log.retention_failed
log.retention_skipped
```

新增事件必须先登记目录并检查是否已有同义事实；不得并存 `provider.call_completed`、
`provider.completed`、`model.call_done`、`llm.finished` 等多套表达。现有实现迁移到 v2 时需要提供旧→新
事件映射测试，不能静默遗漏监控消费者。

T2 tool.scan_completed 是一次宿主只读调用的扫描阶段记录，沿用 tool_call_id/Trace，包含 queue_wait_ms、
scan_duration_ms、scanned_bytes 和固定 status。不逐文件/逐块刷日志，不记录查询、路径、匹配正文或 hash。
T2 搜索安全输入摘要的 query_bytes 表示 query 或 queries 中全部字符串的 UTF-8 字节数之和；不保存关键词、表达式或路径原文。
仅工具调用作用域产生日志，底层基准测试不伪造调用身份；未取得准入时 scan_duration_ms/scanned_bytes 为 0。

T1 使用 `generation.budget_stopped` 记录图预算停止的唯一终态，status=cancelled、reason=graph_budget；
用户停止仍用 generation.cancelled。原 generation.recursion_limit_reached/tool.calls_unresolved 调试事件
由终态原因取代，不把未配对调用记为预算耗尽。协议异常以 generation.failed/error_code=AGENT_PROTOCOL_ERROR
收口，不伪装为 provider.call_failed。摘要内容不进日志，消息/执行身份与已有终态耗时沿用原字段。

运行回收清单已持久化后，机器审计写失败不得跳过剩余目标；继续回收并把批次记录为失败，阻止尚未提交的后续资源变更。
请求取消同样不能截断冻结清单。无法保存某项结果时保留原待处理记录，后续重试核查，不伪造完成或成功日志。
最终记录失败不回滚已提交资源；批次保留失败原因及后续核查身份，不能把错误响应当作“操作从未发生”的证明。

W1c 审批事件只使用 approval_id、request_digest 和既有 execution/chain/tool_call 关联字段；
requested 不填终态，resolved 以 status=success/rejected/cancelled 和 approval_status=approved/rejected/expired
表达决定，reason 区分 owner_decision/expired/cancelled/restart。脚本只进入加密业务审批记录，不进入日志。

W1d 运行/回收事件使用 runtime_id、cleanup_id、target_ordinal、scope/scope_id、actor_id、target_count、
runtime_state、PID/出生身份以及既有 conversation/workspace/execution/chain/tool_call 关联；不得记录进程对象 repr、
命令行、根路径、脚本或 stdout/stderr。配额审计保留 old_limit/new_limit。回收条目记录动作/验证/耗时/退出结果，
批次汇总引用完整条目及未确认目标，不以发出信号代替退出证明。具体过程见 W1d 计划第六节。
现场/持久回收证明的随机令牌及凭据正文不得进入日志、共享事件或测试报告；日志只记录核查结果与既有运行身份。

### 6.2 status、reason 与 error_code

status 只用于终态或策略决策：`success | failed | cancelled | timeout | rejected`。开始事件不填 status。
正常取消和用户停止使用 `status=cancelled` 加安全 reason（如 `user_stop`），不升级为 ERROR。

`error_code` 必须来自 [错误码注册表](../protocol/error-codes.md)，保持现有大写值；不得为日志另造小写
别名，也不得从异常 message 动态生成错误码。未知异常可以只有 error_type、脱敏 message 和 traceback。

### 6.3 `app.jsonl`

事件按功能附加字段。例如：

| 事件族 | 附加字段 |
|---|---|
| `process.*` | `reason`、`world_managed`、`git_head`、`git_dirty`、`working_tree_hash` |
| `auth.*` | `username`、`reason`、`password_reset_required`；不得含密码/Token |
| `world.*` | `source_world`、`target_world`、`switching_supported` |
| `ws.*` | `client`、`recovery_mode`、`stream_epoch`、`after_event_seq`、`latest_event_seq`、`backlog_count` |
| `message.queued` | `event_seq`、`message_revision`、`stream_epoch` |
| `retention.*` | `retention_days`、`purged_conversations`、`purged_files` |

### 6.4 `agent.jsonl`

现有 provider.call_* 与 provider_call_count 使用框架模型调用口径，SDK 内部 HTTP 重试未逐次计数；
不得将其解释为实际传输次数。T4.1 取证及后续计数边界见[执行预算与计数](../protocol/internal/agent-budget.md)，本次不改变日志 schema。

`provider.call_completed` 必须包含：

| 字段 | 类型 | 含义 |
|---|---|---|
| `provider_call_index` | integer | 本轮第几次框架模型调用，不是逐次 HTTP 尝试 |
| `provider_mode` | string | `fake/real` |
| `provider_type` | string（可选） | `anthropic/openai_compatible` 等 |
| `base_url` | string | 本次实际使用的脱敏 Provider 基址；fake 为 `fake://local` |
| `base_url_source` | string | `configured/default/fake`，说明基址来源 |
| `model` | string | 模型名 |
| `ttft_ms` | number（可选） | 调用开始到首个流式分片 |
| `duration_ms` | number | 本次调用总耗时 |
| `input_tokens` | integer（可选） | 厂商报告的输入 token |
| `output_tokens` | integer（可选） | 厂商报告的输出 token |
| `total_tokens` | integer（可选） | 厂商报告或由已报告输入+输出相加 |
| `cache_hit_tokens` | integer（可选） | 厂商报告的缓存命中 token |
| `cache_write_tokens` | integer（可选） | 厂商报告的缓存写入 token |
| `cache_hit_ratio` | number（可选） | 同次调用报告正数 input 与非负 cache hit 时计算的 `cache_hit_tokens / input_tokens` |
| `usage_source` | string（可选） | 至少一个 usage 字段由厂商报告时固定为 `provider` |
| `total_tokens_derived` | boolean | total 由厂商 input+output 相加时为 true；厂商直接报告时省略 |

fake provider 或厂商未报告的 token 字段和 usage_source 省略，不得按字符数估算。生成完成/失败/停止
事件额外包含 `provider_call_count`、`delta_count`、整轮 token 汇总、`duration_ms` 和稳定 error_code。

Provider URL 必须在进入日志前结构化脱敏：移除用户名、密码、query 和 fragment；保留 scheme、host、port
与不含凭据特征的 path。疑似 Key/Token/Secret/Auth 路径段和异常长段替换为 `<redacted>`。配置为空时记录
对应 SDK 的公开默认基址并标记 `base_url_source=default`，不得因为没有显式配置而省略本次实际路由信息。

ContextBuilder 成功后记录一次 `context.loaded`，并把同一组诊断字段绑定到本轮后续的
`provider.call_started/completed/failed` 与 generation 终态。字段包括：

| 字段 | 类型 | 含义 |
|---|---|---|
| `context_schema_version` | integer | 确定性上下文序列化版本 |
| `runtime_prefix_hash` | string | L0 最终规范化内容的 SHA-256 |
| `role_prefix_hash` | string | L1 最终规范化内容的 SHA-256 |
| `conversation_prefix_hash` | string | L2 最终规范化内容的 SHA-256 |
| `checkpoint_hash` | string（可选） | C3 后实际注入 checkpoint 的内容 SHA-256；C3 前省略 |
| `tool_policy_hash` | string | 本轮可见工具策略的 SHA-256 |
| `context_message_count` | integer | 实际进入历史的终态消息数，不含当前消息 |
| `context_truncated_message_count` | integer | 因预算从历史中裁剪的消息数 |
| `estimated_context_tokens` | integer | 本地估算的完整输入 token，不含安全余量 |
| `input_budget_tokens` | integer | 扣除输出预留后的输入预算 |
| `estimator_kind` / `estimator_version` | string/integer | 本地估算器公共身份 |
| `estimator_is_provider_exact` | boolean | 估算器是否经验证与当前 Provider 口径精确一致 |
| `safety_margin_tokens` | integer | 本地估算额外安全余量 |

这些值来自一次 ContextBuilder 结果，后续事件不得重新读取数据库或重算 Prompt。hash 只识别变化层，不保存
层内容。预算字段只用于裁剪和解释 `CONTEXT_BUDGET_EXCEEDED`，不得汇总到 Provider `input_tokens`，也不得
设置 `usage_source=provider`。构建在产生结果前失败时没有可用指纹，只记录错误中已有的安全预算字段。

工具事件附加 `tool_name`、`status`、`duration_ms` 和白名单摘要。每类已知工具必须注册专用摘要器，只
提取明确允许字段；未知、MCP 和高风险工具默认不保存参数值。W1a 文件工具的路径只保存 SHA-256 指纹，
另可保存字节数、offset、分页上限与是否提供 expected hash，不保存相对/绝对路径、正文或工具原始输出。
递归脱敏是第二道防线，不得先整体序列化原始参数/输出再依赖正则删除敏感内容。

### 6.5 `access.jsonl`

| 字段 | 类型 | 约束 |
|---|---|---|
| `method` | string | 大写 HTTP 方法 |
| `path` | string | 不含 query string；不得包含 Token |
| `route_template` | string（可选） | 如 `/api/conversations/{conversation_id}/messages` |
| `status_code` | integer | HTTP 状态码 |
| `duration_ms` | number | middleware 端到端耗时 |
| `client` | string（可选） | 客户端 IP/端口；不做用户身份依据 |
| `response_bytes` | integer（可选） | 能安全取得时记录，不读取或缓冲流式 body |

OPTIONS 预检可以记录，但 summary/监控统计应与业务请求分开。

### 6.6 `errors.jsonl`

错误文件是原始 JSON 行的索引副本，保留相同 event_id、process_seq、category、event 和全部关联字段。
不增加 source_category，不改变业务事实。错误事件本身可以包含：

| 字段 | 类型 | 含义 |
|---|---|---|
| `error_type` | string（可选） | 异常类名 |
| `error_code` | string（可选） | [错误码注册表](../protocol/error-codes.md)中的稳定大写错误码 |
| `exception_message` | string（可选） | 脱敏异常消息 |
| `traceback` | string（可选） | 完整堆栈，凭据仍需过滤 |

只有 ERROR、CRITICAL 或未处理异常复制到这里，WARNING 不复制。预期 401、策略拒绝、provider retry、
cache miss、fallback、慢请求、WS reconnect、正常取消和用户停止不升级为 ERROR；它们仍在所属文件中
保留稳定事件。需要 WARNING 时直接查询 app/agent/access。

## 七、pytest 单元/集成测试规范

这里的 unit 指普通 `pytest` 整体，包括当前仓库中的后端单元和集成测试。成功测试的应用日志只输出
终端并由 pytest 捕获，不单独持久化到 events 文件。

### 7.1 `summary.jsonl`

每次 pytest（全量或局部）在会话结束时向当天文件原子追加一行：

```json
{
  "schema_version": 2,
  "run_id": "c8127a4f",
  "source": {
    "git_available": true,
    "git_head": "da1eeb1",
    "git_dirty": true,
    "working_tree_hash": "4f20e8c1"
  },
  "test_scope": "full",
  "started_at": "2026-08-25T10:15:21+08:00",
  "ended_at": "2026-08-25T10:15:38+08:00",
  "duration_ms": 17420,
  "status": "failed",
  "exit_code": 1,
  "collected": 62,
  "passed": 58,
  "failed": 2,
  "errors": 0,
  "skipped": 3,
  "xfailed": 0,
  "xpassed": 0,
  "deselected": 8,
  "database": "data/roleplex-test-20260825101521.db",
  "failure_dir": "failures/10-15-21_c8127a4f",
  "failure_files": ["world_backup.json", "owner_bootstrap.json"]
}
```

全绿时省略 `failure_dir`、`failure_files=[]`，且不创建 failures 运行目录。summary 不保存每条通过测试
名称。summary 的 status 使用 `passed | failed | interrupted`，它是测试轮次状态，不与运行事件终态枚举混用。

summary 字段定义：

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | integer | 测试报告 schema，固定为 2 |
| `run_id` | string | 本轮 pytest 的 8 位随机标识，与 failure 目录关联 |
| `source` | object | 本轮源码状态；包含 Git 可用性、HEAD、dirty 及 dirty 指纹 |
| `test_scope` | string | `full` 或 `partial`，不能仅凭收集数量推断 |
| `selection` | string[]（partial） | 经过允许字符过滤的测试路径/选择描述，不含 `-k/-m` 原表达式 |
| `selection_hash` | string（partial） | pytest 原始 invocation 参数的 SHA-256 前 8 位，用于区分不同选择，不可逆 |
| `started_at` / `ended_at` | string | 带本地时区的测试轮次起止时间 |
| `duration_ms` | number | pytest 会话总墙钟耗时 |
| `status` | string | `passed`、`failed` 或 `interrupted` |
| `exit_code` | integer | pytest 原始退出码；reporter 写入失败不得改变它 |
| `collected` | integer | pytest 收集到的测试 item 数 |
| `passed` / `failed` / `skipped` | integer | 对应 item 的通过、失败和跳过数量；`failed` 不由日志级别计算 |
| `errors` | integer | setup/teardown 等非 `call` 阶段发生失败的 item 数，不等于 ERROR 日志条数 |
| `xfailed` / `xpassed` | integer | pytest 预期失败和意外通过数量 |
| `deselected` | integer | 被选择条件排除、未实际执行的 item 数 |
| `database` | string/null | 仓库内测试数据库相对路径；非 SQLite 或无法安全表示时为 null |
| `failure_dir` | string（失败时） | 本轮 failure 目录相对于当日 unit 目录的路径 |
| `failure_files` | string[] | 本轮生成的失败详情文件名；全绿固定为空数组 |

`test_scope` 必须区分：

- `full`：从项目约定的默认 pytest 入口执行完整普通测试集，没有路径/nodeid、`-k`、`-m`、`--lf`、
  `--ff`、stepwise 等选择条件。
- `partial`：指定文件/nodeid/关键字/marker、只跑 contract，或任何会缩小/改变测试集合的选项。

partial summary 增加安全选择描述和原始调用选择部分的 hash：

```json
{
  "test_scope": "partial",
  "selection": ["tests/test_worlds.py"],
  "selection_hash": "0f77bca1"
}
```

selection 只允许仓库相对测试路径、测试函数名和固定选项标签；原始 CLI 参数可能含 URL/凭据时只参与
hash，不直接持久化。只有 `test_scope=full` 且最终 passed 才能支持“该源码状态全量测试通过”的结论。

### 7.2 失败文件

一个 pytest item 只生成一个 JSON，setup/call/teardown 的多个失败合并进 `failures` 数组：

```json
{
  "schema_version": 2,
  "run_id": "c8127a4f",
  "test_file": "tests/test_worlds.py",
  "test_name": "world_backup",
  "nodeid_hash": "a81f3c2d",
  "parameter_ids": ["sqlite"],
  "failures": [
    {
      "phase": "call",
      "exception_type": "AssertionError",
      "message": "expected backup to contain world.json",
      "traceback": "...",
      "captured_stdout": "...",
      "captured_stderr": "...",
      "captured_logs": "...",
      "stdout_original_bytes": 92118,
      "stdout_stored_bytes": 65536,
      "stdout_truncated": true,
      "stderr_original_bytes": 0,
      "stderr_stored_bytes": 0,
      "stderr_truncated": false,
      "logs_original_bytes": 428193,
      "logs_stored_bytes": 131072,
      "logs_truncated": true
    }
  ],
  "request_ids": []
}
```

raw nodeid、原始 parameters、参数 repr 和 pytest 自动生成的 ID 不保存；raw nodeid 只在内存中计算
nodeid_hash。`parameter_ids` 只来自项目显式声明的安全日志 case ID；没有声明时省略。

失败文件顶层字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | integer | 失败报告 schema，固定为 2 |
| `run_id` | string | 产生该文件的 pytest 轮次 |
| `test_file` | string | 仓库相对测试文件路径，不含参数化部分 |
| `test_name` | string | 去掉 `test_` 后的安全可读函数名 |
| `nodeid_hash` | string | raw nodeid 的 SHA-256 前 8 位；只用于区分实例，不是 Git ID |
| `parameter_ids` | string[]（可选） | 仅来自显式 `log_case` 的安全 case ID；不自动保存 pytest 参数 |
| `failures` | object[] | 该 item 在 setup/call/teardown 中所有失败阶段，按报告顺序聚合 |
| `request_ids` | string[] | 从已脱敏诊断文本中提取到的去重 request ID，便于关联 runtime/E2E 日志 |

`failures[]` 每个阶段的字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `phase` | string | `setup`、`call` 或 `teardown`；`call` 是测试函数主体 |
| `exception_type` | string/null | 从 pytest `call.excinfo` 读取的异常类名，普通断言为 `AssertionError`；插件合成且没有结构化 excinfo 的报告才为 null，不能据此判断是否失败 |
| `message` | string | pytest 给出的脱敏失败摘要，如断言表达式或异常简述 |
| `traceback` | string | 脱敏后的 traceback 尾部，包含实际失败位置；上限 512 KiB |
| `captured_stdout` | string | 该失败阶段写入标准输出 stdout 的文本尾部；上限 64 KiB |
| `captured_stderr` | string | 该失败阶段写入标准错误流 stderr 的文本尾部；上限 64 KiB |
| `captured_logs` | string | pytest logging capture 得到的格式化日志文本尾部；上限 128 KiB |
| `<kind>_original_bytes` | integer | 对应 traceback/stdout/stderr/logs 脱敏后、截断前的 UTF-8 字节数 |
| `<kind>_stored_bytes` | integer | 实际保存的 UTF-8 字节数 |
| `<kind>_truncated` | boolean | 是否因上限只保留了尾部 |

捕获通道与失败判定是两件事：只有 pytest 的阶段报告满足 `report.failed=true` 才创建 failure；stdout、
stderr 或 logging 中出现任何内容都不会单独把测试判失败。`captured_stderr` 的 stderr 表示输出流，不表示
日志严重级别。Python `logging.StreamHandler` 默认写 stderr，因此 DEBUG/INFO/WARNING/ERROR 都可能出现
在 `captured_stderr`。同一日志也可能同时出现在 `captured_logs` 和 `captured_stderr`，消费者不得把这种
诊断副本计为两次业务事件；真正的 runtime/E2E 机器事件仍以对应 JSONL 为准。

captured 内容只保留尾部，固定上限：stdout 64 KiB、stderr 64 KiB、captured logs 128 KiB。必须同时记录
original/stored bytes 和 truncated。traceback 上限 512 KiB，超限同样保留尾部并记录原始字节数；所有
captured/traceback 在截断前后都经过敏感值扫描和递归脱敏。

## 八、Playwright E2E 规范

### 8.1 运行目录

fake 与 real 分开：

```text
tests/e2e/fake/YYYY-MM-DD/HH-MM-SS_<run_id>/
tests/e2e/real/YYYY-MM-DD/HH-MM-SS_<run_id>/
```

E2E `run_id` 由 Playwright 配置进程生成，并通过环境变量传给后端；包装器因世界切换重启后端时沿用
同一 run ID，因此 alpha/beta 事件仍进入同一 E2E 目录，不因后端重启拆散。

### 8.2 `events.jsonl`

保存该轮后端全部结构化事件，字段使用第五、六节规范。每条额外固定：

```json
{"run_kind":"e2e-fake","run_id":"a8137c2f"}
```

real 对应 `e2e-real`。不同后端进程仍用各自 `process_instance_id` 区分。

### 8.3 `errors.jsonl`

保存该轮后端 ERROR、CRITICAL、未处理异常的原样副本，event_id/category/process_seq 不变，不包含
WARNING。浏览器 pageerror、失败的 console.error 和 Playwright 断言失败写入 summary 与 artifacts
索引，不伪装成后端 Python 日志。

### 8.4 `summary.json`

```json
{
  "schema_version": 2,
  "run_id": "a8137c2f",
  "run_kind": "e2e-fake",
  "provider_mode": "fake",
  "source": {
    "git_available": true,
    "git_head": "da1eeb1",
    "git_dirty": true,
    "working_tree_hash": "4f20e8c1"
  },
  "started_at": "2026-08-25T10:01:21+08:00",
  "ended_at": "2026-08-25T10:01:40+08:00",
  "duration_ms": 19000,
  "status": "passed",
  "passed": 15,
  "failed": 0,
  "skipped": 0,
  "backend_version": "0.1.0",
  "python_version": "3.12.13",
  "node_version": "22.22.2",
  "operating_system": "Linux-WSL2",
  "browser": "chromium",
  "browser_version": "...",
  "database": "data/roleplex-e2e-20260825100121.db",
  "worlds": [],
  "backend_process_instance_ids": ["912ec5d1"],
  "artifact_index": "artifacts.json"
}
```

世界切换 E2E 的 `worlds` 填 alpha/beta 目录；真实 E2E 额外记录 provider type、model 和脱敏 base URL，
但不记录 Key、URL query、请求 body 或完整模型回复。环境元数据只允许版本、操作系统、浏览器和 provider 公共
标识；不保存完整环境变量或 pip/npm 依赖清单。

真实 Provider 的显式数据库 smoke 与 real-world 测试都使用 `run_kind=e2e-real`；前者由 `database` 定位，
后者由 `worlds` 定位。不能仅凭 real 分类推断它是否经过世界包装器。

summary 启动时先原子写为 `status=running`，结束后用临时文件 + replace 更新为最终状态。进程异常结束而
未完成更新时，保留 `running` 供人工识别中断，不写虚假的 passed/failed。

E2E summary 字段定义：

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` / `run_id` / `run_kind` | integer/string/string | schema、8 位轮次标识和 `e2e-fake/e2e-real` 分类 |
| `provider_mode` | string | `fake` 或 `real`；表示该轮后端实际 Provider 模式 |
| `source` | object | 本轮源码 Git HEAD、dirty 状态及可选 working-tree 指纹 |
| `started_at` / `ended_at` | string/null | 带本地时区的开始/结束时间；running 时结束时间省略 |
| `duration_ms` | number | reporter 从启动到当前/终态的墙钟耗时 |
| `status` | string | `running`、`passed`、`failed` 或 `interrupted` |
| `passed` / `failed` / `skipped` | integer | Playwright item 最终结果数量；与后端日志级别无关 |
| `failures` | object[] | 失败 item 的安全文件名、测试名、test ID hash 和脱敏短消息；全绿为空数组 |
| `database` | string（可选） | E2E 独立数据库的仓库相对路径 |
| `worlds` | string[] | 世界切换 E2E 使用的隔离世界路径；普通/真实单库 E2E 为空数组 |
| `backend_process_instance_ids` | string[] | 从该轮 events 中发现的所有后端进程实例 |
| `provider_type` / `model` | string（可选） | 从 `provider.built` 提取的公开 Provider 类型与模型名 |
| `base_url` / `base_url_source` | string（可选） | 从 `provider.built` 提取的脱敏基址及 `configured/default/fake` 来源 |
| `backend_version` / `python_version` / `node_version` | string/null | 非敏感运行版本元数据 |
| `operating_system` / `browser` / `browser_version` | string/null | 非敏感系统与浏览器复现信息 |
| `artifact_index` | string | 固定指向本轮 `artifacts.json` |

### 8.5 `artifacts.json` 和 `artifacts/`

索引示例：

```json
{
  "items": [
    {
      "test_file": "tests/recycle-and-tombstone.spec.ts",
      "test_name": "delete_semantics",
      "test_id_hash": "81a03c9e",
      "type": "screenshot",
      "path": "artifacts/screenshots/deleted-role-history.png",
      "size": 183420,
      "sha256": "..."
    }
  ]
}
```

所有 E2E 默认关闭 trace/video：认证表单、DOM 和请求数据无法保证可逆脱敏，不能为了诊断便利保存凭据。
失败截图可以保留；Markdown/TXT/JSON 诊断附件必须读取、脱敏后再复制，ZIP/trace 不进入日志目录。
没有产物时 `items=[]`，`artifacts/` 可以不创建。

同一用例的不同附件、不同重试轮次必须使用不同文件名；文件名只包含脱敏测试身份、重试序号和附件序号，
不得用原始附件名带入敏感信息。复制后的每条索引必须仍匹配其文件内容，不能覆盖上一张截图后留下过期 hash。

`artifacts.json.items[]` 字段定义：

| 字段 | 类型 | 含义 |
|---|---|---|
| `test_file` / `test_name` | string | 产生产物的仓库相对测试文件与安全测试名 |
| `test_id_hash` | string | Playwright test ID 的 SHA-256 前 8 位，不保存原始参数对象 |
| `type` | string | 当前只允许 `screenshot` 或 `diagnostic` |
| `path` | string | 产物相对于本轮 E2E 目录的安全路径 |
| `size` | integer | 实际持久化文件字节数 |
| `sha256` | string | 产物内容 SHA-256，用于完整性与去重 |

## 九、脱敏与安全边界

### 9.1 禁止字段和值

- `password`、`current_password`、`new_password`、密码哈希。
- API Key、JWT、Authorization、Cookie、SecretStr、Fernet 明文。
- 完整用户输入、完整模型输出、MCP 原始参数和工具完整输出。
- 带凭据的 URL query；access 只保存不含 query 的 path。

### 9.2 允许的 token 用量字段

Provider usage 只允许：`input_tokens`、`output_tokens`、`total_tokens`、`cache_hit_tokens`、
`cache_write_tokens`。ContextBuilder 预算另外允许 `estimated_context_tokens`、`input_budget_tokens`、
`safety_margin_tokens`，但不得设置
`usage_source=provider`。除此之外，包含 `token` 的键仍按凭据过滤。脱敏必须递归处理嵌套字典、数组、
异常对象和 pytest captured 内容。

### 9.3 文件权限

新日志文件在支持的平台上使用 `0640`，目录使用 `0750`；Windows 依赖当前用户 ACL。日志不得被前端
静态服务暴露，也不得放进世界分发包。

## 十、归档、保留与容量

### 10.1 已批准策略

- 自动执行时机：每次后端启动，必须在结构化日志 handler 就绪后执行检测；失败不阻断服务启动。
- 压缩格式：tar.gz，Python 标准库 `tarfile` + gzip，`compresslevel=6`。它不引入额外依赖，在
  Linux/WSL 上可直接用 tar，在项目使用者的 Windows 环境中也已有解压工具。
- 归档触发：当前自然月的每日目录保持原始 JSONL，可直接查看；进入新月份后才归档上一个月及更早的已关闭
  来源。检测仍在每次启动执行，但同月内不会反复压缩昨日目录。
- 归档粒度：按“来源日期 + 类别”生成不可变 tar.gz，并按月份放目录；月初批量处理上月，但不持续追加或
  改写月度大归档，以保留逐日校验和局部损坏隔离。
- 保留期：按归档内容的本地来源日期计算，`age_days >= 30` 时删除；即保留今天和前 29 个自然日。
- 最大占用：整个 `logs/` 树目标上限为 1 GiB，即 `1_073_741_824` bytes；旧版日志也计入总量，但在
  完成显式迁移/归档前视为受保护内容，不自动删除。
- 容量优先级：30 天是最长保留，不是最低保证。总量超限时允许提前删除最旧归档，直到不超过上限。
- 保护边界：绝不删除当前自然月的 runtime/unit/E2E 原始目录、当前活跃日志、status=running 的测试目录、
  临时归档和旧版未迁移日志。若受保护内容本身超过 1 GiB，只记录 CRITICAL 并停止删除，不破坏活跃事实。

当前月目录不归档；例如 9 月 2 日仍可直接查看 9 月 1 日 JSONL，10 月首次启动才批量归档 9 月已关闭来源。
检测和本轮清理在应用报告 ready 前完成，确保健康检查成功时日志树已经处于一致状态；月初可能产生有限
启动延迟。清理失败写 ERROR 后继续启动，不能让日志维护故障使 Roleplex 永久不可用。

### 10.2 tar.gz 内容与校验

每个 tar.gz 根目录包含 `manifest.json`：

```json
{
  "schema_version": 2,
  "source_date": "2026-08-24",
  "archive_kind": "runtime",
  "created_at": "2026-08-25T08:00:00+08:00",
  "files": [
    {
      "path": "runtime/2026-08-24/app.jsonl",
      "size": 183420,
      "sha256": "..."
    }
  ]
}
```

允许的 archive kind：`runtime`、`unit-tests`、`e2e-fake`、`e2e-real`。归档流程：

1. 只选择早于当前自然月、已经关闭且不处于 running 的来源。
2. 把 tar.gz 写入同目录临时文件，不直接覆盖正式名称。
3. 重新以 `r:gz` 打开并完整读到 gzip 流尾，确认 tar 结构可遍历；拒绝绝对路径、`..` 路径穿越、
   symlink、hardlink、设备文件和其他非普通文件/目录成员。
4. 逐项读取普通文件内容，核对 manifest 的相对 path、size 和 SHA-256；不能仅凭 tar header 判定成功。
5. fsync 后原子 replace 为正式 tar.gz；正式归档存在时先校验 manifest，再把来源日期、类别及每个源文件的
   相对路径、大小和 SHA-256 与当前源逐项比较。完全一致才按幂等重入继续删源；同名但内容不同必须保源并报错。
6. 先写并 flush `log.archive_created`，确认审计事件落盘后，才删除已经成功归档的原始目录。
7. 写 `log.archive_source_removed` 记录实际删除数量和字节数。

任一步失败都保留原始目录，清理临时 tar.gz，并写 ERROR；不能出现“压缩失败但源日志已删”。进程在
正式归档落地后、源目录删除前崩溃时，下一次启动验证现有 manifest 后继续幂等收尾。

### 10.3 30 天与 1 GiB 淘汰顺序

每次启动按以下固定顺序：

1. 扫描并归档当前自然月之前的已关闭来源；同月每日目录保持原样。
2. 删除 `age_days >= 30` 的最旧正式归档。
3. 重新计算整个 logs 树实际字节数。
4. 若仍超过 1 GiB，按 `source_date`、`created_at` 从旧到新删除已关闭归档，直至达标。
5. 若没有可安全删除的归档仍超限，停止并记录 CRITICAL，不删除受保护内容。

tar.gz 临时文件和损坏/校验失败的归档不计为“可安全淘汰的正式归档”；它们只由恢复流程处理。

### 10.4 启动审计事件

归档清理必须产生可追溯结构化事件，并写入当天 `runtime/app.jsonl`。每次启动即使无动作也至少写开始和
完成摘要：

| 事件 | 关键字段 |
|---|---|
| `log.retention_started` | `archive_before`、`cutoff_date`、`max_total_bytes`、`bytes_before` |
| `log.archive_created` | `source_date`、`archive_kind`、相对 archive path、文件数、源/归档字节、SHA-256 |
| `log.archive_source_removed` | `source_date`、`archive_kind`、删除文件数、删除字节数 |
| `log.archive_delete_planned` | 相对 archive path、字节数、`reason=age|size`、来源日期、SHA-256 |
| `log.archive_deleted` | `planned_event_id`、实际释放字节数；本事件拥有自己的 event_id |
| `log.retention_completed` | `bytes_before/after`、创建/删除归档数、释放字节、耗时 |
| `log.retention_failed` | 阶段、脱敏异常类型、可登记的稳定 error_code、耗时 |

所有待删除路径必须先规范化并验证位于 v2 日志根目录内。删除正式 archive 前先写
`log.archive_delete_planned` 并 flush；删除完成再写结果。若进程在两者之间崩溃，下一次启动可以根据
planned 事件、文件存在性和 manifest 判断实际状态，不能无审计地猜测。
planned 审计事件无法成功写入并 flush 时必须中止该项删除，不能“日志失败但继续清理”。

审计日志位于当天活跃文件，不属于本轮可删除集合，因此本次启动的删除证据不会被自己删掉。审计事件
本身按正常 30 天/容量规则保留，不单独建立无限增长的永久审计文件。

### 10.5 并发与重复启动

清理器在 `logs/archive/` 下使用进程级互斥租约。无法取得租约时写 `log.retention_skipped` 并继续启动，
不能让两个世界切换进程或测试后端同时归档/删除同一来源。过期租约必须验证持有进程已经消失后才能
回收；不能仅按文件时间判断。

## 十一、原子性、失败与兼容

- JSONL 使用单行 append；单进程单 worker 下由 handler 锁保护。轮转必须在同一 handler 锁内完成。
- summary、failure、artifact index 使用临时文件写完后 `replace`，避免半个 JSON。
- pytest summary append 前先完整序列化成单行；写失败只警告终端，不得把测试本身改判失败。
- 旧版 `logs/YYYYMMDD/{runtime|unit|e2e-*}/` 不自动搬迁或删除；实现 v2 后从新日期目录开始写。
- 日志 schema 变化必须递增 `schema_version`，新增字段保持消费者可忽略。

### 11.1 JSONL 损坏容忍

- reader 发现仅最后一行是不完整 JSON：将其视为 crash truncation，跳过该行并显式报告文件、偏移和
  丢弃字节数。
- reader 发现中间任意一行损坏：整个文件标记为 corrupted，停止静默消费；不能跳过后继续生成看似
  完整的统计。
- runtime 进程准备继续追加 active 基础文件时，必须先检查最后一行。若尾部不完整，截断到最后一个
  完整换行，再写 `log.tail_recovered`，记录 recovered_bytes 和损坏尾部 SHA-256，不保存损坏原文。
- 已关闭的 `.001/.002/...` 文件永不修补或追加；reader 仍按“仅容忍最后一行”的规则报告。
- 磁盘满或 append 失败时向终端/系统 stderr 报告，不能递归调用同一个失败 handler 造成日志风暴。

## 十二、常用排障查询

查一条请求跨 app/agent/error 的完整链路：

```bash
rg '请求ID' logs/runtime/2026-08-25/{app,agent,errors}*.jsonl
```

查当天真实模型调用及 token：

```bash
rg '"event":"provider.call_completed"' logs/runtime/2026-08-25/agent*.jsonl
```

查某轮单元测试：

```bash
rg '"run_id":"c8127a4f"' logs/tests/unit/2026-08-25/summary.jsonl
```

查真实 E2E：

```bash
find logs/tests/e2e/real/2026-08-25 -name summary.json -print
```

## 十三、实施顺序与验收

建议分四步实施，避免同时重写日志 handler 和测试框架钩子：

1. runtime 按日四文件、event_id/process_seq、进程实例事件、分类过滤、错误原样副本、尾部恢复和轮转。
2. pytest session scope/summary 与按失败 item 聚合且不落 raw nodeid/参数的 failure JSON。
3. Playwright fake/real run 目录、summary 和 artifact index。
4. 实现启动归档、tar.gz manifest 校验、30 天/1 GiB 淘汰、互斥租约和删除审计。

验收标准：

- 世界切换重启不会创建 runtime 子目录，日志能按 `(process_instance_id, process_seq)` 还原观察顺序。
- runtime 每种活跃文件都满足 10 MiB/一小时/跨日轮转边界。
- 全绿 pytest 只新增一行 summary，不创建 failure 目录。
- pytest summary 能区分 full/partial；setup/call/teardown 多阶段失败合并，参数化重名不覆盖且不落原参数。
- fake/real E2E 物理隔离，世界切换重启仍落在同一 E2E run 目录。
- errors 与原事件 event_id/category/process_seq 相同，删除 errors 不影响 app/agent/access 原始事实。
- 最后一行崩溃截断可报告/恢复，中间行损坏会阻断消费。
- 启动归档只有在 tar.gz/manifest 校验和审计 flush 成功后才删除源；30 天与 1 GiB 淘汰顺序可重复验证。
- 删除前后事件能够对应，模拟归档失败、磁盘满、进程中断和重复启动均不会误删活跃/未归档事实。
- 任意日志和测试失败文件中都不能检出测试口令、API Key、Authorization 或完整模型回复。

## 十四、已批准归档决策摘要

- 保留：30 个自然日（今天 + 前 29 日）。
- 容量：整个 logs 树目标上限 1 GiB；超限可以提前删除最旧正式归档。
- 格式：按日期/类别生成 tar.gz，gzip level 6，包含 manifest、size 和 SHA-256。
- 时机：每次后端启动、结构化日志就绪后执行；互斥、幂等，失败不阻断启动。
- 安全：当天、活跃、running、临时、损坏和未迁移旧日志不自动删除；无法达标时记录 CRITICAL。
- 审计：先写计划并 flush，后删除，再写结果；归档验证失败时原始目录必须保留。

tool.write_wait_completed 记录写入准入结束的固定 status、queue_wait_ms、lock_wait_ms 和等待失败 phase/reason（工作区协议定义），沿用原 tool_call_id/Trace。只在真实工具调用上下文记录，不记录参数、路径、源码或逐轮轮询。

tool.call_not_dispatched 只记录宿主确认未派发的工具名、调用身份、status=not_executed、reason=graph_budget；不伪造 tool.call_started/completed，不记录参数或模型原文。

T3 workspace_edit 的 replacements 只允许记录 replacement_count 与合计 old_text_bytes/new_text_bytes；批次沿用 item_count。不得记录匹配片段或返回原始替换内容。

T4.2 沿用 generation.budget_stopped，reason 兼容新增 decision_budget；provider.call_completed 仍只表示调用返回，响应完整性失败随后以 generation.failed / PROVIDER_RESPONSE_INCOMPLETE 记录，不增加原始响应日志。

工具参数拒绝沿用 tool.call_not_dispatched，reason 增加 arguments_invalid/tool_unavailable，error_code 使用已登记的 TOOL_ARGUMENT_INVALID/TOOL_ARGUMENT_JSON_INVALID/TOOL_NOT_AVAILABLE；字段路径仅供 Owner 加密详情，不进入正式日志，拒绝不伪装为 generation.failed。

执行异常的 generation.failed 可增加 error_phase（provider_request/tool_execution/event_processing/runtime）与 error_type（固定允许的异常类标签，未知为 OtherError）；不保存异常正文。具体协议配对故障优先使用各自 AGENT_TOOL_* / AGENT_EVENT_STREAM_INCOMPLETE 错误码。
