# 工作区、命令与仓库能力阶段记录

状态：E0、W1a/W1b/W1c 已完成；Repository/Git/worktree 与群聊工作区为未实施方向。整理日期：2026-09-16。

[历史原文](../archive/plan/agent-repository-workspaces-v1.md)保留当时设计及验收。当前行为只以以下领域文档为准，旧分层确认/提交顺序不再作为维护任务门槛。

| 已完成内容 | 当前契约 |
|---|---|
| 持久 execution、单聊与群聊串行执行身份 | [Agent 运行时](../protocol/internal/agent-runtime.md) |
| World 工作区登记、单聊绑定、文件操作和执行租用 | [工作区](../protocol/public/rest/workspaces.md) |
| 固定结构化命令与进程清理 | [命令执行](../protocol/internal/workspace-commands.md) |
| Owner 逐次 Shell 审批 | [Shell 审批](../protocol/public/messaging/shell-approvals.md) |
| 后续加入的搜索、局部/批量编辑与私有 diff | [搜索读取](../protocol/internal/workspace-search-read.md)、[工具详情](../protocol/public/messaging/tool-details.md) |

<a id="w1b-实现与验证2026-09-08"></a>
<a id="w1c-实现与验证2026-09-10"></a>

## 验证入口

命令/Shell、World 切换和真实场景的现行入口见[测试指南](../testing/README.md)。Windows Shell 的原生实机覆盖仍有缺口；Linux 结果不能作为 Windows 通过证据。

## 未实施方向

旧 W2a/W2b/W3 包含 Repository Binding、Git 只读工具、execution worktree、群聊工作区和聊天标题的绑定交互。原生搜索已在工具可靠性阶段提前实现，不再视为等待 W2b 的功能。

这些设想可在相关需求中复用；新任务需按当前执行/权限模型核对实际依赖，不自动要求先完成所有仓库阶段才能改动 Agent。旧计划的深色界面、工具集合和预算数值已被后续实现更新；当前界面见[工作台设计](../design/frontend-navigation-theme.md)。

后续按[重排建议](README.md#后续执行顺序建议)将绑定界面、群聊授权、Repository/Git 和并行写入隔离拆开；只保留各项实际需要的依赖。
