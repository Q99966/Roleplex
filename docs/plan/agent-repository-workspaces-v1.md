# 工作区、命令与仓库能力阶段记录

状态：E0、W1a/W1b/W1c、会话工作区入口与群聊串行文件工具已完成；Repository/Git/worktree 尚未实现。复核日期：2026-09-17。

[历史原文](../archive/plan/agent-repository-workspaces-v1.md)保留当时设计及验收。当前行为只以以下领域文档为准，旧分层确认/提交顺序不再作为维护任务门槛。

| 已完成内容 | 当前契约 |
|---|---|
| 持久 execution、单聊与群聊串行执行身份 | [Agent 运行时](../protocol/internal/agent-runtime.md) |
| World 工作区登记、单聊/群聊绑定、文件操作和执行租用 | [工作区](../protocol/public/rest/workspaces.md) |
| 会话概览中的绑定/更换/解绑，群聊角色串行文件交接 | [会话绑定与群聊执行](../protocol/public/rest/workspaces.md#会话绑定) |
| 固定结构化命令与进程清理 | [命令执行](../protocol/internal/workspace-commands.md) |
| Owner 逐次 Shell 审批 | [Shell 审批](../protocol/public/messaging/shell-approvals.md) |
| 后续加入的搜索、局部/批量编辑与私有 diff | [搜索读取](../protocol/internal/workspace-search-read.md)、[工具详情](../protocol/public/messaging/tool-details.md) |

<a id="w1b-实现与验证2026-09-08"></a>
<a id="w1c-实现与验证2026-09-10"></a>

## 验证入口

命令/Shell、World 切换和真实场景的现行入口见[测试指南](../testing/README.md)。会话工作区与群聊文件已随 `53d70ab` 实现，对应后端 `test_group_workspaces.py` 和浏览器 `group-workspace.spec.ts`；本次文档复核没有重跑测试。Windows Shell 的原生实机覆盖仍有缺口；Linux 结果不能作为 Windows 通过证据。

## 未实施方向

旧 W2a/W2b/W3 中的 Repository Binding、Git 只读工具与 execution worktree 仍是未实施方向。原生搜索、群聊工作区和会话概览中的绑定交互已提前落地，不再作为等待这些旧阶段的功能；当前入口位置以现行界面为准，不要求另做聊天标题入口才能算完成。

这些设想可在相关需求中复用；新任务需按当前执行/权限模型核对实际依赖，不自动要求先完成所有仓库阶段才能改动 Agent。旧计划的深色界面、工具集合和预算数值已被后续实现更新；当前界面见[工作台设计](../design/frontend-navigation-theme.md)。

工作流首版已由[阶段记录](conversation-workflows-v1.md)承接现有能力，后续[基础实现](orchestrator-parallel-loops-v1.md)已接入共享读、修改操作互斥、固定图工具分配及并行循环。当前[图管理与重规划整改](orchestrator-graph-control-v1.md)复用这些能力，补齐协调者真正读取、修改和调整流程的工具；不重新把所有任务限制为串行。

Repository/Git/worktree 按[重排建议](README.md#后续执行顺序建议)独立安排，只在需要独立分支同时修改和合并时作为可选隔离方案。它们不再作为普通并行任务、并行读取、汇合或循环的前置。
