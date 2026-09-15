# World Agent 默认决策预算

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner API；消息链与扣减为内部契约 |
| 状态 | T4.3a 已人工验收，时间截止/审批计时/恢复未实现 |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-15 |
| 事实来源 | routers/agent_budget.py、services/agent_budget.py、消息创建、Agent 前置回调及对应测试 |

GET /api/agent-budget/config 只允许当前 World Owner，返回 decision_limit（保存值）、effective_limit（受部署 ceiling 裁剪）、ceiling、revision。
PUT 同路径接收严格整数 decision_limit=1..256、expected_revision>=0，禁止额外字段；超过部署 ceiling 返回 422 AGENT_BUDGET_LIMIT_INVALID，修订冲突返回 409 AGENT_BUDGET_REVISION_CONFLICT。成功返回更新值与修订号；读写均 no-store。
Guest 返回既有 Owner 鉴权错误；登录、Token 撤销和 World 隔离复用既有认证，不允许指定其他 Owner/World。无新增幂等键，CAS 冲突应重读确认，不盲目覆盖。

默认值暂为 8；环境 AGENT_DECISION_CEILING=1..256，默认 256，只裁剪新任务。Owner 选择 1..ceiling，无关闭/无限选项。降低部署上限或修改默认不改变已有 chain 快照；时间/费用硬封顶尚未实现。
用户消息创建事务同时创建 workflow_budgets；同一 chain 的所有角色共享快照，新用户消息才创建新预算。现有 client_message_id 幂等重发返回原链，不续额。模型与 Guest 不可修改快照。
每个模型决策前短事务检查 used_decisions<decision_limit，再原子增加共享计数和 execution.decision_count；同一 execution/决策序号仅为数据库重试幂等，不授权重放模型请求。失败、取消后已消费额度不退款；数据库异常在模型请求前终止，已提交的计数保守保留。
同一 execution 仍由原所有者任务执行；并发不同 execution 共享 SQL 条件更新，不能靠进程锁或事后汇总保证限额。未消费到额度的角色 decision_budget 停止，无模型请求。
缺少快照的旧链不补造授权，也不自动恢复。消息链统计不冒充精确 HTTP 请求或费用统计，不把群聊单个角色结束当成整条任务完成。
无用户正文、Key、原始参数新增到日志；预算没有新的 Trace 身份。原停止原因和工具提交语义见[预算领域](../../internal/agent-budget.md)。
