# 执行事实与可信收尾

| 元数据 | 值 |
|---|---|
| 受众 | 内部（运行时、ContextBuilder、消息所有者与测试） |
| 状态 | 已实现（T1，已人工验收） |
| 协议版本 | 1 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `app/agent/loop.py`、`tools.py`、`write_capture.py`、`app/services/chat.py`、`tool_details.py`、`app/models.py`、锁定框架源码 |
| 关联测试 | `backend/tests/test_tool_reliability_t0.py`、`test_workspace_batch_mutation.py`、`test_write_diff.py`、`test_agent_pipeline.py` |
| 复核日期 | 2026-09-14 |

范围与验收关口见[路线计划](../../plan/tool-reliability-capability-roadmap-v1.md)，探针结果与可重复命令见
[T0 验证记录](../../testing/tool-reliability-t0.md)。本文负责事实投影与收尾契约；公开摘要字段只在
[执行摘要](../public/messaging/execution-summary.md)定义。T1 的实现、回归和真实复测见 [T1 验证记录](../../testing/tool-reliability-t1.md)。

## 一、T0 基线事实来源与缺口

| 来源与关联代码 | 能证明什么 | 不能证明什么 / 保存时点 |
|---|---|---|
| `AgentExecution` → `Generation` → `Message` | 一轮执行、角色、会话、chain 与消息归属 | execution 完成不等于用户任务通过验收；调度状态与消息分别由其所有者维护 |
| `loop.run_agent` 的模型结束回调 | Provider 提议的工具 ID、参数和本轮正文（进程内） | 回调不等于图提交或宿主派发；这些消息当前不进入 `_wrap_up` |
| 框架工具开始/结束 → `ToolCallStarted/Finished.call_id` | 宿主调用生命周期及其配对 | 开始不能证明通过审批、OS 已启动或磁盘已提交 |
| `GuardedTool._arun` → `tool_context.tool_call_id` | 包装工具回调 run ID 与领域 call_id 相同 | Provider tool_call_id 是另一个身份；当前领域事件未保留两者映射 |
| 结束事件的 `ToolMessage.tool_call_id` | 工具结果与 Provider 提议配对 | 结果丢失时映射可能缺失，不能按工具名、完成顺序或相同参数猜配 |
| `ToolCall` 审计行 | 执行/消息归属、工具名、安全参数摘要、状态及耗时 | **没有 call_id 列**；不能用时间或名字连接到某次调用，created_at 是审计写入时刻 |
| 消息 `tool_call` part | 持久 call_id、原时间线位置和公开状态 | success 是工具返回状态，不是所有副作用已采集；开始/结束分别由 reducer 保存 |
| `ToolExecutionDetail` | 唯一 `(message_id,call_id)`、execution、开始/结束时间及加密输入输出 | 仅白名单采集；七天后不可用，解密/保存失败也不能补造；开始事务先建行、结束更新密文 |
| `WriteReceipt.applied` 与批量修改凭据 | 提交边界确认的版本、逐项 applied、diff 覆盖或降级 | 文件系统提交到数据库之间没有分布式事务；未保存前崩溃保持未知，不读当前文件补历史 |
| `WriteCaptureScope` | generation 内、原 call_id 下尚未消费的有界凭据，取消可取回 | `take` 会消费；完成路径消费后不能再次取回当作收尾事实；最多 32 个未消费凭据 |
| Shell 审批、命令结果、`RuntimeEntry` | 审批决定、已观察退出码、服务登记与特定时刻就绪/回收事实 | 批准不等于执行，退出 0 不等于任务验收，服务就绪不等于永久可用，不枚举脚本全部文件修改 |
| `EventLog` 与结构化日志 | 前者提供持久消息事件恢复；后者提供诊断关联 | 不解析机器日志重建业务状态；事件时间不替代身份和消息 revision |

现状 `_finish_tool_event` 先写审计，再更新消息/详情，不是一个跨所有表的原子事务。
正常结束已观察结果时 reducer 使用 shield 等待保存；取消时消费剩余文件凭据，最后清空作用域。
异常路径可能只把尚在 running 的工具/详情改为 interrupted，未落库凭据随后被清除。
这些缺口必须保持可见，不能因为审计有一条 success 就恢复出某个 call 的提交结果。

## 二、T1 当前范围：证据与停止原因

用户已确认移除正常/异常回合的统计摘要生成及默认历史注入。T1 仅保留具体工具结果、提交凭据、
准确停止原因和关闭不完整的额外模型收尾。没有业务恢复入口，不自动重试或重放工具。

执行事实由原 generation 的消息所有者维护，沿用宿主 call_id、工具卡、私有详情与原版本证据。
`execution_evidence.tool_evidence` 只从白名单原生凭据提取每次调用的安全提交状态/数量，不解析模型正文、
未知工具输出或机器日志推断成功。部分批次保留已确认前项和未知后项；取消/失败不能覆盖已确认提交。
具体文件/版本/逐项结果与命令、服务回执继续受原详情权限和七天保留约束，不合并成一份回合总结。

同一执行的 Provider 提议和返回仍在防腐层按真实 ID 配对，宿主身份不由模型指定。图预算停止凭
GraphRecursionError 或受控 remaining_steps/派发状态确认；缺失结果使用 AGENT_PROTOCOL_ERROR，
不凭 pending 数量猜测触顶，不用兜底文本判定预算。用户停止原样取消，不追加模型解释请求。

原消息终态在同一短事务保存 `meta_json.stop_reason`，字段定义见[消息协议](../public/messaging/messages.md)。
正常完成由原 status 表达，不新增总结；启动恢复记录 interrupted，保留已有工具证据，不重新读取文件补造历史。
旧 execution_summary 只兼容读取停止原因，用户发送内容不能成为服务器终态来源。

## 三、模型历史与缓存边界

Context schema 4 移除 summary_text/facts_only 及“非权威正文”前缀。正常历史保留原正文、缩进、顺序与既有工具占位；
旧统计摘要完全忽略，不用未知 part 占位间接增加输入。stopped 只追加简短停止标记，能确认图预算或用户停止时
准确标注，旧原因缺失时只写“已停止”。error/interrupted 的正文与摘要均不自动进入下一轮历史。

ContextBuilder 继续校验会话、角色、成员和消息边界，使用既有消息级预算裁剪；超预算不退化为统计摘要。
版本升级会改变一次稳定前缀，不承诺厂商命中率提升；正常历史不再携带逐轮统计文本。
中断后的工作恢复需要用户选择、权限复核、具体证据/当前资源核查与未知结果处置，留待独立设计，
不能把普通“下一轮消息”自动当成恢复任务。

## 四、持久化、并发与兼容

无表/列/索引变化，不需要迁移。`stop_reason` 使用现有消息 JSON 元数据，具体证据沿用原工具卡与加密详情。
新消息不再带 execution_summary_version 标记或 execution_summary part；已结束的旧记录不批量改写、不删除。
旧前端可能只能显示“已停止”；新版通过消息 stop_reason 显示图预算提示，旧摘要保持隐藏。

工具边界、终态和恢复仍由原所有者写入消息 revision；消息事件持久化后广播。迟到任务不能覆盖终态，
临时数据库锁用有限重试，不自动重放已执行工具。资源回收沿用命令/Shell/服务规则，结束回复不自动停止已托管服务。
文件系统与数据库仍没有分布式事务，崩溃前未保存的结果保持未知。

日志继续使用 generation.budget_stopped、generation.failed 等既定事件，仅记录固定原因、身份和耗时。
不新增回合统计日志，不另造 Trace 或执行账本；工具/模型原文不进入正式日志。

## 五、验证

`test_execution_summary.py` 保留历史文件名，现验证“不生成、不注入旧/新摘要”、原始正文稳定、正常预算裁剪、
提交后触顶/失败/取消、元数据停止原因、旧记录兼容、恢复与权限。原 Agent、文件、消息/WS 测试继续覆盖底层边界。
真实前后端验证工具卡/Owner 详情仍可用、Guest 隔离、停止原因刷新恢复，以及 API 返回中不再出现新摘要。
独立真实 Provider 对照使用逐调用证据核对写入及无额外收尾，不再构造测试系统摘要。
最新结果见[T1 验证记录](../../testing/tool-reliability-t1.md)。

自动建父目录的事实随原调用凭据保存，与文件提交分开；单文件/批量的字段、旧记录兼容和 effect_state 降级以[工具详情](../public/messaging/tool-details.md#自动创建父目录的调用级证据)为准，不据文件未提交推断完全无副作用。


## 小阶段 5：图预算阻止派发的提议

防腐层只有在锁定框架的 remaining_steps 确认禁止派发、未观察到工具开始且提议配对无异常时，才产生 ToolCallsNotDispatched。
该事件一次携带同响应全部提议：宿主分配调用身份、白名单工具名、安全参数摘要和可选 Owner 私有输入。
模型编造的未知工具名降级为 unknown_tool，不写入其原始名称/参数。事件不代表授权通过或工具执行，不能产生伪造的开始事件。
原消息所有者将整组记录收口后再响应取消，状态 not_executed、not_executed_reason=graph_budget；无耗时、文件提交或目录副作用。
Owner 输入继续走原加密详情，Shell/MCP 未登记的输入不扩展采集；不新增回合摘要、模型请求、自动重放或恢复。
GraphRecursionError 不掩盖已发现的协议配对异常。当前仍可能花费一次模型请求后才发现工具提议不能派发；不以跳过合法最终回答为代价提前一刀切中止模型请求。

T4.2 decision_budget 表示最后已接纳操作结束并交接后，不再请求下一次模型决策；无工具提议时不制造未派发记录。历史简短停止标记使用决策上限原因，不注入新回合总结。终态与截断边界见[预算契约](agent-budget.md)。

用量观测接入时补齐了同进程取消交接边界：原生文件工具在正文执行前保留宿主身份和既有白名单参数摘要；防腐层只导出提交凭据快照，消息所有者落库确认后才消费。取消前先关闭图事件流并等待工具收口；开始通知尚未落库或尚未交付时，也可依据真实凭据补齐已提交事实。此改动不重放操作，不承诺跨进程崩溃恰好一次。
