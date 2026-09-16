# 服务发现与修改拒绝诊断阶段记录

状态：S1/S2 已完成；S3 运行中原生编辑未实施。整理日期：2026-09-16。

[历史原文](../archive/plan/service-discovery-diagnostics-v1.md)保留问题背景和方案；其中“当前没有服务列表入口”等描述针对实施前状态。

S1 为已授权角色增加本会话服务列表查询；S2 为原生写入/编辑提供类型化拒绝原因与允许的下一步建议，跨会话只返回汇总。查询权限不自动授予文件或进程控制权限。

当前行为见[运行实例](../protocol/public/messaging/runtime-services.md)、[工作区](../protocol/public/rest/workspaces.md)与[私有详情](../protocol/public/messaging/tool-details.md)。回归入口为 `test_service_discovery.py`、`test_write_diagnostics.py` 和 commands E2E 对应场景，命令见[测试指南](../testing/README.md)。

S3 是后续候选设计，当前原生写入仍有服务占用保护。新需求若涉及运行中编辑，应解决实际并发与生命周期问题；历史的单独阶段编号本身不构成额外审批流程。
