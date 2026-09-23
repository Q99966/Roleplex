# Roleplex 文档导航

复核日期：2026-09-23。本入口帮助区分当前说明、接口契约、设计和历史记录。

| 要了解的内容 | 权威入口 |
|---|---|
| 项目现在能做什么、安装启动、配置与存档 | [项目 README](../README.md) |
| 开发工作方式、安全与验证要求 | [AGENTS.md](../AGENTS.md) |
| API、消息、WebSocket、错误码与内部数据/执行契约 | [协议索引](protocol.md) |
| 测试命令、端口、隔离数据、账号与排障 | [测试指南](testing/README.md) |
| 主动/自动压缩、摘要版本与历史检索回读 | [压缩协议](protocol/public/rest/context-compression.md)、[Memory](protocol/public/rest/memory.md)、[C 批验收](testing/context-compaction-memory.md)、[D 批验收](testing/context-auto-compaction.md) |
| 持久会话上下文、来源与按角色占用 | [上下文协议](protocol/public/rest/conversation-context.md)、[B 批验收](testing/conversation-context.md) |
| 平台/世界/角色/会话提示词配置与生效来源 | [提示词协议](protocol/public/rest/prompt-settings.md)、[A 批验收](testing/prompt-settings.md) |
| 当前日志结构与持久化设计 | [日志 v2](design/logging-v2.md) |
| 当前工作台侧栏、配色与详情交互设计 | [工作台设计](design/frontend-navigation-theme.md) |
| 会话工作流的范围与验收记录 | [可操作工作流图](plan/conversation-workflows-v1.md)、[接口契约](protocol/public/rest/workflows.md) |
| 群协调基础实现与当前缺口 | [基础范围与记录](plan/orchestrator-parallel-loops-v1.md) |
| 图管理、工具授权与运行重规划的实施范围及验收 | [实施范围](plan/orchestrator-graph-control-v1.md)、[本次验收](testing/orchestrator-graph-control.md) |
| 节点反馈、图可读性与操作面板 | [分批记录](plan/workflow-feedback-usability-v1.md)、[反馈验收](testing/workflow-feedback.md)、[可读性验收](testing/workflow-readability.md)、[操作面板验收](testing/workflow-workbench.md) |
| 会话上下文、提示词、检索及 Skills/MCP 总体计划（A–D 已实现，E–F 待实施） | [总体计划与实施顺序](plan/conversation-context-memory-capabilities-v1.md)、[既有上下文阶段](plan/context-cache-orchestration-v1.md) |
| 世界类型、桌宠与世界协调（已接入） | [实现记录](plan/world-orchestrator-foundation-v1.md)、[另一 worktree 开工交接](plan/world-type-worktree-handoff-v1.md)、[验收](testing/world-orchestrator-foundation.md) |
| 固定世界管理者、消息署名、收件人与任务广播（已修复） | [修复记录](plan/world-coordination-message-provenance-v1.md)、[协作通信协议](protocol/public/rest/workflow-communication.md)、[本次验收](testing/workflow-communication.md) |
| 旧里程碑的结果、未实施方向与历史原文 | [计划索引](plan/README.md)、[归档说明](archive/README.md) |

## 阅读与维护

当前行为由对应领域协议说明，代码和测试用于核对；设计解释取舍，不重复维护完整字段。文档与实现不一致时应查明是说明过期还是实现缺陷，不能仅按文件日期判断，也不能把尚无实现的计划当作可用接口。

计划状态分为本次任务采用的实施计划、已完成记录和待选方向。进入新任务时，只采用与用户要求相关的范围；历史排期和验收步骤不自动成为新任务的前置条件。已完成记录转为简短的结果与入口，原设计放入 `archive/`，保留原路径供旧链接使用。后续新增计划应明确目标、未解决的问题、采用范围与退出条件，不持续向旧计划追加“当前任务”。

`testing/` 中的专项记录证明记录日期、环境和命令下的结果，不代表每次改动都重跑过，也不自动要求未来任务运行相同的收费测试。当前命令选择以测试指南为准。

## 本次整理的主要纠偏

| 原有问题 | 当前处理 |
|---|---|
| 旧 P/M/W/T 阶段顺序、逐阶段确认与独立提交要求被视为永久规则 | 历史计划归档；[执行顺序建议](plan/README.md#后续执行顺序建议)按实际依赖重排，导出、文本编排、仓库能力和上下文容量可分别安排。 |
| README 同时承载产品说明、测试指南和大量验收流水 | README 保留使用说明；测试方式和历史结果通过入口引用。 |
| Guest 邀请、MCP 产品接入、Git/worktree 等规划与已实现能力混写 | README 和协议索引分别标注当前能力、原型与未实现内容。 |
| 旧批次 8 项/256 KiB、决策 256 上限、深色 UI 等被后续实现取代 | 旧数值留在有日期的归档中；当前限制与界面以领域协议和现行设计为准。 |
| PostgreSQL 离线 SQL 被描述成完整数据库运行验证 | 明确 SQLite 实跑与 PostgreSQL 离线编译的证据边界。 |
| 任何可见改动都被要求新增 E2E、人工验收及真实 World 验证 | 按变更风险选择验证层级；收费测试以本次授权和验证目标为依据。 |
| 协议总则要求并不存在的统一 wire 版本、内部预算仍写“无持久化/无配置” | 对齐现行 `/api`、WS 能力声明、消息链预算与参数恢复行为。 |

本次只整理文档，没有重新执行历史功能验收，也没有变更产品权限或数据库结构。
