# T4.2 准确停止与结果交接验证

- 状态：实现及离线/真实验证完成，用户已确认并授权提交；复核日期：2026-09-15。
- 范围：[T4 计划](../plan/tool-reliability-capability-roadmap-v1.md#85-小阶段与退出条件)。
- 权威契约：[内部预算](../protocol/internal/agent-budget.md)、[消息停止原因](../protocol/public/messaging/messages.md)。

## 实现与边界

直接计数模型决策，在下一请求前停止；最后一轮工具仍按既有权限执行并交接。默认 8 是沿用既有模型请求量级的内部过渡值，Owner 配置、最终默认、资源/时间预算与恢复均未提前实现。
默认图保护给当前拓扑预留交接空间；内部显式低图限制仍可验证底层保护。修复 15 图步数最后纯文本被错误改标的情况，正常响应与直接返回工具正常结束。
新增 decision_budget 与原 graph_budget 区分；数据库使用原 JSON 状态，前端显示简短原因、历史保持准确停止标记，不增加系统统计摘要。
响应明确截断、内容过滤或工具解析异常时，在工具执行任务内先检查；不能靠异步事件消费者抢在工具前阻止副作用。Provider 调用返回并不等于响应完整，usage 不估算。

## 确定性验证

在 `backend` 执行：

```bash
python -m pytest -q tests/test_agent_decision_budget.py tests/test_agent_budget_accounting.py tests/test_execution_summary.py --tb=short --show-capture=no
python -m pytest -q --tb=short --show-capture=no
```

先观察新增六项决策预算测试失败，再实现后通过。专项覆盖 1/2/8/32 决策、同响应多个工具、最后文本/直接返回工具、SDK 重试、显式截断、权限拒绝计数、等待中取消、参数边界、部分提交与未知结果、历史刷新与终态保护。

后端全量：**422 passed、3 skipped、18 deselected（281.94 秒）**。真实契约默认排除，不将离线替身用量当作厂商数据。全量后增加重复工具身份的执行前拒绝回归，决策预算专项 **18 passed（0.64 秒）**，不与全量相加。

## 浏览器

在 `frontend` 执行：

```bash
npm run test:e2e:commands -- budget-proposals.spec.ts execution-summary.spec.ts
npm run build
```

真实前后端/fake Provider 三项通过（14.3 秒），构建通过。八次决策的持续写入场景中，前七次各一文件、最后一次两个文件，共九文件落地后停止；最终文本场景七次工具加最后文本正常结束。
核对磁盘、工具记录、无第九次请求、刷新后停止原因；断线与 Guest 私有详情边界回归通过。测试沿用可视范围懒加载规则，先滚动最后卡片再检查详情；各用例角色名隔离。报告：`logs/tests/e2e/fake/2026-09-15/15-21-42_e325e1cc/`，已检查最后两项文件均提交的截图。

## 独立真实 Provider

专用命令（会联网并消耗额度，普通 CI 不运行）：

```bash
python -m pytest tests/contract/test_tool_reliability_real.py -m contract -k 'openai_compatible and real_last_decision_terminal' -q -s --tb=short --show-capture=no
```

本轮显式使用已授权 Token Rhythm 配置，模型 deepseek-flash，不切换官方 Key；正文与 Key 不进入观察报告。
用 decision_limit=1 验证实际文件提交后 decision_budget，以及纯文本 completed；两者都必须只有一次框架模型调用，不请求额外总结。

本轮 **2 passed、8 deselected（4.25 秒）**：工具场景一次请求、一次工具完成，独立磁盘核对通过，decision_budget，厂商报告输出 43 Token；纯文本场景一次请求、零工具、completed，输出 95 Token。没有使用官方 Key。
每例只保留隔离目录下 `last-decision-observation.json` 的固定布尔/计数字段；本轮关联测试库 `data/roleplex-test-20260915152517.db`。此小请求不证明复杂任务默认额度已足够。

## 人工验收

确认正常回答不再错误显示预算停止；长工具循环显示“达到本轮决策上限”，最后工具卡保留实际结果。当前不显示自动恢复入口，不承诺模型已分析尚未请求模型解读的最后工具结果。
