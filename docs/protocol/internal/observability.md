# 日志、请求关联与异步链路观测

| 元数据 | 值 |
|---|---|
| 受众 | 内部开发、测试与本机运维 |
| 状态 | 已实现（日志 schema v2） |
| 协议版本 | 8 |
| 维护者 | Roleplex |
| 事实来源 | `backend/app/config/logging.py`、`backend/app/config/log_archive.py`、`backend/tests/reporting.py`、`frontend/tests/log-reporter.ts` |
| 详细规范 | [日志目录与字段规范 v2](../../design/logging-v2.md) |
| 复核日期 | 2026-09-14 |

本文说明当前代码已落地的观测行为。目录、完整字段表、事件目录、轮转、pytest/E2E schema 和归档算法
统一引用详细规范，不在本文复制第二份权威定义。

T1 图预算终态与协议异常的事件映射见详细规范；安全执行摘要只保存在消息中，不进入机器日志。

## 输出布局

- runtime：`logs/runtime/YYYY-MM-DD/`，按 app、agent、access、errors 四类写入；后端重启继续追加当天
  active 文件，不创建进程目录。
- pytest：`logs/tests/unit/YYYY-MM-DD/summary.jsonl` 每轮一行；全绿不保存应用事件，失败才在
  `failures/HH-MM-SS_<run_id>/` 写有界诊断。
- E2E：`logs/tests/e2e/{fake|real}/YYYY-MM-DD/HH-MM-SS_<run_id>/`，保存 events、errors、summary 和
  脱敏 artifact 索引。世界切换后的多个后端进程沿用同一 run ID。
- archive：当前自然月每日目录保持可直接查看；新月份启动后才把上月及更早的关闭日志按日期/类别归档为
  tar.gz。保留 30 天，整个日志树目标上限 1 GiB。

runtime active 文件按 10 MiB、一小时或跨日轮转；关闭片段使用递增 `.001/.002/...` 且永不再次写入。

## 事件身份与链路

每条后端事件包含唯一 event ID、进程实例 ID 和进程内观察序号。`timestamp` 固定使用北京时间 `+08:00`。
错误文件复制同一事件，不改变 ID、category 或序号。可选业务关联字段无值时省略；当前世界名始终保留。

Roleplex 只使用现有业务 Trace 模型：消息 chain 是整条 trace，Agent/工具 execution 是 span，父 execution
表达父 span。上下文通过 contextvars 跨 HTTP、WS、数据库、后台任务和工具调用传播。

M4a 队列把原 HTTP request ID 和安全唤醒参数写入持久 job；E0 后共享 chain、每角色独立 execution ID、role
和 execution kind 从 `agent_executions` 读取，并在创建会话 worker 子任务时显式绑定。不同 chain 复用同一
worker 时不得继承上一条请求的日志上下文。队列终态使用 `generation.queue_job_completed`，只记录身份和
success/failed/cancelled，不保存任务 payload；启动降级另记 `execution.interrupted`，不伪造 Provider 调用。

WebSocket/流式事件继续关联 stream epoch、event seq、message revision、delta seq 和工具审计标识；流式
文本不逐 token 写日志。

## 分类与错误

- app：进程、认证、世界、数据库、会话、消息与 WebSocket。
- agent：generation、execution、provider、context 和工具调用。
- access：自定义 HTTP 完成/失败事件；默认 Uvicorn access 不再持久化或重复输出。
- errors：仅 ERROR、CRITICAL 和未处理异常的原样索引副本；WARNING 不进入。

错误码必须来自 [错误码注册表](../error-codes.md)。正常取消、策略拒绝、重试和降级使用稳定 status/reason，
不伪装成 ERROR。

## Provider usage

每次模型调用记录 provider mode/type、模型、首分片耗时、总耗时和厂商报告的输入/输出/总量/缓存命中
与缓存写入 token。仅当同次调用有正数 input 和非负 cache hit 时计算命中比；至少一个 usage 字段存在时
标记来源为 provider。fake 或厂商未报告时省略，不做字符数估算。多次模型调用的 generation 汇总只有在
每次调用都报告对应字段时才生成，避免用部分数据冒充整轮事实。

`provider.built`、`provider.call_started/completed/failed` 记录同一脱敏 `base_url` 和来源。URL 在业务层
先移除 userinfo、query、fragment 并遮蔽疑似凭据 path；日志递归脱敏只是第二道防线。fake 固定记录
`fake://local`，未显式配置的真实 Provider 记录公开 SDK 默认基址，不能留空让排障者猜路由。

## Context 与缓存诊断

ContextBuilder 成功后写 `context.loaded`，并把同一快照的 schema version、L0/L1/L2/tool policy SHA-256、
实际历史消息数、裁剪数、本地估算器身份与预算绑定到本轮 Provider 和 generation 事件。C3 前不伪造
checkpoint hash；完整 Prompt、层内容、用户输入和模型输出仍不落盘。缓存 token 和命中比只使用厂商数据，
本地估算字段保持 `estimated_*`/`estimator_*` 命名，不进入 Provider usage 汇总。

## 测试报告

pytest summary 区分 full/partial，并记录 Git HEAD、dirty 指纹、数据库和通过/失败/跳过统计。失败文件不
持久化 raw nodeid 或原始参数，只保存相对测试文件、测试名、安全 case ID 和 nodeid hash。stdout、
stderr、captured logs、traceback 均保留尾部、限长、记录原始/保存字节数并递归脱敏。

Playwright summary 初始原子写为 running，结束后 replace 为终态；崩溃保留 running。summary 记录
Python/Node/浏览器/系统版本、数据库/世界、provider 公共信息和后端进程实例。所有 E2E 关闭 trace/video；
截图可以保留，文本诊断附件必须脱敏后复制。

## 归档与损坏容忍

runtime 启动且结构化 handler 就绪后执行归档检测。tar.gz 必须含 manifest，并重新读取 gzip/tar、检查
安全相对路径、成员类型、size 和 SHA-256 后才可删除源。删除归档前写 planned 事件并 flush，删除后写
结果；无法审计则中止删除。归档清理有进程租约，失败不阻断服务启动。

JSONL 只容忍最后一行崩溃截断；active 文件重启追加前会截断损坏尾部并记录恢复事件。中间行损坏会
标记整个文件 corrupted；已关闭片段不修补。

## 敏感数据边界

会话连接解耦后，同一 ws_connection_id 可先后关联多个会话；订阅释放记录 ws.unsubscribed，后续订阅
仍复用连接身份。同步完成对应 ws.subscribed，不新增重复业务事实；Token 仅在首帧使用，不进入控制日志。

密码、哈希、API Key、访问凭据、Authorization/Cookie、完整用户输入/模型输出、带凭据 query 和 MCP
敏感参数不落盘。工具摘要先走字段白名单，递归脱敏只是兜底。只允许日志设计中显式登记的 Provider 与
Context token 字段。

W1b 复用工具开始/完成日志，参数审计仅保存 allowlist command ID；取消以 cancelled 正常收口。
W1c 继续复用上述执行事实；审批生命周期新增 tool.approval_requested/resolved，安全字段及终态以
[日志 v2](../../design/logging-v2.md)为准。审批与工具共享 execution/chain/tool_call，不创建另一套 Trace。
命令路径、cwd、stdout/stderr 不进入日志、审计或公开工具卡，详见 [结构化命令](workspace-commands.md)。

## 验证覆盖

- `backend/tests/test_logging.py`：身份、分类、副本、轮转、重启追加、损坏恢复。
- `backend/tests/test_reporting.py`：pytest scope、安全身份和 captured 尾部。
- `backend/tests/test_log_archive.py`：tar.gz manifest、失败保源、30 天/容量淘汰、running 保护与审计。
- Playwright 普通、世界切换和真实 provider 配置：run 目录、summary、环境/provider 信息与脱敏产物。

T2 扫描沿用既有工具/执行身份，新增 tool.scan_completed 的字段与语义见日志 v2；不逐块记录，不保存查询或匹配正文。
