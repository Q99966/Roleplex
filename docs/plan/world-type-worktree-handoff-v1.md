# 世界类型 worktree 接入与交接 v1

日期：2026-09-23。公共 P1–P4 已在当前工作区实现，记录见[前期工作](world-orchestrator-foundation-v1.md)和[验收](../testing/world-orchestrator-foundation.md)。**先取得包含这些实现的公共提交**，旧基线 `7b1c3ae` 不包含新能力。本文是接入说明，字段与行为只由[世界类型协议](../protocol/public/rest/world-types.md)及[世界协调协议](../protocol/public/rest/world-orchestrator.md)维护。

公共层的[管理者身份、消息来源与任务广播修复](world-coordination-message-provenance-v1.md)已接入。取得包含 R1–R4 的公共提交后，按[协作通信](../protocol/public/rest/workflow-communication.md)联调固定身份、收件人和独立输入；不再依赖普通角色换绑或每节点一条 user 消息。

## 1. 可直接复用

- 创建时选择类型、类型版本与 World 存档兼容、Owner 后幂等初始化、模型引用配置、专属页面入口。
- World 提供固定管理者，桌宠配置其模型或导入独立设置；独立岗位会话、上下文与压缩、消息/工具结果/用量、群流程钻取和任务停止。
- 世界目标真实委派给群；群保持自己的工作流和工具分配。类型活动复用相同子任务、根额度、来源、取消和恢复。
- World 专属约定的保存、检索、修订、停用与历史来源；管理者配置变更后保留岗位历史，普通角色聊天不会获得这些资料。

狼人杀全部业务及信息可见规则属于类型分支；本次公共层没有注册狼人杀产品选项，也没有替该分支规定群结构、身份、阶段或结算方式。Skills、MCP、知识图谱和内置 Git/worktree 产品能力不是前置。

## 2. 具体接入顺序

1. 在 `backend/app/world_types/<type_id>/` 实现业务模型/服务与描述器；公共导入入口为 `app.world_types`，可导入 WorldTypeDescriptor、WorldContext、WorldMaterial、Initialization、WorldToolDefinition、WorldActivityDefinition、ActivityPlan、assert_active。
2. 在 `backend/app/world_types/installed.py` 的 installed_types 增加描述器。先实现配置和幂等初始化，缺模型/配置返回 needs_configuration；业务表走 Alembic。初始化资源引用要能复用，不能靠重新创建全部角色和群处理重启。
3. 通过 build_context/validate_sources 提供当前实际执行者的材料和版本；通过 resolve_tools 提供角色显式允许的工具。宿主身份和额度不可由模型参数覆盖。长操作/副作用边界复核 assert_active，并遵守已有资源、命令和审批规则。
4. 需要世界协调者启动的业务操作登记为 WorldActivityDefinition，prepare 在宿主事务内返回指向实际群定义的 ActivityPlan；模型执行交给原群工作流。当前接口以群流程为载体，特殊后台活动先补公共生命周期，不能另起无账本模型循环。
5. 在 `frontend/src/world-types/<type_id>/` 实现专属页面，在 `frontend/src/world-types/installed.ts` 静态登记入口。复用宿主 onOpenConversation 和当前类型状态；不从存档字符串执行任意 import。
6. 用自己的完整 fake Provider 流程验证业务；参考 `backend/tests/world_types_fixture.py`、`test_world_tasks.py`、`frontend/tests/world-managed/world-types.spec.ts`。fixture 仅由专用测试入口装配，生产不包含它。

## 3. 文件分工与迁移

| 区域 | 维护与合并原则 |
|---|---|
| worlds、公共 world_types 契约、world_orchestrator、调度/预算/上下文/权限 | 公共层；类型通过扩展点接入，缺能力时做最小公共补充 |
| backend/app/world_types/<type_id>/ 与 frontend/src/world-types/<type_id>/ | 类型业务分支 |
| 两端 installed 装配文件 | 集中增加类型项，避免覆盖其他分支的公共实现 |
| WebPet、公共对话控制器和通用 API | 复用公共组件；类型专属操作放自己的页面 |
| Alembic、统一 ORM metadata 入口 | 公共头当前为 0033_workflow_communication，类型迁移接实际头；合并时整理合法迁移图并检查 |

优先以公共提交形成共同祖先；已有分支可合并公共提交或 cherry-pick 明确独立的提交，不整文件复制覆盖。不同分支可以暂有迁移头，合并前检查 SQLite 重放、外键/模型一致性和 PostgreSQL 离线 SQL。已应用/发布的迁移不任意改写；业务模块不能运行时建表。

```bash
git worktree add -b feature/world-type-werewolf <独立工作目录> <公共底座提交>
```

## 4. 运行与测试隔离

各 worktree 使用独立的前后端端口、World 存档根、日志和受控文件工作区。包装器显式传 `--worlds-dir`、`--port`；创建类型时可用 `--world-type`、`--type-version`；前端设置匹配的 Vite 端口和 VITE_PROXY_TARGET，后端设置对应 CORS。详细启动方式见[README](../../README.md)。

Git worktree 只隔离代码，数据库、密钥和运行租约仍须独立目录。不要用不同迁移版本共同打开真实 World，不把主分支 .env 或解密密钥提交到分支。新世界模型配置显式提供，不能复制真实凭据到 fixture。

现有 Playwright 套件有固定默认端口；并行 worktree 先错开同套件。如需同时运行，再集中补兼容的端口配置。数据轮次、日志、截图和清理沿用[测试指南](../testing/README.md)。

## 5. 类型分支自己的验收门槛

类型身份创建/重启一致、缺配置可恢复、初始化不重复；实际业务工具/活动有执行关联及准确结果；重复请求、并发额度、取消/撤权、失败/部分完成和重启不伪造成功；材料与业务输出通过该类型自己的信息视图校验；业务 UI 可操作且普通 general 回归不退化。

公共验收证明扩展点已可调用，不能代替狼人杀本身的业务结果验证。
