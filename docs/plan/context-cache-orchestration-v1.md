# 上下文、缓存观测与编排阶段记录

状态：C0–C2 与 M4a 已完成；并行编排、Checkpoint、Memory 和导出/导入尚未实施。整理日期：2026-09-16。

[历史原文](../archive/plan/context-cache-orchestration-v1.md)保留阶段设计与取舍；[外部缓存参考](../design/cache-v1.md)用于理解来源。旧排期不自动成为新需求的前置条件。

## 已完成范围

统一 ContextBuilder、角色历史投影、稳定前缀与上下文预算、厂商缓存 usage 观测，以及 @ 角色的串行群聊已落地。后续又加入决策配置、用量持久化和中断事实交接，旧计划不再维护这些实现细节。

- 上下文与调度：[Agent 运行时](../protocol/internal/agent-runtime.md)。
- 当前消息与群聊行为：[消息协议](../protocol/public/messaging/messages.md)。
- 用量与缓存统计：[角色用量](../protocol/public/rest/execution-usage.md)、[日志观测](../protocol/internal/observability.md)。
- 中断后的事实来源：[中断上下文](../protocol/internal/interruption-context.md)。
- 可重复验证：[测试指南](../testing/README.md)。

## 待选设计

历史 M4b（Orchestrator）、C3（Checkpoint）、C4（扩展缓存基准）、Memory、导出/导入与世界分发方案保留供后续需求评估。现有缓存 usage 验证不表示 Checkpoint 或长期记忆已实现。

“导出必须等编排/Checkpoint 全部完成”“所有代码协作必须先做 worktree”等曾用于当时排期，不是永久依赖。新功能按实际数据、兼容性和授权需求确定方案；本次整理不自动启动这些能力。

后续顺序以[重排建议与实际依赖](README.md#后续执行顺序建议)为入口：文本编排与仓库能力分开，导出和 Checkpoint 可按需求提前。

首版已实现[会话内可操作工作流图](conversation-workflows-v1.md)：先支持用户明确编排的串行节点及可操作运行，再按需求增加自动分工。它复用现有 ContextBuilder 和执行身份，并补齐节点/尝试的结果交接；不将旧 M4b 全部设计当作前置，也不假定当前“最近本角色中断回复”的投影已经覆盖节点重试。
