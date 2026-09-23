# 世界类型注册、配置与初始化

状态：类型底座及工具/活动执行已接入（2026-09-23）；[世界协调与任务](world-orchestrator.md)维护任命、预算和控制协议。物理存档与切换见[World](worlds.md)。

## 类型与安装

类型来自受信应用代码；后端装配点为 `backend/app/world_types/installed.py` 的 installed_types，返回 WorldTypeDescriptor 元组。general@1 由核心提供。描述器位于 `world_types/contracts.py`，包含 id/version/name/description/frontend_entry、Pydantic configuration_model 和可选 initializer/overview/build_context/validate_sources/resolve_tools/activities。公共导出从 `app.world_types` 导入。

type ID 为 `[a-z][a-z0-9_]{0,63}`，版本为正整数。同一 ID/版本重复注册拒绝；已有存档精确解析版本，创建省略版本时取最新版。提供 build_context 必须同时提供 validate_sources。manifest 不能指定模块路径、脚本或外部命令。生产仅注册实际实现的类型，受控 fixture 只在专用测试启动器装配。

新世界 format_version=2，明确记录 world_type/type_version；格式 1 缺字段解释为 general@1。数据库只保存配置与初始化凭据，不提供修改存档类型的第二入口。缺实现/不兼容的类型可列出不可用状态，启动/切换在迁移前拒绝。类型 ID、版本随备份保留。

## Owner 接口

所有接口要求当前 World Owner，Cache-Control 为 no-store，不通过请求选择另一个 World。

| 接口 | 请求/结果 |
|---|---|
| GET /api/world-types | items：每种已安装类型的最新描述器与配置 schema、前端入口和能力声明 |
| GET /api/world-type | 当前类型、配置、revision、初始化状态/资源/待补字段/安全错误及可选概览；读取不初始化 |
| PUT /api/world-type/config | `{expected_revision,configuration}`；严格按类型 schema 验证，CAS 保存后尝试初始化 |
| POST /api/world-type/initialize | `{expected_revision}`；明确重试当前配置，已完成同版本不重复创建 |

初始化状态为 pending/needs_owner/needs_configuration/ready/failed。配置更新与初始化结果分别可观察：初始化失败不会把已经保存的配置伪装成未提交。并发冲突返回 409，未知/不合法配置返回固定 422，不回显原配置或内部异常。错误见[注册表](../../error-codes.md)。

## 初始化与资料适配

宿主先迁移数据库，再建立初始化状态；有 Owner 后调用 initializer(session, context, configuration, resources)。扩展返回 Initialization(status, resources, required_fields)。该函数只在宿主事务中读写业务资源，不自行 commit、不调用模型或外部服务；资源和 ready 凭据一起提交，失败回滚。resources 应保存稳定业务引用，供后续配置初始化幂等复用。跨重启同版本已完成状态直接复用。

overview(session, context, configuration, resources) 返回 Owner 的有界 JSON 概览。配置 schema 用已有 ModelConfig 的引用表达模型选择，不能把 API Key 等凭据复制到类型配置中。

WorldContext 由宿主构造，包含真实世界/type/version、Owner/触发者，执行构建时另含角色、会话、execution、execution_kind、parent_execution_id、world_task_id、budget_chain_id、root_budget_chain_id、appointment_revision 和可见消息上界；浏览器和模型不能替换这些身份。类型在 build_context(session, context, configuration) 返回至多 32 个 WorldMaterial，每项含 source_id/revision/text（单项最多 100000 字符）。扩展负责该执行者的业务可见性，宿主沿原会话权限校验并统一计入容量。

材料作为明确标注的背景消息进入本次输入，不写进 ConversationContextEntry 或共享摘要；世界资料的来源/版本/hash 和配置 revision 存入原 context_snapshot.material.world_type。validate_sources(session, context, receipt) 在构建与每次调用前复核，必须明确返回 true，失效或异常拒绝继续使用。普通会话查询、Memory 和工具权限不会因注册类型自动扩大。工具/活动共用该可信身份，不能从模型参数重建身份。

## 前端入口

创建世界提供世界类型选择。当前类型配置位于“运行世界与存储”，显示初始化状态、必要配置及明确重试入口；配置冲突保留草稿。通用编辑器支持标量/枚举及引用本世界模型配置，复杂字段由类型专属界面提供，不能用不可编辑字段悄悄覆盖原配置。

前端装配点为 `frontend/src/world-types/installed.ts`，按 frontend_entry 静态登记 React 组件。组件接收 WorldTypeView 和宿主的 onOpenConversation，复用实际资源/会话路由；后端字符串不作为任意 import 路径。前端缺模块时显示不可用提示。受控测试页只在指定开发测试构建启用。

## 类型工具与活动

`resolve_tools(session, context, configuration)` 返回至多 64 个 `WorldToolDefinition(name, description, args_model, execute)`。名称须带 `type_<type_id>_` 前缀且不重复；角色显式 builtin_tools 白名单、本次工作流分配、权限、预算均继续生效。工具默认 dangerous，描述器不能自授 safe。预览的 execution 可为空；实际能力快照和工厂均使用真实 execution。工具执行前重新解析当前定义并比较 schema，`execute(context, validated_parameters, configuration)` 在无宿主长事务时运行。长操作可调用公共 `assert_active(context)` 在副作用边界检查取消/撤权；异常只返回固定结果未知错误，不自动重试业务副作用。

`activities` 是 `WorldActivityDefinition(name, description, parameters_model, prepare)` 元组；参数不能占用宿主的 request_key 字段。对应世界任务工具为 `world_activity_<type_id>_<name>`，参数为业务 schema 加 request_key。仅 execute 世界任务取得活动启动能力。

`prepare(session, context, parameters, configuration, resources)` 在宿主短事务中准备类型业务资源，返回 `ActivityPlan(conversation_id, definition_id, graph_revision, goal, reference)`。模型/工具执行由宿主交给该群现有协调器；活动引用、父执行、独立子链、根额度、反馈和取消沿用 WorldTaskChild。prepare 不自行 commit、不调用 Provider 或外部命令；返回群必须属于 Owner、当前仍有 Owner 成员及有效群任命，定义版本由群服务再次核对。类型可准备自己的群，无需依赖普通委派在任务创建时冻结的群列表。

首版活动适配的执行载体是既有群工作流。没有第二套任意后台活动运行器，也没有独立的 start/read/stop 三组类型回调：读取和停止统一通过世界任务/原群运行。需要特殊长期后台资源时，后续明确扩展通用生命周期，不能私自启动无账本模型循环。reference 保存类型业务引用，不应包含凭据或无权给 Owner 查看的材料。

受控实现见 `backend/tests/world_types_fixture.py`；测试装配 `world_types_e2e_app.py` 证明创建、初始化、页面、材料、实际类型工具与活动，生产安装点不包含 fixture。迁移与并行分支接入见[交接说明](../../../plan/world-type-worktree-handoff-v1.md)。

## 固定管理者与执行输入兼容

公共层在 Owner 就绪后建立专用世界管理者，类型初始化器不再创建/替换该岗位。类型角色和群仍由自身 initializer 管理。WorldContext 的 role_id 是本次实际执行角色；类型工具/活动不应依赖某个固定数字 ID 或“节点输入必为 user 消息”。输入现通过 execution_inputs 关联原 execution，详情见[协作通信](workflow-communication.md)。
