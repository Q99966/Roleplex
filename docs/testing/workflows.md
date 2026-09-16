# 会话工作流验证记录

日期：2026-09-16。环境：Linux，Python 3.12、SQLite、Chromium；不代表 Windows 或 PostgreSQL 实例验收。

## 本轮实现与入口

- 后端：`cd backend && python -m pytest tests/test_workflows.py -q`。
- 关联上下文与群聊：`tests/test_context_builder.py`、`tests/test_interruption_context.py`、`tests/test_group_workspaces.py`。
- fake 浏览器：`cd frontend && npm run test:e2e:commands -- workflow-canvas.spec.ts`。
- 右栏既有行为：`npm run test:e2e -- conversation-details.spec.ts`。
- 真实 World：`npm run test:e2e:real-world -- workflow-provider.spec.ts`，联网计费；沿用测试指南的专用凭据、隔离 World 和保留策略。
- 迁移：`cd backend && python scripts/check_migrations.py`；前端：`cd frontend && npm run build`。

## 实际验证

已通过：

- 保存/图校验、版本冲突、同一角色多节点、启动幂等、快照不随编辑改变。
- 开发→人工确认→审查的真实文件交接；人工等待无活跃 lease，阻止换绑。
- 确认与停止竞争、精确停止不影响后发普通聊天、后端重启不重放，等待记录可恢复。
- 重试保留旧尝试，重做上游不沿用下游旧结果，共享预算不随节点或重试重置。
- Owner/Guest、跨会话、成员撤销、工作区文件工具撤销、准确尝试的群聊文件事实与过期证据保持未知。
- 画布编排、保存/启动、人工确认、原消息/diff 查看、刷新后恢复运行、返回聊天保留草稿、窄屏与键盘关闭。
- 浏览器中开发已提交后停止、刷新、重试人工节点并继续审查；开发节点不重复执行，旧尝试保留。
- SQLite upgrade/downgrade、外键与模型一致性；PostgreSQL 离线 SQL 渲染。

本轮关联批次 41 项通过后，新增工具撤销、精确历史事实、单聊入口、必需上游预算检查和只重试上游后明确继续下游，并分别运行对应定向用例。
真实场景不代替这些并发/取消/崩溃测试。

## 真实 World 留存

第一次真实调用因 Provider 返回 `PROVIDER_AUTH_FAILED` 未执行工具，属于明确失败；用户更新专用凭据后，重新启动独立测试 World，第二轮通过（1 项，13.8 秒）。
两角色分别实际创建、读取和编辑文件；人工确认前后均核对磁盘，节点终态与记录一致，厂商输出用量非空。

可查看 World：`data/roleplex-real-world-e2e-20260916185747/default/`。
会话为“真实工作流协作 20260916185747”，流程为“真实开发审查 20260916185747”。
外部工作区位于测试指南配置的 `ROLEPLEX_E2E_WORKSPACE_ROOT` 下，本轮相对目录 `roleplex-real-world-e2e-20260916185747/workflow/`。
测试账号登录方式、World 启动和保留轮数沿用[测试指南](README.md)，本记录不复制凭据或密钥。
真实用例禁用截图、trace 和视频；不生成包含真实配置的浏览器产物。World 包含密钥，不能提交或公开分享。

## React Flow 替换（2026-09-16）

使用 @xyflow/react 12.11.6，保留原业务控制与消息详情。新增 `workflow-reactflow.spec.ts` 验证鼠标/键盘移动、
布局保存恢复、连接点拖拽、键盘连线与删除、分支/环路保存、启动拒绝无副作用以及运行快照只读。
后端新增对应图保存/执行分离、坐标与拓扑独立、旧无坐标兼容和非有限坐标拒绝用例。
原 `workflow-canvas.spec.ts` 的人工确认、停止、刷新重试继续回归。
本轮属于画布及保存契约调整，使用 fake Provider；上方真实 World 结果是前一轮验证记录，本轮未重复收费调用。

本轮 React Flow 回归结果：工作流后端 21 项通过；密码/请求校验关联回归 5 项通过；浏览器 3 项通过（两项既有工作流流程、一项布局与通用图编辑）；前端构建通过。
坐标存入既有 JSON，不涉及表结构变更。本轮未重复迁移与真实 Provider 收费验收；之前的迁移和真实验证保持历史证据边界。

### Delete 快捷键

`workflow-reactflow.spec.ts` 新增 Delete 的浏览器路径：删除选中边、删除孤立节点、入边及自环保护、
文本输入与画布外焦点保护、清理结果引用、保存以及运行视图只读。属性删除按钮和快捷键使用同一节点边界。

### 侧栏与节点编辑

`workflow-node-editing.spec.ts` 覆盖流程级控件移至右栏、编辑态无多余编辑按钮、Tab 插入后的连接和焦点、
Shift+Tab/输入框保护、节点类型切换、默认/自定义颜色、保存刷新、窄屏抽屉添加/保存/启动、运行视图只读及返回定义编辑。
后端定向用例覆盖颜色校验与运行快照不随定义类型/颜色修改。沿用 fake Provider，本轮不触发真实模型费用。

本轮侧栏与节点编辑实际结果：1 项后端颜色/快照定向测试、5 项工作流浏览器回归及前端构建通过。
覆盖桌面和窄屏；未执行新的收费 Provider 验收，未新增数据库表列。
