# 上下文、缓存观测与编排阶段记录

状态：C0–C2、M4a、工作流及固定图协调/并行循环已有实现；协调者图管理与版本化重规划已接入，具体范围和验证见图管理阶段记录。[新总体计划](conversation-context-memory-capabilities-v1.md)的 A–C（提示词、持久上下文、主动压缩与检索）已实现；自动压缩和 Skills/MCP 仍待 D–F；世界 Orchestrator 与导出/导入独立安排。复核日期：2026-09-22。

[历史原文](../archive/plan/context-cache-orchestration-v1.md)保留阶段设计与取舍；[外部缓存参考](../design/cache-v1.md)用于理解来源。旧排期不自动成为新需求的前置条件。

## 已完成范围

统一 ContextBuilder、角色历史投影、稳定前缀与上下文预算、厂商缓存 usage 观测，以及 @ 角色的串行群聊已落地。后续又加入决策配置、用量持久化和中断事实交接，旧计划不再维护这些实现细节。

- 上下文与调度：[Agent 运行时](../protocol/internal/agent-runtime.md)。
- 当前消息与群聊行为：[消息协议](../protocol/public/messaging/messages.md)。
- 用量与缓存统计：[角色用量](../protocol/public/rest/execution-usage.md)、[日志观测](../protocol/internal/observability.md)。
- 中断后的事实来源：[中断上下文](../protocol/internal/interruption-context.md)。
- 可重复验证：[测试指南](../testing/README.md)。

## 本轮采用的后续范围

[会话上下文、提示词、主动检索与 Skills/MCP 总体计划](conversation-context-memory-capabilities-v1.md)替代旧 C3/Memory 候选排期，明确所有单聊/群聊区分完整消息与长期保存的会话上下文；前端提供各层提示词配置、占用、主动/自动压缩，Agent 通过工具检索和回读相关会话信息。Skills 与 MCP 按真实管理、加载/连接、调用及撤销闭环实施。

旧 C3 的来源边界、原消息保留和失败回退继续适用，但不再仅在超阈值时生成摘要；旧 Memory v0 的“仅检索 Owner 逐条确认记忆”和自动注入方式不作为本轮历史检索的限制。必要缓存一致性验证随各批完成，具体修改与实施顺序以新计划为准。A 已新增配置/来源 API 与 0024 迁移，见[协议](../protocol/public/rest/prompt-settings.md)和[验收](../testing/prompt-settings.md)。B 已新增持久会话材料、按角色占用与调用级输入观测，见[上下文协议](../protocol/public/rest/conversation-context.md)及[验收](../testing/conversation-context.md)。C 已接入[主动压缩](../protocol/public/rest/context-compression.md)及[Memory 检索回读](../protocol/public/rest/memory.md)，实际验证见[C 批记录](../testing/context-compaction-memory.md)；自动压缩继续按 D 实施。

## 其他待选设计

历史 M4b（Orchestrator）、C4 的扩展缓存基准、自动长期记忆提取、导出/导入与世界分发方案保留供后续需求评估。现有缓存 usage 验证不表示 Checkpoint、检索或长期记忆已实现。

“导出必须等编排/Checkpoint 全部完成”“所有代码协作必须先做 worktree”等曾用于当时排期，不是永久依赖。新功能按实际数据、兼容性和授权需求确定方案；本次整理不自动启动这些能力。

后续顺序以[重排建议与实际依赖](README.md#后续执行顺序建议)为入口：当前按新总体计划推进，导出与仓库能力仍可按实际需求独立安排。

首版已实现[会话内可操作工作流图](conversation-workflows-v1.md)，复用 ContextBuilder 和执行身份，补齐节点/尝试的结果交接。

已接入的群内任命、固定图分配/结果上报及并行循环见[基础记录](orchestrator-parallel-loops-v1.md)。当前以[图管理与重规划整改](orchestrator-graph-control-v1.md)为权威：协调执行可以早于 run 创建，模型获得目标图及明确编辑范围，运行重规划按准确图版本/激活/迭代组织上下文。不能继续把固定图上的 assignment 提交当作已完成图规划，也不扩大到其他群或世界级入口。
