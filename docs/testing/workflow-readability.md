# 工作流图可读性验收

日期：2026-09-21。范围为[三批计划](../plan/workflow-feedback-usability-v1.md)的第二批。沿用第一批反馈能力，本次增加展示投影、布局、定位及兼容的展示信息保存；第三批的整体工具栏/唯一上下文面板整理仍待实施。

环境为 Linux、Python 3.12、Node 24、Chromium、SQLite 隔离数据库。没有引入新布局依赖，没有修改数据库表结构；模型相关回归全部使用确定性 fake Provider，本批未运行收费模型。

## 实际结果

| 入口（相应目录） | 结果 |
|---|---|
| backend：`python -m pytest tests/test_workflow_presentation.py tests/test_graph_control.py tests/test_orchestrator.py tests/test_workflow_feedback.py -q --tb=short --show-capture=no` | 59 项通过 |
| frontend：`npm run test:workflow-logic` | 11 项通过，不启动后端/浏览器 |
| frontend：`npm run test:e2e:commands -- workflow-readability.spec.ts workflow-node-editing.spec.ts workflow-drafts.spec.ts workflow-reactflow.spec.ts workflow-routing.spec.ts workflow-feedback.spec.ts graph-control.spec.ts` | 14 项通过 |
| frontend：`npm run build` | 通过；原主包体积提示仍存在 |

纯算法验证从真实依赖推导稳定层次、保留手动位置并避免新增节点重叠，声明回边不拉乱主干，未声明环路可降级显示。循环作为区域布局，不把无关并行任务圈入自动整理后的循环；多个循环保留各自返回目标。折叠投影可以完整还原节点/边，交错阶段拒绝错误合并，失败、等待和当前反馈仍进入摘要。未命名布尔条件不会被猜成“通过”。

后端验证阶段名称和分支文案的保存/清除/历史、旧客户端未提供字段时的保留、非法引用和未知运行字段的原子拒绝。暂停中的 fake Provider 复现过两个问题：纯展示修改提前退出 plan，以及 summary 期间展示修改被错误拒绝；修复后保留原 phase、分工、容量、激活、尝试和预算，之后完成原汇总一次。

浏览器验证了总览与循环展开、实际规则查看、整理撤销后完全恢复原图、只保存坐标而不修改业务结构、标签刷新恢复、窄屏定位、折叠后的真实反馈等待，以及运行视图整理不改版本/尝试。原图管理、反馈、键盘/拖动/连线和本地草稿回归一起通过。另修复了旧 v1 人工确认的状态误标，以及手动恢复后立即刷新可能丢失被替换编辑的窗口；旧文案和菜单自动恢复时序相关的测试已按现行交互修正，未放宽执行断言。

## 已查看的受控截图

标准浏览器记录：`logs/tests/e2e/fake/2026-09-21/18-25-31_e901e00f`。截图只含受控数据，已人工检查层级、连线、循环边界、长标题与窄屏定位；不是用户对最终界面的人工验收。

- [桌面阶段总览](../../logs/tests/e2e/fake/2026-09-21/18-25-31_e901e00f/artifacts/screenshots/__d0cffbcc_0_0.png)：并行实现、审查循环收起后保留主干。
- [展开节点与循环](../../logs/tests/e2e/fake/2026-09-21/18-25-31_e901e00f/artifacts/screenshots/__d0cffbcc_0_1.png)：并行分层、汇合、业务分支文案与外侧回边。
- [窄屏定位任务](../../logs/tests/e2e/fake/2026-09-21/18-25-31_e901e00f/artifacts/screenshots/__d0cffbcc_0_2.png)：直接定位并以可读缩放查看具体任务和属性。
- [折叠后的运行等待](../../logs/tests/e2e/fake/2026-09-21/18-25-31_e901e00f/artifacts/screenshots/__e246829f_0_0.png)：循环轮次、等待和待处理反馈仍可见。

这些链接指向本机测试产物，未纳入源码或发布为公共网页；保留方式沿用测试指南。最终字段和执行边界见[工作流协议](../protocol/public/rest/workflows.md)。

## 覆盖边界

本轮验证桌面 1920×1100 和窄屏 430×900，同时复用了 1440/1680 桌面与 390 窄屏回归。没有 Windows 实机、移动设备触摸或 PostgreSQL 实例验证。任意手动重叠、极密图和交叉依赖不保证完全无交叉；默认保留手动位置，需要时显式整理或聚焦。阶段/循环折叠是展示操作，原节点、结果和控制依赖仍保留；历史图按原版本展示，不重建过去不存在的执行事实。
