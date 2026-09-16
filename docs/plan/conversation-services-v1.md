# 会话后台服务阶段记录

状态：Linux 首版、Shell 共存和协调回收已完成；Windows 后台服务未开放。整理日期：2026-09-16。

[历史原文](../archive/plan/conversation-services-v1.md)保留 W1d 的方案和收尾记录，其中阶段性“未完成验收”描述不是当前状态。

当前支持逐次审批启动服务、就绪检查、`/ps` 面板、状态/日志/停止、配额，以及会话/工作区/World 操作前协调回收。服务发现与原生修改拒绝诊断后续已补充；权限、生命周期和默认值只在[运行实例协议](../protocol/public/messaging/runtime-services.md)维护。

Shell 仍为一次调用结束即清理的能力；后台服务使用独立生命周期。现行审批和文件写入规则见[Shell 审批](../protocol/public/messaging/shell-approvals.md)、[工作区协议](../protocol/public/rest/workspaces.md)。World 操作见[World 协议](../protocol/public/rest/worlds.md)。

验证入口与平台缺口见[服务验证记录](../testing/runtime-services.md)及[测试指南](../testing/README.md)。原 S0–S3 顺序、独立提交、必须先完成 W1d 再进入 W2a 等为历史交付安排，不自动适用于新任务。
