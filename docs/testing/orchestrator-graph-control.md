# 群工作流图管理与重规划验收

日期：2026-09-21。采用范围：[图管理整改计划](../plan/orchestrator-graph-control-v1.md)，当前字段与边界：[工作流协议](../protocol/public/rest/workflows.md)。本页记录此次实际运行，不把旧固定图验收当作图管理完成证据。

## 已验证行为

- 无完整图时通过 `/plan@协调者` 创建独立协调会话，模型实际调用 read_graph、独立 write_graph 和 edit_graph；草稿可包含尚未配置的条件/循环，启动时再做完整编译。
- 整图和局部修改共用原子服务：版本冲突、只校验、幂等重发/跨工具键冲突、无效引用整批回滚、布局保留、Owner 保护及 Guest/跨会话拒绝。
- 运行中追加审查而不重跑开发；当前轮保持旧版本，多个受影响循环等待共同边界后采用新版本。停止、取消和换任命后，旧对象与尚未采用的修订均不能迟到修改或派发。
- 移除后以同一 ID 重新添加的节点、跨图版本重试各有准确激活身份，旧尝试保持原事实。独立协调重启降级，不重放提交；继续同一草稿、启动与重规划复用预算。
- 结果类型与实际工具 schema 一致，非法报告可修正，重复报告幂等，修正已有报告使用结果版本 CAS。模型委托重试先观察真实运行，写入证据未知时不能自称确认后重放。
- 画布同步模型提交；人工脏草稿遇到新版本保留并显示冲突，可另存或明确采用远端。历史图按版本查看，不把新增节点投影到旧图。

## 确定性与浏览器结果

在 backend/ 运行：

```bash
pytest tests/test_graph_control.py tests/test_orchestrator.py tests/test_workflows.py tests/test_group_chat.py tests/test_resource_admission.py -q --tb=short
```

该轮 **76 passed，213.81 秒**。覆盖图管理、旧固定图、普通群聊、并行循环与资源边界。后续结果修订 CAS、非法结果修正、未完成条件/循环草稿和基础两轮协调专项复跑 **4 passed，17.40 秒**；不把专项复跑描述为再次全量运行。

在 frontend/ 运行：

```bash
npm run test:e2e:commands -- graph-control.spec.ts orchestration.spec.ts workflow-canvas.spec.ts workflow-node-editing.spec.ts workflow-reactflow.spec.ts workflow-routing.spec.ts
npm run build
```

组合 **10 passed（约 1.2 分钟）**；后续图管理页面专项 **2 passed（18.6 秒）**，最终含受控截图复跑 **2 passed（17.1 秒）**；已查看页面截图，TypeScript/Vite 构建通过。使用真实 FastAPI/Vite 与 fake Provider，不产生模型费用，未用 API 预置最终图冒充“从目标创建图”。正常预置图只用于单独的运行重规划场景。

开发中修正了旧 PUT 缺省执行版本被新草稿默认值覆盖、JSON 状态变更未标记导致新分配丢失、未来激活与旧图身份混用等问题；失败轮次不计为通过。最终记录区分完整回归和补充专项。

迁移 0021/0022：SQLite 升级、ORM 一致性、外键检查、空验证库降级通过；PostgreSQL 离线 SQL 894 行通过。0022 遇到已有跨图同节点同轮历史时拒绝破坏性降级，不删除记录来满足旧约束。

## 真实 Provider 与保留 World

入口：

```bash
npm run test:e2e:real-world -- graph-control-provider.spec.ts
```

**1 passed；用例 58.3 秒，整轮约 1.1 分钟。** 使用正常 World 包装器与后端加密凭据，真实协调者从目标创建三步流程，分别调用整体写入和局部编辑。随后实际写入文件、等待 Owner 人工确认，再由协调者在运行中增加读取审查；原开发未重跑，确认后完成文件验收和汇总。断言实际文件、结构化结果、真实 usage、共享 chain 与刷新后的历史图。

- World：`data/roleplex-real-world-e2e-20260921113413/default`
- 群：`真实自主图管理验收`
- Owner：`realtest20260921113413`，密码沿用[测试指南](README.md#四数据账号与保留)的 real-world 测试占位值。
- 报告：`logs/tests/e2e/real/2026-09-21/11-34-13_9365d6b8/summary.json`

从 backend/ 使用正常入口查看：

```bash
python scripts/run_world_server.py --world default --worlds-dir ../data/roleplex-real-world-e2e-20260921113413 --port 8000
```

真实验收创建时为 0021；随后 0022 加强激活历史唯一约束，以确定性回归验证。保留 World 下次正常启动会按迁移升级，未手工改库或倒推历史。真实测试关闭截图/trace/video，失败仅保留阶段标签；第一次探测尚不存在的文件产生工具失败，随后实际创建和验证成功。没有为强迫模型产生预期结果无限重跑。

World 含加密凭据及解密密钥，仅留本机。测试服务已由各入口退出，数据沿现有轮次保留规则管理。

## 覆盖边界

当前仍为单后端进程、互不嵌套循环域。精确并发、取消竞争、未知副作用和恢复由确定性测试证明；真实模型用例不证明所有竞争时序。PostgreSQL 仅离线 SQL，Windows 原生进程没有本次实机证据。世界级协调、桌宠、Git/worktree、自动回滚文件和完整崩溃后自动续跑不在此阶段。
