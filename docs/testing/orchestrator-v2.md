# 群协调、并行与循环验证

日期：2026-09-17。范围为[群协调基础实现](../plan/orchestrator-parallel-loops-v1.md)，协议见[会话工作流](../protocol/public/rest/workflows.md)。测试使用隔离数据库和受控工作区；下列记录保留当时实际运行结果。

> 2026-09-21 覆盖复核：本页证明预设图上的分配/结果上报、并行循环和相关安全边界，不覆盖协调者从目标创建图、读图/改图工具、图编辑授权、共同编辑和运行重规划。原通过数及报告保留；该次缺口的采用范围见[整改计划](../plan/orchestrator-graph-control-v1.md)，后续实际实现验收见[图管理记录](orchestrator-graph-control.md)。本次文档更新未重跑这些测试。

## 后端与迁移

扩大回归首次结果为 153 passed / 1 failed（226.46 秒），失败为旧流程读取控制快照时的 revision 竞争：不同 SELECT 拼出了旧运行版本和新尝试状态。已修正为与状态转移共用短锁；修正后 `test_workflows.py + test_orchestrator.py + test_resource_admission.py` 共 44 passed（122.59 秒）。随后规划完成后重启恢复/停用锁定、普通群聊及删除回归 18 passed（14.19 秒）。新增三组有限预算、循环次数上限和不限预算均通过，包含在上述 44 项中。首次失败不计为通过，也没有把单独重跑通过代替修复。

扩大回归同时覆盖 workspaces、group_workspaces、workspace_commands、workspace_edit、workspace_batch_mutation、shell_approvals、runtime_service 与 delete_semantics；这些未失败的测试结果保留，不把后续局部回归描述为全套重跑。

确定性模型通过真实 Agent/工具循环完成：协调规划 → 开发文件 → 两角色屏障并行审查 → 汇合 → 协调判断 → 第二轮返工 → 汇总。断言 28 次共享决策、准确父 execution、独立轮次和实际磁盘内容。手动运行同一图无须任命。

额外覆盖两分支全部停止与任命撤销、无效布尔条件阻断、Guest/跨群角色/过期任命版本、同角色不同分配与调用时撤权、独立分支重试、旧轮次保留、人工条件的跳过/汇合、等待重启恢复、别名/重叠根/独立根、公平读写、重复取消、等待超时、版本竞争，以及规划 execution 已完成但调度状态未采纳时的重启恢复。角色重复保存的约束错误返回安全冲突，避免 SQL 参数进入日志。

迁移 0020 已通过 SQLite 升级、ORM 一致性、外键检查和降级；PostgreSQL 788 行离线 SQL 渲染通过，未运行 PostgreSQL 实例。Frontend TypeScript 与 Vite 构建通过。

## 浏览器

最后一轮四个相关 spec 合计 **7 passed / 0 failed**；报告为 `logs/tests/e2e/fake/2026-09-17/11-51-58_e7b5f64b/summary.json`，覆盖快照读取与撤权并发修正后的页面操作。2026-09-21 继续任务时核对了持久报告与保留 World；没有重新调用收费 Provider，也没有把此核对算作测试重跑。

真实 FastAPI + Vite + fake Provider 的 `orchestration.spec.ts` 覆盖成员任命、右侧工具/循环配置、两轮执行、刷新选择精确历史、未任命入口与人工等待期间取消任命。旧 workflow-canvas、workflow-node-editing、workflow-reactflow 三个 spec 的 5 项用例通过，覆盖原串行、停止/重试、Tab/Delete、类型/颜色、分支环路保存和窄屏操作。

新增测试的初轮修正包括：真实配置使用显式 API origin；刷新后显式选择历史运行；同轮多个夹具使用不同角色/工作区名称。初轮失败不计为通过；最终结果以测试指南入口的成功运行记录为准。

## 真实 Provider

`npm run test:e2e:real-world -- orchestration-provider.spec.ts`：1 passed，43.3 秒。真实模型配置使用既有后端加密凭据，通过正常 World 包装器；协调者实际规划与汇总，两审查节点真实读取，第一轮不通过、第二轮返工后通过，检查结构化结果、全部 execution 厂商 usage 和刷新历史。

- World：`data/roleplex-real-world-e2e-20260917113621/default`
- 测试账号：`realtest20260917113621`；密码使用[测试指南](README.md#四数据账号与保留)的 real-world 固定占位值。
- 运行报告：`logs/tests/e2e/real/2026-09-17/11-36-21_33dd884f/summary.json`
- 群：`并行返工协调验收`，右侧选择工作流历史运行查看规划、分配、两轮判断与汇总。

真实测试关闭截图/trace/video，文件内容留在本轮产品记录与隔离工作区；第一次读取尚不存在的目标文件是受控预期，后续实际创建和更新成功。初次真实入口准备阶段因夹具误用默认代理端口失败，修正 API origin 后运行通过；该准备失败没有发起模型请求。没有为强迫模型产生预期结果无限重跑。

人工查看沿用测试指南的 `run_world_server.py --world default --worlds-dir ../data/roleplex-real-world-e2e-20260917113621 --port 8000`，启动前确认端口未被其他服务使用。World 包含真实加密 Key 及解密密钥，只留本机。

## 覆盖边界

当前仍为单后端进程；并行容量是进程内执行槽。循环域互不嵌套，可包含并行分支；未实现任意跨域跳转。Windows 原生 Shell/进程回收及 PostgreSQL 实例没有本次运行证据；Linux 上既有 Shell/服务测试覆盖受影响路径。外部进程写文件不受进程内准入控制，原文件版本校验仍生效。世界级协调、桌宠入口与 Git/worktree 没有纳入实现或验收。
