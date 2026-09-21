# 工作流反馈与局部处置验收

日期：2026-09-21。范围为[三批改进计划](../plan/workflow-feedback-usability-v1.md)的第一批；当前协议见[节点反馈](../protocol/public/rest/workflows.md#节点反馈与处置)。第二批布局/折叠和第三批面板收敛未实施，本记录不替代它们的验收。

环境：Linux、Python 3.12、Node 24、SQLite，真实浏览器 Chromium。所有 fake 测试使用隔离数据库与受控内容；真实 Provider 通过既有 real-world 后端读取本机配置，Key 不进入浏览器、命令行或本文。

## 确定性验证

| 命令（相应目录） | 验证范围 |
|---|---|
| backend：`python -m pytest tests/test_workflow_feedback.py tests/test_graph_control.py tests/test_orchestrator.py -q --tb=short --show-capture=no` | 53 项通过；反馈来源与幂等、权限/版本冲突、分支等待、循环内补图、分类处置、证据要求、停止/撤权/取消竞态、重启及原有图管理/并行循环 |
| backend：`python -m pytest tests/test_orchestrator.py::test_same_role_parallel_allocations_and_execution_recheck tests/test_workflows.py tests/test_workflow_budget.py tests/test_context_builder.py -q --tb=short --show-capture=no` | 35 项通过；新工作节点报告工具与原生工具隔离、串行兼容、预算及上下文回归 |
| backend：`python -m pytest tests/test_workflow_feedback.py tests/test_graph_control.py tests/test_orchestrator.py -q -k 'feedback or history or manual_loop' --tb=short --show-capture=no` | 最终 16 项通过、38 项未选；包含新增防自唤醒、工具 schema、重新打开以及当前激活/历史显示相关复验 |
| backend：`python scripts/check_migrations.py` | 0023 SQLite 升级、模型一致性、外键、降级通过；PostgreSQL 离线 SQL 通过 |
| frontend：`npm run test:e2e:commands -- workflow-feedback.spec.ts graph-control.spec.ts` | 4 项通过；实际前后端、反馈人工委托/自动授权、来源补充、处置竞争、局部补图及原图管理流程 |
| frontend：`npm run test:e2e:commands -- workflow-feedback.spec.ts` | 随后 2 项复验通过，受控反馈截图已人工查看；原执行仅一次，反馈与局部修改可查看 |
| frontend：`npm run build` | 通过；保留已有主包体积提示，不作为功能失败 |

开发过程首先验证了缺失接口和缺失反馈等待行为；另一次浏览器失败来自同名文本包含在核验复选框中，已改用精确 textbox 选择器。原工具分配测试也已按新契约调整：工作节点可报告 feedback，同时仍不能调用未分配的文件工具或图管理工具。这些失败不计作通过记录。

实现缺陷、契约冲突、能力缺口和未验证项使用不同的受控 Provider 分支。能力不足/未验证保持 waiting，未开启 Shell、未生成成功验证或重复派发协调任务。停止/撤权使旧协调工具拒绝提交；取消与验证结束相撞时保留已完成结果，不再自动创建复核执行。含阻塞反馈的重启恢复要求 Owner 明确继续，来源不重跑。

后续专项还检查了模型只标记“待复核”不会再次唤醒自己，以及工具 schema 只暴露协调者实际可用的四种处置、不提供人工核验字段。重复打开和新验证分别记录，旧证明不能直接用于再次关闭。画布使用 activation.current 选择当前有效激活，避免修订后误读旧的 superseded 状态。

## 真实 Provider 协作

命令：在 frontend 运行 `npm run test:e2e:real-world -- workflow-feedback-provider.spec.ts`，1 项通过，约 1.7 分钟。执行前已说明联网计费；没有以 fake 代替本次实际模型验证。

受控 `release.txt` 含两条冲突约定与明确裁定依据。真实角色通过工具完成：

1. 审查者读取文件，使用 workflow_result 登记 contract 反馈；原审查只执行一次。
2. 协调者读取图和运行，用 workflow_edit_graph 局部新增 `contract_fix`，再 assign 关联处理节点。
3. 裁定角色实际读取、写回并再次读取核对，文件最终为 `release=beta`（无换行），报告 feedback_resolved=true。
4. 新协调执行读取实际验证尝试，使用 workflow_feedback_update 关闭反馈，交付与汇总随后完成。

本次运行图从 v1 到 v2，反馈最终 resolved、revision=6；三个协调会话分别为 execute、replan、replan，均 completed。共享原预算，消耗 21 / 64 次决策；没有刷新预算或把每个普通成功节点都交回模型重新规划。

- 保留 World：`data/roleplex-real-world-e2e-20260921165245/default`。
- 会话：3；运行：`d7abb86855924102bf8fcf0f3533a598`。
- 工作区：既有测试工作区根下 `roleplex-real-world-e2e-20260921165245/workflow-feedback`。
- 标准摘要：`logs/tests/e2e/real/2026-09-21/16-52-45_4a56c84d/summary.json`。原消息、工具记录、图版本及反馈处置留在该 World；按测试指南的保留规则管理，含密钥的 World 不作为普通附件分享。

## 覆盖边界

真实模型用例证明本次契约裁定与文件验证链路；恶意参数、精确竞争、取消/恢复由确定性测试覆盖，不宣称所有 Provider 都已验证。PostgreSQL 仅离线编译，没有实例运行证据；没有 Windows 实机验证。图的拓扑整理、阶段/循环折叠、窄屏面板重构仍属于后续批次。反馈关闭依赖实际完成的处理尝试及结构化验证结论，原文件效果和当前文件仍通过既有事实入口核查。
