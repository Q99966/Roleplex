# T4.3c 自定义与不限决策次数验收

- 日期：2026-09-16。
- 状态：实现与验证完成，用户已确认并授权提交。
- 范围与语义以 [T4 计划](../plan/tool-reliability-capability-roadmap-v1.md)、[预算 v2](../protocol/public/rest/agent-budget.md)、[内部循环契约](../protocol/internal/agent-budget.md) 为准。

## 确定性与迁移

在 backend 执行：

```bash
python -m pytest tests/test_agent_decision_budget.py tests/test_workflow_budget.py tests/test_unlimited_migration.py -q --tb=short --show-capture=no
python scripts/check_migrations.py
python -m pytest -q --tb=short --show-capture=no
```

专项 29 passed（16.57 秒）；后续补齐超过256的降级保护后，迁移专项复验 1 passed（3.20 秒），不与前次相加。覆盖有限 257 次后停止、不限实际执行 260 次工具并在第 261 次决策正常结束、取消与权限拒绝、并发共享计数、幂等消费、缺失/错属快照、Owner/Guest、CAS 冲突及正整数技术边界。

迁移 0017：SQLite upgrade/downgrade、ORM metadata、外键核对通过，PostgreSQL 离线 SQL 编译通过（641 行）；没有真实 PostgreSQL 完整重放。另在旧版数据库插入配置，验证升级保留旧值，降级遇不限或超过256的值拒绝且不改写数据；恢复旧版可用值后才能降级。

不限使用当前锁定框架的无穷停止阈值，受控执行跨过旧上限，不以放大有限常数代替。未来升级框架必须重跑长循环和取消边界测试。

全量回归：460 passed、3 skipped、18 deselected（367.01 秒）。后续新增权限参数化、缺失快照断言和降级保护由上述专项补验，不将专项数与全量相加。

## 浏览器

在 frontend 执行：

```bash
npm run test:e2e:commands -- agent-budget-settings.spec.ts budget-proposals.spec.ts
npm run build
```

普通浏览器专项 3 passed（15.4 秒），覆盖配置冲突、自定义512、切换不限、刷新持久化、运行中修改默认不改变任务快照，以及最后工具与文本的停止边界。
不限模式输入框占位调整后，设置专项复验 1 passed（8.1 秒），前端构建通过。
截图：logs/tests/e2e/fake/2026-09-16/14-31-46_e607c0dc/artifacts/screenshots/__73e5ae98_0_0.png。

## 标准真实 World

使用用户授权的中转站配置，由独立命令执行；凭据仅通过进程环境进入后端加密播种，未进入源码、浏览器测试参数或报告。测试关闭截图/trace/video，仅保存白名单结果。

```bash
npm run test:e2e:real-world -- workflow-budget-provider.spec.ts
```

结果：3 passed（22.8 秒），实际模型 deepseek-flash。共5次厂商调用，厂商报告输出603 Token；不估算费用，不将这组短任务称为真实模型数百轮验证。

| 会话 | 实际请求 | 结果 | 厂商输出 Token |
|---|---:|---|---:|
| 真实预算验收：额度不足（1 次） | 1 | 一角色正常结束，另一角色准确标决策停止 | 30 |
| 真实预算验收：自定义 512 次 | 2 | 两角色正常结束 | 82 |
| 真实预算验收：不限次数 | 2 | 两角色正常结束 | 491 |

保留 World：data/roleplex-real-world-e2e-20260916142947/default。
账号：realtest20260916142947；密码沿用测试指南的固定无价值占位值。
报告：logs/tests/e2e/real/2026-09-16/14-29-47_df917a3f/，三个 diagnostics 均 passed=true、cleanup_passed=true。
测试所启动服务已由专用流程清理，不复制到正式 worlds，也不另造登记机制。
