# T3 多片段编辑验证

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

- 状态：实现与自动化/真实 Provider 验证完成，2026-09-15 用户确认当前版本并授权提交。
- 复核日期：2026-09-15。
- 权威范围：[T3 计划](../plan/tool-reliability-capability-roadmap-v1.md#七t3一个文件节点支持多个精确替换)、[工具契约](../protocol/public/rest/workspaces.md#t3-多片段编辑)。

## 覆盖与复跑

在 `backend` 运行：

```bash
python -m pytest -q tests/test_workspace_replacements.py tests/test_workspace_edit.py tests/test_workspace_batch_mutation.py tests/test_write_diff.py
```

覆盖同一原始版本、数组顺序独立、一次原子替换、第二/第三项失败不修改文件、依赖新文本拒绝、唯一匹配与重叠、Unicode/CRLF、相邻片段、no-op、整节点预算和最终文件上限；并验证版本竞争、等待时取消、32 项提交后的 diff 降级。真实工具工厂测试经过框架嵌套模型解析，避免仅直接测试文件服务而遗漏参数转换。跨文件预检、部分提交与旧接口由既有回归共同覆盖。

在 `frontend` 运行：

```bash
npm run test:e2e:commands -- replacements.spec.ts workspace-edit.spec.ts batch-mutation.spec.ts
npm run build
```

浏览器启动真实前后端并使用确定性 fake Provider。验证三处编辑的一份 diff、单文件/批次第二项失败序号、刷新后详情恢复、Guest 无私有详情权限。3 条浏览器用例通过；构建通过。当轮截图确认浅色卡片与当时的独立深色 diff，失败记录明确文件未提交。随后用户要求 diff 适配浅色背景，最新视觉约定见[主题设计](../design/frontend-navigation-theme.md)。配色调整后 write-diff/replacements 浏览器回归 2 passed，构建通过；截图检查通过，报告位于 `logs/tests/e2e/fake/2026-09-15/14-12-12_cac7964d/`。

本轮浏览器报告：`logs/tests/e2e/fake/2026-09-15/13-52-48_e960877d/`。截图与测试产物沿用测试指南的按轮保留策略，旧轮次可被清理。

后端全量回归：`python -m pytest -q`，**390 passed、3 skipped、14 deselected**（258.12 秒）；真实契约用例按既定配置不进入普通回归。随后补齐第三项失败和 32 项 diff 降级边界，并统一片段额度常量后，重跑 replacements/edit/batch 三组回归：**56 passed**（23.50 秒）。

## 独立真实 Provider

以下专用命令会联网并产生费用，不进入普通 CI；使用独立数据库，凭据只在后端加密播种，关闭 trace/video/截图。实际运行前已告知联网计费。

```bash
npm run test:e2e:real-world -- replacements-provider.spec.ts
```

本轮结果：**1 passed**。模型 `deepseek-v4-flash` 共 3 次 Provider 请求，厂商报告输出 709 Token；读取实际全文件 hash 后，以 1 次 workspace_edit 的 3 个 replacements 完成修改，生成 1 份 diff，正常结束。独立读取磁盘确认全部目标变化正确、任务提示中未给出的尾部标记保留，清理通过。

报告：`logs/tests/e2e/real/2026-09-15/13-54-14_4bb6d996/`。仅保存白名单数值/布尔观察，不保存源码、凭据或完整模型输出。

本次证明接口可用与结果正确，不证明复杂小游戏任务的 Token 降幅；未将单次观测作为异常 Token 已解决的证据。

## 人工验收

用启用 read/edit 的角色读取文件后修改三处，确认仅一份最终差异；再提供无法匹配或重叠的片段，确认对应序号和文件未提交提示。旧单片段方式继续可用。本轮用户已确认提交；执行上限与 W3 隔离不属于本次改动。
