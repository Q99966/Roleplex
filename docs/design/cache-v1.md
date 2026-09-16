# Prompt Cache 外部参考设计

状态：外部参考，原文已归档。整理日期：2026-09-16。

[参考原文](../archive/design/cache-v1.md)来自早期产品讨论，包含跨会话 Shared Memory、FTS5、sqlite-vec、Embedding 等假设，并不描述 Roleplex 当前可用功能，也不要求后续任务采用这些存储或检索技术。

Roleplex 已实现的上下文构建和缓存统计见[Agent 运行时](../protocol/internal/agent-runtime.md)、[角色用量](../protocol/public/rest/execution-usage.md)与[日志观测](../protocol/internal/observability.md)。阶段结果和未实施方向见[上下文与编排记录](../plan/context-cache-orchestration-v1.md)。

评估新的缓存/记忆需求时可引用原文的具体思路，但需按当前数据、权限与 Provider 事实选取，不把整份参考方案当作当前架构约束。
