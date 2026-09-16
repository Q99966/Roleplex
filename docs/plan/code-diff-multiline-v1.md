# 多行输入、diff 与批量文件阶段记录

状态：M/G/D/E1/E2 已完成，后续 T3 与批次限制调整已覆盖旧设计。整理日期：2026-09-16。

[历史原文](../archive/plan/code-diff-multiline-v1.md)保存当时的候选选型、逐切片验收和实现记录。

| 结果 | 当前说明与验证 |
|---|---|
| 多行输入、失败草稿保护、保留正文空白 | [消息协议](../protocol/public/messaging/messages.md)；`multiline-composer.spec.ts` |
| 共同执行授权 | [Agent 运行时](../protocol/internal/agent-runtime.md)、[G 验证](../testing/tool-execution-g.md) |
| 原生 write/edit 差异与探索归组 | [工具详情](../protocol/public/messaging/tool-details.md) |
| 局部编辑、同文件多片段与批量修改 | [工作区协议](../protocol/public/rest/workspaces.md)、[T3 验证](../testing/tool-reliability-t3.md) |
| 统一批量/单项读取 | [搜索读取契约](../protocol/internal/workspace-search-read.md) |

旧批次最多 8 文件/整批 256 KiB 已被[批次限制调整](../testing/batch-mutation-limits.md)取代；不能将旧计划数字重新引入修改工具。读取与单文件限制仍按各自协议执行。旧独立深色 diff 方案已由[现行工作台设计](../design/frontend-navigation-theme.md)取代。

后续改动按受影响行为选择测试，不重走 M/G/D/E 的历史确认顺序。Shell/服务不采集原生文件 diff、跨文件非事务、未知结果不重放等当前语义以领域协议为准。
