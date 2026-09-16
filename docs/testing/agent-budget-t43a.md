# T4.3a 配置与共享决策预算

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 状态：实现与验证完成，用户已确认当前版本并授权提交；日期：2026-09-15。
- 本轮仅覆盖 T4.3 的配置/共享额度切片，不冒充完整资源预算；时间截止、审批计时和最终默认基准另步实施。
- 权威接口：[World 预算配置](../protocol/public/rest/agent-budget.md)。

## 范围

Owner 在“系统与环境设置 → 运行世界与存储”配置每个新任务的共享决策上限；默认仍为 8 的过渡值，可选 1..部署 ceiling。允许用户自行选择更高额度，但不宣称某个快捷档位已是复杂任务的最佳默认。
消息创建事务冻结 chain 快照；同一消息触发的群聊角色共用，不分别发一份额度。模型请求前以短事务原子扣减并记录 execution 序号，不逐 Token 写库。
配置 CAS、防越部署上限、旧快照不续额、消息幂等不补额和取消优先有验证。缺失快照的旧任务不偷偷发新额度，不提供自动恢复。

## 确定性验证

在 `backend` 执行：

```bash
python -m pytest -q tests/test_workflow_budget.py tests/test_agent_budget_accounting.py --tb=short --show-capture=no
python scripts/check_migrations.py
python -m pytest -q --tb=short --show-capture=no
```

专项覆盖：Owner/Guest、严格整数和部署 ceiling、并发配置 CAS、20 个不同 execution 争用 5 个共享额度、重试同决策不重复扣减、修改默认不影响冻结值、群聊两角色共享一个额度、client_message_id 重发不续额、已请求停止不扣减。
迁移 0014 已通过 SQLite upgrade、metadata 比较、外键检查、downgrade 和 PostgreSQL 离线编译（585 行）。没有运行真实 PostgreSQL 服务上的重放，不把离线检查称为完整数据库验证。

后端全量 **427 passed、3 skipped、22 deselected（283.91 秒）**，其中普通回归不运行真实契约。

## 浏览器

在 `frontend` 执行：

```bash
npm run test:e2e:commands -- agent-budget-settings.spec.ts budget-proposals.spec.ts
npm run build
```

3 passed（14.0 秒），构建通过。界面配置/CAS 冲突重读、12 次额度下完成 11 次决策、运行时默认降至 1 仍按原快照完成、刷新保持结果；既有最后工具/最终文本终态回归通过。
报告：`logs/tests/e2e/fake/2026-09-15/15-40-13_4be6e3f4/`。截图已检查，沿用浅色组件，不新增主题切换。
11 次决策是确定性功能测试，不将它当成自然任务默认值的实测依据。

## 真实 World 浏览器验收

按用户要求沿用既有真实 World 入口，临时库契约测试不能替代此层。此前临时库群聊两项通过（10.30 秒）仅保留为历史补充，其重复计费测试入口已由本节用例替代。

在 `frontend` 执行专用命令（联网计费，普通 CI 不运行；执行前已告知）：

```bash
npm run test:e2e:real-world -- workflow-budget-provider.spec.ts
```

使用 Token Rhythm / deepseek-flash，正常 World 包装器，health.world_managed=true。通过浏览器登录、设置共享额度、@全部 发送两角色群聊，再刷新检查状态和同 chain 关系；不使用 fake Provider、不切换官方 Key。关闭截图/trace/video，模型正文留在 World 数据库供 Owner 查看，报告只保存白名单统计。

本轮 **2 passed（18.2 秒）**：

| 会话 | 冻结额度 | 实际决策 | 角色终态 | 厂商报告输出 Token |
|---|---|---|---|---|
| 真实预算验收：额度不足（1 次） | 1 | 1 | done / stopped（decision_budget） | 29 |
| 真实预算验收：额度充足（4 次） | 4 | 2 | done / done | 136 |

World：`data/roleplex-real-world-e2e-20260915155754/default/`。
账号：`realtest20260915155754`；密码沿用测试指南固定占位值 `Roleplex-Real-E2E-1`。
报告：`logs/tests/e2e/real/2026-09-15/15-57-54_82cf4652/`。
独立只读数据库核对两组预算与消息终态一致，活跃 generation 为 0，World 租约已释放，测试服务已清理。目录按原规则保留，不复制到项目 worlds 根或增加登记机制。

本例证明真实 World 群聊共享预算和界面路径，不证明复杂任务默认额度、费用或时间硬上限。
