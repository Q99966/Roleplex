# 世界类型与桌宠协调验收

日期：2026-09-23。范围为[前期工作 P1–P4](../plan/world-orchestrator-foundation-v1.md)。领域行为见[类型协议](../protocol/public/rest/world-types.md)、[世界协调协议](../protocol/public/rest/world-orchestrator.md)，运行入口和受控数据管理见[测试指南](README.md#世界类型与桌宠协调p1p4)。

## 已证明的行为

- World 类型贯通 manifest/WorldInfo、界面创建、CLI/包装器；旧格式普通世界解释为 general，显式未知类型/版本拒绝且不降格，已有非空数据库缺 manifest 不自动改名补建。配置/初始化通过真实 API 与受控类型页面操作，切换/重启后不重复创建资源。
- 桌宠绑定真实服务端任命并发出模型请求，协调会话独立于主聊天；换绑保留原作者/历史并撤销旧 grant，Guest 和普通角色会话不能探测岗位历史。换绑后仍能压缩同一岗位上下文并采用摘要。
- 两群委派与类型活动真实运行；类型工具收到实际 execution、世界任务和根额度身份。群保持不同 chain，最后一个根决策额度的并发竞争只有一个成功，用量从原调用去重聚合。
- 幂等委派、补充同一任务、精确子任务停止、整棵树停止、解绑撤权、群局部重规划和重启中断均保留准确关系。独立群消息不因停止世界任务被取消；重规划复用原链，不重复计量或代签人工确认。
- 世界记忆有来源、版本和停用/恢复；源修订后连历史版本也隐藏旧正文。已读世界条目/群结果在权限或版本失效后阻止下一次模型调用，私有压缩沿同一验证路径。检索不自动包含后来新增的群或人格全部私人会话。
- 浏览器可从桌宠完成对话、上下文、任务图、结果/反馈、用量、世界记忆、群流程钻取并返回原选择。窄屏可补充要求、刷新历史和停止；网络丢失已接受请求的响应后，收起/重开/重发仍核对同一 client_message_id，不重复创建任务或复活已结束的 generation。

## 实际运行

所有 Provider 都为确定性 fake；浏览器使用真实前后端与独立 SQLite/World，没有调用收费模型。

| 验证 | 结果 |
|---|---|
| 世界类型/任务/任命/Memory、World/创建/运行操作、预算/用量、上下文执行 13 个后端文件 | 83 项通过 |
| 新来源/恢复/停止边界及原压缩、自动维护、ContextBuilder、WS、删除语义 10 个文件 | 48 项通过，含与上行重叠的用例 |
| 岗位换绑后的真实压缩、提示词及工具预览 | 11 项通过，含重叠用例 |
| 最终类型备份、子状态/重规划与来源权限复核 | 17 项通过，含重叠用例 |
| 桌宠与世界任务浏览器 | 3 项通过，包含响应丢失核对、窄屏、刷新和停止 |
| World 创建、类型页面/初始化与跨世界重启 | 2 项通过 |
| 原主聊天连接/历史窗口、断线快照与迟到翻页 | 14 项通过 |
| 0028–0031 迁移 | SQLite 升降级/外键/模型一致性通过；PostgreSQL 离线 SQL 1294 行通过 |
| TypeScript 与生产构建 | 通过；已有主包超过 500 kB 的构建提示仍存在 |

后端主回归：

```bash
# backend/
pytest tests/test_world_task_controls.py tests/test_world_tasks.py tests/test_world_orchestrator.py tests/test_world_types.py tests/test_world_memories.py tests/test_worlds.py tests/test_world_creation.py tests/test_world_runtime_operations.py tests/test_agent_decision_budget.py tests/test_execution_usage.py tests/test_workflow_budget.py tests/test_memory.py tests/test_context_execution.py -q
```

83 项为该轮运行时的数量，后续增加的解绑/恢复边界由 48 项回归覆盖，不把重叠结果直接相加。最终 17 项复核额外核对了受控类型备份解包后的 manifest/类型身份，及最后的子状态/来源处理。

浏览器报告位于 `logs/tests/e2e/fake/2026-09-23/13-41-40_a353b440`（协调 3 项）、`13-25-31_078e0cf4`（类型/创建 2 项）、`13-39-12_54f875e4`（连接/历史 14 项）。受控画布截图已在实现侧检查：[世界任务图](../../logs/tests/e2e/fake/2026-09-23/13-41-40_a353b440/artifacts/screenshots/__713680c3_0_0.png)、[协调对话与上下文](../../logs/tests/e2e/fake/2026-09-23/13-41-40_a353b440/artifacts/screenshots/__91370b1d_0_0.png)。截图、报告和测试 World 按既有策略保留，未提交或公开发布，不替代用户验收。

本轮先后发现并修复：生命周期复用旧异步锁/事件；停止按钮消失后的 Esc 失效；重规划错误采用原运行 revision；丢失响应后重发的请求身份和已结束状态恢复。历史浏览器测试的一项错误假设也已修正：REST 与 WS 完整信封字节开销不同，因此以真实 snapshot 消息 ID 核对，不要求两种窗口消息数相等。失败路径均已针对性重跑。

## 覆盖限制

运行环境为 WSL2/Linux、Python 3.12、Node 24、Chromium 和隔离 SQLite。PostgreSQL 只有离线 SQL，没有实例验证；没有 Windows 原生或移动设备实机证据。fake 验收证明授权、关联和运行机制，不证明真实模型的规划/摘要语义质量或精确 Token 计量。

类型活动首版由已有群工作流执行；不是任意后台活动平台。生产仅内置 general，测试 fixture 不成为用户产品选项；狼人杀业务、Skills/MCP、知识图谱、全世界自动记忆提炼仍由后续独立任务实现。类型分支还需完成自己的完整业务验收。
