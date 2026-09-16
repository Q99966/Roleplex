# Agent 执行预算与计数

| 元数据 | 值 |
|---|---|
| 受众 | 内部运行时、调度与测试维护者 |
| 状态 | 已实现：直接决策计数、消息链共享快照、自定义与不限模式；无统一任务时间/Token/费用截止 |
| 协议版本 | 1（内部计量约定；公开配置以预算 v2 为准） |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-16 |
| 事实来源 | `app/agent/loop.py`、`app/services/agent_budget.py`、`app/config/decision_budget.py`、`app/models.py`、`app/services/execution_usage.py` |
| 关联测试 | `tests/test_agent_budget_accounting.py`、`test_agent_decision_budget.py`、`test_workflow_budget.py`、`test_execution_usage.py` |

## 计量口径

| 计数 | 定义与边界 |
|---|---|
| 模型决策 | 准备进入一次模型决策时占用额度。产品路径在模型请求前持久更新消息链 `used_decisions` 与该 execution 的 `decision_count`；失败不退还，不按成功次数计预算。 |
| 模型调用 | `ProviderCallStarted/Completed` 来自框架回调；持久用量以 execution/call_index 关联。可能有开始而无完成，不能称为精确 HTTP 尝试数。 |
| SDK HTTP 尝试/重试 | 一次模型调用内可有多次传输；未完整独立观测的次数与费用保持未知，不从决策次数推算。 |
| 工具提议 | 模型返回的工具调用，包括参数拒绝或预算阻止派发者；提议不等于执行。 |
| 实际工具调用 | `ToolCallStarted` 表示进入工具路径，仍可能在权限、审批、准入或预检处拒绝；不能据此认定文件已提交。 |
| 文件节点 | 批量 write/edit 中的 items；同文件 replacements 是片段数，不另算文件节点或决策轮次。 |
| 图步数 | 框架内部 super-step，取决于锁定版本和拓扑；不作为产品工具余额，也不按工具个数换算。 |
| Token 与耗时 | 使用厂商实际报告和各调用观测；缺失保持未知，现有耗时统计不表示已实现统一墙钟预算。 |

## 配置、快照与原子扣减

World 配置的字段、默认值、数值范围、revision 冲突与迁移以[公开预算 v2](../public/rest/agent-budget.md)为准。正整数表示有限次数，`null` 表示明确不限；旧 256 限制和 `AGENT_DECISION_CEILING` 已退役。

用户消息与 `WorkflowBudget` 在同一事务冻结配置，客户端幂等重发不新建预算。同一消息链的群聊角色共享额度；修改 World 配置只影响新消息链，不改变已排队或运行任务的快照。

`before_decision` 在请求模型前原子消费共享额度，同时更新 execution 内的序号。数据库重试按序号幂等，失败或重试不能重复扣减，也不能静默重置历史计数；快照缺失、归属或序号不符明确失败。执行终结或用户停止时传播取消，不再发起模型请求。

## 停止与结果交接

到达额度后阻止下一次模型决策；最后一次有效模型响应的已接纳工具先按现行授权、取消和资源规则处理并保存结果。有效的正常最终回答优先于“计数已到上限”，不能因此改标预算停止。

`decision_budget` 表示无下一次决策额度，消息状态为 `stopped`；`graph_budget` 保留内部保护与旧记录含义。两者都不代表用户任务失败或已验收，也不触发额外模型总结、自动续额或工具重放。

明确截断/内容过滤以 `PROVIDER_RESPONSE_INCOMPLETE` 收口，不派发该响应的工具。有可靠调用 ID 的 JSON/schema 参数错误或不可用工具可向模型返回结构化拒绝，允许在原预算内修正；身份、配对或事件流故障使用具体错误码，见[Agent 运行时](agent-runtime.md#可恢复的工具参数拒绝)。不能再将所有参数错误描述为终止性的 `AGENT_PROTOCOL_ERROR`。

未派发、已开始、已提交与未知结果分别保留，取消不能覆盖已知事实，见[执行事实](execution-facts.md)。后续请求的事实核对与交接已实现，见[中断上下文](interruption-context.md)；它不恢复旧图、旧 chain 或旧工作流预算。

## 图保护与不限模式

未显式指定内部图上限时，有限模式当前使用 `2 × decision_limit + 2`，给模型与工具结果交接留出空间；该公式仅适用于现有拓扑，不作为跨框架版本承诺。显式 `recursion_limit` 用于内部诊断/测试。

不限模式在防腐层使用 `decision_limit=None`，框架内部使用无穷值参与步数比较；该值不进入 Provider、数据库或公开 JSON。共享实际计数、幂等、权限、取消、上下文预算、单次超时和错误处理仍然有效，不以另一有限隐藏图上限冒充不限。

升级相关框架或图拓扑时，验证超过旧 256 次的循环、有限模式最后结果、正常结束、参数拒绝、取消及协议错误，不能只测纯文本 happy path。

## 历史与未实现范围

早期默认 15 图步和第八次文本误判的取证见[T4.1 记录](../../testing/agent-budget-t41.md)，修复结果见[T4.2 记录](../../testing/agent-budget-t42.md)。这些是当时拓扑的观测，不是现行产品额度。

统一任务时间/审批计时、Token/费用限制及自动续跑旧任务尚未实现。阶段结果与后续安排见[计划索引](../../plan/README.md)，历史阶段顺序不构成新增限制的前置要求。
