# 工具可靠性阶段结果与能力方向

状态：此前约定的 T 收尾已完成。整理日期：2026-09-16。

[历史原文](../archive/plan/tool-reliability-capability-roadmap-v1.md)保留问题取证、逐阶段设计、修订与验收。原文多处“当前剩余”“后续确认”已经被完成记录取代，本页不作为自动续开的任务队列。

## 已完成结果

| 范围 | 当前契约 | 验证记录 |
|---|---|---|
| T0/T1 工具取证、准确停止与执行事实 | [执行事实](../protocol/internal/execution-facts.md) | [T0](../testing/tool-reliability-t0.md)、[T1](../testing/tool-reliability-t1.md) |
| T2 搜索与范围读取 | [搜索读取](../protocol/internal/workspace-search-read.md) | [T2](../testing/tool-reliability-t2.md) |
| 自动父目录、写入等待、预算未派发事实 | [工作区](../protocol/public/rest/workspaces.md)、[执行事实](../protocol/internal/execution-facts.md) | [父目录](../testing/workspace-write-parents.md)、[等待](../testing/workspace-write-wait.md)、[停止事实](../testing/budget-stop-facts.md) |
| T3 同文件多片段编辑 | [工作区](../protocol/public/rest/workspaces.md) | [T3](../testing/tool-reliability-t3.md) |
| T4 决策计数、准确停止、共享配置与不限模式 | [公开预算](../protocol/public/rest/agent-budget.md)、[内部计数](../protocol/internal/agent-budget.md) | [T4.1](../testing/agent-budget-t41.md)、[T4.2](../testing/agent-budget-t42.md)、[T4.3a](../testing/agent-budget-t43a.md)、[T4.3c](../testing/agent-budget-t43c.md) |
| 参数错误恢复与工具说明整理 | [Agent 运行时](../protocol/internal/agent-runtime.md) | [参数恢复](../testing/tool-argument-recovery.md)、[说明整理](../testing/workspace-tool-guidance.md) |
| 角色执行用量 | [用量](../protocol/public/rest/execution-usage.md) | [用量验证](../testing/role-execution-usage.md) |
| 取消批量修改固定项数/整批参数限制 | [工作区](../protocol/public/rest/workspaces.md)、[工具详情](../protocol/public/messaging/tool-details.md) | [批次限制调整](../testing/batch-mutation-limits.md) |
| T4.4 中断事实保存与按需交接 | [中断上下文](../protocol/internal/interruption-context.md) | [中断验证](../testing/interruption-context.md) |

<a id="六t2搜索定位与范围读取含实际预算与有界排队"></a>
<a id="七t3一个文件节点支持多个精确替换"></a>
<a id="八t4授权资源内持续执行准确暂停与继续"></a>
<a id="85-小阶段与退出条件"></a>
<a id="119-小阶段-3-实施定稿"></a>

## 旧设计的替代关系

- 默认图 15 步、最多 256 决策不再代表产品额度；当前配置、共享扣减和不限语义以预算协议为准。
- 批量修改不再受旧 8 项/整批 256 KiB 限制；展示降级与实际提交结果分开。其他读取/单文件限制没有因此自动取消。
- 中断恢复已收敛为保存、核对和交接事实。新请求不靠“继续”关键词识别，不新增恢复按钮，不自动续跑或重置旧 chain。
- 时间/审批总预算、Token/费用限制、全面资源基准和完整任务系统未被用作已完成切片的前置条件。
- 每轮模型总结、缺少事实的额外收尾请求，以及逐切片人工批准/独立提交顺序不再作为当前开发要求。

## 待选能力方向

历史十二类能力库涉及 Git/仓库、编排、跨会话协作、长期记忆、浏览器操作和更多交付流程；原方案可供相关需求参考。它们不是当前已暴露的工具，也不因列入路线图而自动成为本次任务。

后续按用户需求选择目标和可验证结果，复用现有权限、执行身份与事实来源；避免为旧编号补齐用户未要求的预算、恢复界面或调度系统。现行测试选择见[测试指南](../testing/README.md)。
