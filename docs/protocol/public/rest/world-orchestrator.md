# 世界 Orchestrator、任务与岗位记忆

状态：已接入（2026-09-23）。所有下述管理接口要求当前 World Owner；身份来自认证，不接受模型或客户端另传 World/Owner。存档隔离见 [World](worlds.md)，类型适配见[世界类型](world-types.md)。

## 固定世界管理者与协调会话

每个 World 在 Owner 就绪后幂等建立一个专用管理角色（managed_kind=world_manager）与 purpose=world_coord 的岗位会话。身份随 World 固定，模型、人设和工具配置可以更新；未配置模型为 needs_configuration，停用为 disabled。创建/读取本身不调用 Provider。

| 接口 | 请求 / 返回 |
|---|---|
| GET /api/world-orchestrator | fixed_identity=true、稳定 role_id/conversation、role_name/model_name、revision、enabled/status/available、active_task_count、profile（既有安全 RoleResponse） |
| PUT /api/world-orchestrator/config | 既有角色配置字段；expected_revision 必填，使用 profile.revision，CAS 修改固定管理角色 |
| POST /api/world-orchestrator/import-role | `{role_id, expected_revision}`；从同 World 普通角色复制模型、人设及工具配置；使用岗位 revision，保留管理者名字及固定身份 |
| POST /api/world-orchestrator/enabled | `{enabled, expected_revision}`；启停岗位，使用岗位 revision |
| PUT /api/world-orchestrator（旧入口） | 返回 409 WORLD_ORCHESTRATOR_MANAGED_IDENTITY；客户端升级后使用配置/导入/启停，不再替换任职角色 |

普通角色列表默认排除管理角色，Owner 工作台可用 `GET /api/roles?include_managed=true` 建立包含历史身份的目录。普通角色修改/删除入口拒绝管理身份，返回 ROLE_MANAGED_IDENTITY；普通会话创建和成员变更不能把管理者当作普通参与角色加入。配置保存复用原角色表，不复制另一套模型调用或计量服务。

已有旧绑定在启动时复制为独立的管理配置，沿用原岗位会话；原普通角色、私人会话和实际历史执行作者保留。旧未绑定/停用状态不自动启用。配置导入不会持续跟随来源角色变化，也不能跨 Owner 或 World 复制模型引用。影响执行的配置变更递增授权版本、停止旧任务的确切执行；启用不会恢复旧 grant。

岗位会话不出现在普通列表，成员、删除等普通管理入口不能修改它；REST、WS、上下文和 Memory 继续验证用途与身份。桌宠最小化、关闭、互动和音效仅改变 UI；普通 @ 和同名角色不取得世界权限。

发送复用 `POST /api/conversations/{id}/messages`：

- `world_task_mode=chat`（默认）：只读世界工具与正常回复。
- `world_task_mode=execute`：持久创建一个世界任务，冻结根决策预算和当时 Owner 拥有且加入的普通群范围。
- 同时传 `world_task_id`、`expected_task_revision`：在该任务协调回合空闲时补充要求，保存新的用户消息，沿用根 chain、额度、目标范围和任命；结束/停止/额度耗尽或任命变化后拒绝续办。
- 可传 `expected_appointment_revision` 防止界面旧任命发起新工作；`client_message_id` 保持原幂等身份。重复请求返回原消息和本次请求的 generation_ids，不因任命变化重发，不把所有同链回合混成一次请求。

普通会话不能通过这些字段取得世界执行。上下文、主动/自动压缩、流式恢复、工具详情与模型调用用量复用已有服务。上下文预览计入对话模式的世界只读工具；执行模式额外工具/任务资料以实际调用估算为准。协调上下文窗口按固定管理角色确定，世界任务对子群的授权不把它们变成该会话成员。

## 任务事实与控制

| 接口 | 请求 / 返回 |
|---|---|
| GET /api/world-orchestrator/tasks | 最新 50 个本世界任务；每项含 children、准确状态、revision、根额度、usage |
| POST /api/world-orchestrator/tasks/{task_id}/stop | `{expected_revision}`；停止本任务及派生链 |
| POST /api/world-orchestrator/tasks/{task_id}/children/{child_id}/stop | `{expected_revision}`；只停止准确子任务，使用子任务 revision |

根状态：queued/running/waiting/stopping/completed/stopped/failed/interrupted。一次回复结束只表示协调回合结束。`world_finish_task` 要求存在子事实、所有子任务成功且没有未解决的阻塞反馈；不能用总结覆盖未执行、失败或未知结果。

child 包含 kind、conversation_id、coordination_id、run_id、chain_id、graph_revision、revision、status、error_code、available、类型 reference、最多 20 个选中尝试结果及 20 条未解决反馈。kind 为 group/activity/replan/memory；replan 引用原子链，自己的 chain_id 为空，避免重复汇总。来源群删除或退出成员后保留身份和状态，隐藏其结果、意见与类型正文。

子群保留独立的 WorkflowRun.chain_id 唯一性，通过 `WorkflowBudget.root_chain_id` 关联根。每次收费决策在一个事务内扣根额度、子额度和 execution 序号；重试不重复扣减，自动压缩也不能刷新额度。usage 从根及去重子链的 ModelCallUsage 汇总；缺失厂商用量保持未知。

委派按 `(task_id, request_key)` 幂等认领；同键不同参数拒绝。认领/派发与实际协调引用可核对，模糊失败不自动重放。反馈在无在途世界协调回合时按来源版本合并为一个后续回合，继续原根预算。等待工具只等现有状态，不逐次调用模型。

停止先封闭派发，再取消确切群运行/chain；停止一个重规划回合不停止原群运行。取消不回滚文件。进程启动将未结束世界任务标为 interrupted，保留子群和原执行的恢复事实；不自动重放活动、模型或工具。终态任务若要继续新的工作，Owner 重新提出目标。

`world_task_updated` WS 事件仅含 task_id/revision/status；正文按授权 API 读取。旧客户端可忽略未知事件。当前面板定期读取状态，WS 的主职责仍为原消息流。

## 实际工具发放

每次能力解析及每次调用核对 `WorldCoordinationGrant`、真实 execution/generation、停止标志、Owner 和当前任命 revision；类型或普通角色配置不能伪造此 grant。

| 场景 | 工具 |
|---|---|
| 有效世界对话或任务回合 | world_read_overview、world_read_task、world_memory_search、world_memory_read |
| 同时具有本次 execute 任务授权 | world_delegate_group、world_replan_group、world_wait_task、world_finish_task、world_stop_task、world_memory_save、实际注册的 world_activity_* |
| 角色另行显式启用 | memory_search/read、本类型工具；继续经过原工具权限与实际分配校验 |

概览返回最多 50 个当前可访问群及 20 个任务引用。委派参数为 conversation_id/goal/request_key/mode（design或execute）及可选 definition_id/expected_graph_revision；已有模板须提供准确版本。局部调整使用 child_id/expected_graph_revision/goal/request_key，交给目标群协调者原有读图/改图/反馈工具。世界工具不能代签人工确认、扩大群角色工具权限或直接改已执行节点。

世界历史检索：普通只读协调回合只读取岗位会话；执行任务可读取冻结目标群中当前仍有权访问的消息/摘要，无须把协调角色加入全部群。角色的普通执行保持原成员与共享交集规则，不继承岗位信息或导入所有私人会话。

## 可编辑世界记忆

| 接口 | 请求 / 返回 |
|---|---|
| GET /api/world-orchestrator/memories?query= | 最新 50 项；Owner 可以看停用项，来源失效不回显正文 |
| POST /api/world-orchestrator/memories | request_key、category、text；保存、同键幂等 |
| GET /api/world-orchestrator/memories/{id}?revision= | 按准确版本回读 |
| PUT /api/world-orchestrator/memories/{id} | expected_revision、text、status（active/disabled）；CAS 修改或恢复 |
| GET /api/world-orchestrator/memories/{id}/versions | 最新 50 个不可变历史版本；仍验证原来源 |

category 为 constraint/decision/preference/note/inference，text 最长 20000 字符。Owner 保存标记 origin=owner；模型保存要求本次世界任务授权，origin=agent，保存任务原用户消息 ID/revision 和实际角色。模型归纳与推断不会成为平台规则。停用/恢复不改作者；Owner 真正修改正文后形成手动来源的新版本。

岗位记忆归 World，更新配置后可由新授权回读。模型搜索只返回 active 内容，搜索及读取使用原 MemoryReference 表记录 `source_kind=world_note` 和版本，不保存另一套 Trace；下一次调用及私有压缩采用前复核条目、来源和任命。子群结果的工具读取也使用 world_child 引用核对成员/来源权限，进度更新不会把过去的真实结果改写成当前结果。修改、停用、来源消息修订/删除使旧引用失效，历史版本也不能绕过源失效。协调会话共享摘要不会收录私有工具结果。

当前为明确保存和主动查询的条目库；不包含全世界自动抽取、跨 World 检索或向量服务。错误语义见[注册表](../../error-codes.md)，实际覆盖见[验收记录](../../../testing/world-orchestrator-foundation.md)。

## 委派消息的来源与回报

世界工具委派在目标群显示世界管理者来源，收件人为本次群协调者；Owner 保留为授权用户。群协调者的回复、系统按图派发、实际角色结果分别标注自己的主体/职责，并显示宿主确定的接收对象和回复关联。正文 @ 不再次触发执行。完整消息、广播批次和历史来源修复见[协作通信协议](workflow-communication.md)。
