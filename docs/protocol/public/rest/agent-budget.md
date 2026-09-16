# World Agent 默认决策预算

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner API；消息链与扣减为内部契约 |
| 状态 | T4.3c 已人工验收，时间截止/审批计时/恢复未实现 |
| 协议版本 | 2（同路径升级，前后端须同步部署） |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-16 |
| 事实来源 | routers/agent_budget.py、services/agent_budget.py、消息创建、Agent 前置回调及对应测试 |

GET /api/agent-budget/config 只允许当前 World Owner，返回 decision_limit（保存值）、effective_limit（与保存值相同）、ceiling（兼容字段，固定 null）、revision。
PUT 同路径要求显式提供 decision_limit：严格正整数 1..9007199254740991 或 null（不限次数），expected_revision>=0，禁止额外字段；非法数值返回既有参数校验 422，修订冲突返回 409 AGENT_BUDGET_REVISION_CONFLICT。成功返回更新值与修订号；读写均 no-store。
Guest 返回既有 Owner 鉴权错误；登录、Token 撤销和 World 隔离复用既有认证，不允许指定其他 Owner/World。无新增幂等键，CAS 冲突应重读确认，不盲目覆盖。

默认值仍为 8；null 明确表示不限，不等于缺失配置或缺失快照。数字技术边界为 JavaScript 最大安全整数，数据库使用 BigInteger 保存；不是任务推荐次数。旧整数请求仍有效；旧客户端不支持 nullable 值，必须随服务端更新，不能混用旧前端。AGENT_DECISION_CEILING 退役，不再裁剪配置或新任务；已有配置和任务快照不改写。时间/费用硬封顶仍未实现。
用户消息创建事务同时创建 workflow_budgets；同一 chain 的所有角色共享快照，新用户消息才创建新预算。现有 client_message_id 幂等重发返回原链，不续额。模型与 Guest 不可修改快照。
每个模型决策前短事务检查 decision_limit 为 null 或 used_decisions<decision_limit，再原子增加共享计数和 execution.decision_count；同一 execution/决策序号仅为数据库重试幂等，不授权重放模型请求。失败、取消后已消费额度不退款；数据库异常在模型请求前终止，已提交的计数保守保留。
同一 execution 仍由原所有者任务执行；并发不同 execution 共享 SQL 条件更新，不能靠进程锁或事后汇总保证限额。未消费到额度的角色 decision_budget 停止，无模型请求。
缺少快照的旧链不补造授权，也不自动恢复。消息链统计不冒充精确 HTTP 请求或费用统计，不把群聊单个角色结束当成整条任务完成。
无用户正文、Key、原始参数新增到日志；预算没有新的 Trace 身份。原停止原因和工具提交语义见[预算领域](../../internal/agent-budget.md)。

不限模式持续记录决策和厂商用量，仍检查生成状态、用户停止、权限及工具执行条件；不会因决策累计次数停止。不新增费用或时间边界，不自动恢复，不增加模型收尾请求。
