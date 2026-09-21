# 会话工作流

| 元数据 | 值 |
|---|---|
| 状态 | 已实现图管理、版本化重规划、节点反馈与局部处置；保留并行循环与 v1 兼容 |
| 协议版本 | 4（兼容新增反馈、处置与显式自动处理授权）；图执行内核仍用 runtime_version=1/2 |
| 复核日期 | 2026-09-21 |
| 事实来源 | `workflows/{schemas,graph_schemas,graph_service,planning,graph_tools,replanning,results,feedback_schemas,feedback,engine,coordination,allocations,service}.py`、`routers/workflows.py` |
| 验证入口 | [测试指南](../../../testing/README.md) |

## 权限与入口

所有端点以 `/api/conversations/{conversation_id}/workflows` 为前缀，要求当前 World Owner，且仍为本人创建的存活会话成员。Guest 不获得定义、分配、私有事实或控制权，只接收其可见会话的公开消息。跨会话对象返回 404，非 Owner 返回 403。

`GET /`（实际无末尾斜杠）返回 `{definitions,runs,coordinations,parallel_capacity,member_capabilities}`，禁止缓存。成员能力仅包含 role_id、name 和当前可分配的工具名称，不包含凭据或宿主根。列表是刷新、重连和事件缺口后的完整控制快照。

`GET /draft-scope` 返回 `{scope: string}`（64 位十六进制），要求同样的 Owner/会话访问权限且 `Cache-Control: no-store`。scope 是基于 World 密钥、World 名称、账号和会话创建身份的不可逆浏览器存储分区，不是访问凭据；不返回 Key 或 Token。密钥轮换后分区改变，旧草稿不会自动匹配。客户端在取得该分区后恢复本地草稿，始终保留原基准版本，显式提交仍走下述版本检查。浏览器存储细节见[前端设计](../../../design/frontend-navigation-theme.md)。

## 定义与保存

`PUT /definitions/{definition_id}` 接受 `{name,expected_revision,graph}`，返回 `{id,name,revision,graph}`。新定义 expected_revision=0，保存版本递增；ID 使用 1–64 位 ASCII 字母、数字、下划线或连字符。

节点是任务，同一角色可有多个不同工具集合的任务。保存允许尚未补齐执行语义的分支和环路；拒绝重复 ID/边、未知端点和失效引用。v2 草稿可为空图，模型任务可暂不填角色或任务；启动时必须补齐有效角色和任务。v1 兼容 PUT 保留原空任务/缺失角色校验。坐标和颜色不影响执行授权。

| 图字段 | 语义 |
|---|---|
| `runtime_version` | 1 或 2；兼容 PUT 和旧存量图缺省 1，新图写入工具缺省 2，旧图不自动升级 |
| `nodes` / `edges` | 节点列表与 `[source,target]` 对；无固定节点数上限 |
| `entries` | 多入口图必须显式列出全部入口；单入口可省略 |
| `concurrency` | 可空正整数；不超过主机 `WORKFLOW_PARALLELISM`，为空使用主机容量 |
| `edge_rules` | `{source,target,when}`；when 为 always/true/false |
| `loops` | 显式循环声明，见下节 |

| 节点字段 | 语义 |
|---|---|
| `id` / `title` | 稳定 ID 与展示名称；`__` 开头保留给协调阶段 |
| `kind` | role（角色任务）、approval（人工确认）、join（汇合）、condition（规则条件）、judge（模型判断） |
| `role_id` | role/judge 可设置；其他类型必须为空。协调模式下 judge 使用本群协调者 |
| `task` / `expected_output` | 本次任务与预期产出；草稿可暂缺任务，role/judge 启动时 task 不得为空 |
| `inputs` | 显式选择上游节点结果，启动检查图上的祖先关系 |
| `tools` | 可空工具名称列表；空列表表示不授予原生工具，null 在手动启动时按现有授权初始化，在协调模式由模型申请 |
| `result_keys` / `result_schema` | 必需结果字段及类型映射；类型为 boolean/integer/number/string/null，未显式指定时可由下游条件推导，冲突拒绝 |
| `condition` | condition/judge 必填的结构化规则 |
| `position` / `color` | 可空有限坐标 `{x,y}` / `#RRGGBB`；缺省按类型布局与配色 |

## 条件、并行与循环

普通节点出边为 always；完成后多个目标可并行就绪。判断节点必须有 true 和 false 出边。规则结构为 `{sources,key,operator,value,aggregate}`：sources 是上游结果节点，judge 还可使用 `$self`；key 为标量字段名；operator 为 eq/ne，aggregate 为 all/any。value 仅接受布尔、数字、字符串或 null。类型不匹配、结果缺失或无有效来源时阻断，不从自然语言或脚本推断。

多个入边均解决后才处理目标。失败依赖阻断下游；未选分支传播 skipped；汇合等待所有未跳过的必要分支。任一有效入边可激活目标，全部跳过则目标也跳过。inputs 引用 skipped 来源时不注入，不能使用旧成功替代当前失败。

循环声明：`{id,entry,decision,exit,body,repeat_when,max_iterations,carry_inputs}`。body 包含 entry 和 decision，exit 在域外；必须存在 decision→entry 回边和 decision→exit，true/false 由 repeat_when 决定。只有 entry 接受外部输入，只有 decision→exit 离开循环；体内所有节点从 entry 可达并可到达 decision。移除所有声明回边后必须是 DAG。当前支持互不重叠、互不嵌套的循环域；不支持隐式回边或跨域跳转。循环体内允许并行与汇合。

每轮创建独立 activation，同轮重试另有 attempt。iteration 从 0 开始。carry_inputs 只向循环入口显式交接上一轮的选定结果，其余输入使用本轮准确尝试。max_iterations 是 Owner 可选正整数，null 不设循环次数限制；达到上限会受阻，不自动发新预算。纯规则、无模型/人工工作的忙循环被拒绝。

## 协调与手动启动

`POST /runs` 返回 202，请求 `{definition_id,expected_revision,request_key,input_text,mode,feedback_mode}`。mode 缺省 manual；coordinated 要求群聊已显式任命有效协调者（见[会话管理](conversations.md)）。feedback_mode 缺省 manual，仅 coordinated 可选 automatic，授权在原运行范围和预算内自动处置反馈。普通发送、@、@全部继续原串行群聊规则，不进入协调模式。单聊及手动流程不依赖群协调者。

`POST /runs` 的 coordinated 模式保留旧固定图兼容：先编译，再由 workflow_plan 提交全部已有 role/judge 节点的分配。它仍不是从目标创建图的入口。新的前端“协调执行”使用下述独立协调会话；模型实际读/写/编辑图后显式调用 workflow_start。judge 使用协调角色进行结构化判断，workflow_summary 汇总真实结果。

新派发的 v2 普通工作/判断节点（即使使用协调者角色）获得自身分配的原生工具及 workflow_result，可按需报告反馈；没有结果契约时不强制提交。它不获得图管理权。旧已存 allocation 不扩大能力，未知职责默认无权。

本运行的模型调用、分支、迭代、重试共享同一冻结 WorkflowBudget。启动同事务保存快照、任命版本、工作区身份、触发消息和 chain；定义修改不影响既有运行。request_key 同会话同请求幂等，不同内容冲突。一会话只允许一个活动流程，普通聊天使用独立 chain。

配置、角色、资源、工具不足等检查在创建运行前或派发前完成；运行期间权限变化仍必须复核。没有 Git/worktree 或世界级协调者前置条件。

## 独立协调会话与 /plan

群聊输入 `/plan@协调者 <目标或调整要求>`，通过现有 @ 选择器绑定稳定角色 ID。前端发送明确协调请求，后端独立校验 Owner、当前群任命和目标；普通消息正文、引用或直接 POST messages 中的命令文字不产生权限。输入框显示“新草稿”或选中的定义，运行视图不能隐式取得重规划权限。

`POST /coordination` 返回 202，请求字段：

| 字段 | 语义 |
|---|---|
| role_id、goal、request_key | 当前任命角色、非空目标、同会话幂等请求键；同键不同内容冲突 |
| mode | design（缺省，草稿规划）、execute（规划后可启动）、replan（指定运行调整） |
| definition_id | 可空；空值预留新的草稿 ID，首次写入才创建定义，不伪造已启动流程 |
| run_id | 仅 replan 必填；必须为本会话 v2 运行 |
| expected_graph_revision | 已有定义/运行必填；使用目标最新图版本，与进度 revision 分开 |
| continue_session_id | 可选同群同目标、同任命的前次会话，创建新 execution 继续，不恢复旧模型状态 |
| protected_nodes | 可选 Owner 固定节点列表，保护业务字段、相连边及涉及的循环；省略沿用任务保护，显式列表是本次 Owner 的保护范围 |
| feedback_mode | manual（缺省）或 automatic；execute 启动时冻结到运行，design/replan 不修改已有运行的自动管理授权 |
| feedback_ids | replan 可选本运行反馈 ID 列表，最多 20 项；同事务关联此次处置，不能引用其他运行或已关闭反馈 |

同一尚未启动的目标草稿继续规划，沿用前次协调的 chain/预算；execute 启动复用这一预算，replan 复用目标运行预算。已用于另一运行的规划链不能再启动第二个运行；新运行是独立任务。换任命不能借前次会话恢复旧授权。定义不存在时的新草稿槽位也可通过 continue_session_id 延续。

返回协调 id、目标 definition_id/run_id、started_run_id、mode/goal、role_id/appointment_revision、status/revision、execution_id/chain_id/message_id、constraints、decision_limit/used_decisions、usage、时间与错误。completed 只表示本次模型结束；图是否提交以图修订记录为准。多次读/写/编辑不受一次性 attempt.result_json 限制。

- `POST /coordination/{id}/cancel`：携带 expected_revision，取消本协调 execution；如果本请求已启动运行，同时封闭该运行。针对原有运行的重规划请求只取消本协调执行，已提交图修订保留。
- `GET /coordination/{id}/message`：Owner 读取该执行的准确原消息/工具卡（可能尚为 null），禁止缓存。

设计请求只授予 read/write/edit/validate，不能启动；execute 额外获得 workflow_start；replan 额外获得 workflow_inspect_run/workflow_control/workflow_feedback_update。管理工具不授予文件访问。能力显式保存在 execution_allocations，与工具 schema、ContextBuilder 和实际工厂共享，并在每次调用及提交时重新检查。

## 图工具与统一提交

| 工具 | 职责 |
|---|---|
| workflow_read_graph | 当前或指定 graph_revision 的完整图；node_ids 可选择直接关联子图，返回 coverage 与 omitted_node_ids，部分响应不可用于整体覆盖 |
| workflow_write_graph | 整体写入完整 graph，可附 name；不与 edit 混用 |
| workflow_edit_graph | operations 为带 op 判别字段的按 ID 局部批次 |
| workflow_inspect_run | 当前授权运行的状态、版本、尝试、结果及原执行最小文件证据；不读取当前文件正文，不把缺失证据当未执行 |
| workflow_start | 携带 expected_graph_revision，启动授权定义；同协调请求重复调用不多建运行 |
| workflow_control | 在授权运行内 stop/retry/resume，使用运行 expected_revision；不能 confirm 或代签工具审批 |
| workflow_feedback_update | inspect_run 后处置本运行具体反馈；复用 Owner 处置服务，但不能代签人工核验、豁免问题或接受遗留 |

write/edit 均携带 expected_graph_revision、mutation_key、可选 validate_only（默认 false）。新目标图版本为 0；同目标修改键持久去重，摘要包含工具名和规范化请求。同键同内容返回原结果，换工具或不同内容冲突。只校验不创建定义/修订，正式提交仍复核当前版本与授权。模型整体覆盖前须取得完整当前图；部分读取会清除完整读护栏，完整编辑结果可作为后续完整图依据。

write 的 graph 遗漏业务节点表示请求删除，不隐式合并；已有节点未提供 position/color 时保留展示值。按 ID 比较完整差异，不删除重建全部身份；派生索引、执行状态、Owner、目标归属不能由模型参数覆盖。后台在副本完成全部变更，最终结构/引用/权限通过后才原子提交，失败不留下半批修改。

edit 支持 add_node、update_node（changes 只含设计字段）、remove_node、connect/disconnect（同时处理 edge_rules）、set_inputs、set_condition、upsert_loop/remove_loop、set_entries、set_concurrency。删除节点需在同批显式修复相关连线/输入/条件/循环；中间态不必可执行，最终引用必须有效。字段/节点与操作下标用于错误定位，不返回失败输入原文。

Owner REST 共用同一服务：

- `GET /graphs/{kind}/{id}?graph_revision=...`：kind 为 definition/run，读取当前或历史图，返回 graph_revision、graph、versions、compile_issues、members（含工具说明/schema）、覆盖范围和运行 edit_scope。
- `POST /graphs/{kind}/{id}/write`：与 write 工具相同字段。
- `POST /graphs/{kind}/{id}/edit`：与 edit 工具相同字段。
- 原整图 PUT 继续兼容并通过同一验证/提交服务；旧 PUT 不带 mutation_key，保留原 expected_revision 冲突语义。

提交返回图、新 graph_revision、修改 id、changes（新增/更新/移除节点及连线/配置差异）、status、replayed、executable/compile_issues。每次提交记录来源 execution 与 Owner；定义更新影响后续启动，运行更新必须显式指定 run 目标。

## 运行图修订与历史

运行 graph_revision 表示当前有效图，latest_graph_revision 包含最新已提交（可能待采用）修订，pending_graph_revision 指向等待循环边界的版本。graph_versions 给出版本和 applied/pending/superseded/not_applied 状态。进度 revision 不用作图编辑版本。

未派发节点（包括 waiting_feedback）可增删改并重算就绪依赖；已入队/活跃、人工等待或已处理节点的业务内容、入边与作用域冻结，仍可增加后继。新节点创建 activation，原未执行意图标为 superseded，旧尝试/文件事实保留。单纯修改已结束运行的图不恢复执行；明确 resume 或在反馈处置中 assign 新的待执行任务才继续，并检查同群不能已有另一活动运行。

活动循环的节点、连线或循环声明变更暂存为未来修订。受影响的循环分别交接完本轮判断后等待共同边界，无关分支继续；全部选择重复时原子采用新图、创建下一轮激活并检查新轮数限制。如果循环已退出或必要分支失败，修订标为 not_applied，原有效图继续按事实收口；不重跑旧轮。新的修订可替代尚未采用的修订。循环支持范围仍是互不嵌套的独立域。

阻塞反馈已暂停本轮交接时，可以保持循环入口、判断、出口、重复规则和上限，在本轮未派发区域局部补充处理节点。仍逐项检查已执行内容、入边和作用域冻结。改变上述循环边界返回 WORKFLOW_FEEDBACK_LOOP_BOUNDARY，避免“等下一轮采用处置节点、又等处置结束才能进入下一轮”的互等。

activation/attempt 新增 graph_revision；attempt.node_snapshot 保存派发所依据节点。旧记录保持 null，不倒推不存在的历史版本；首次纳入版本服务时仅保存标有 legacy 的当时基线。历史图通过精确版本读取，界面不把新图节点叠到旧版本。旧尝试与当前图的节点内容/循环作用域已经不兼容时，retry 返回 WORKFLOW_GRAPH_RETRY_VERSION；不能假借重试重写历史。循环达到上限后，Owner 可提交新的未来修订并显式继续，预算不重置。

workflow_result 的导出 schema 由节点结果类型与条件来源推导，非法值不保存，可以在原预算中修正。同一尝试相同结果重复提交返回原结果；执行仍活跃且结果尚未交接时，可携带上次返回的 expected_result_revision 修正报告，成功返回 result_revision；并发不同结果不能无版本覆盖。执行结束/已消费后旧工具对象无权改写。规划/汇总旧工具保留兼容，但不能更改真实运行终态。

## 节点反馈与处置

workflow_result 兼容新增 `feedback` 列表（缺省空，单次最多 20 项）。每项为 `{request_key,category,summary,details?,blocking?,requested_tools?,suggested_role_id?}`；category 为 implementation/contract/capability/unverified/suggestion，分别表示实现问题、契约冲突、能力缺口、未验证和建议。summary 为 1–240 字符，details 最多 8000 字符，blocking 缺省 true，普通建议应设 false。requested_tools 只核对实际能力，不授予工具；suggested_role_id 必须仍为本群可用角色。

结果与反馈在同一事务提交。模型不能传入来源身份；后台绑定 run、graph_revision、node、activation/iteration、attempt、结果 revision、实际角色及 execution。Owner 可以从明确尝试提交意见。原意见不可覆盖，后续处置只追加事件；result 保存反馈 ID 和幂等指纹，原消息/工具/文件事实继续通过既有接口读取。修正结构化结果不会隐式撤销已有问题。

| REST（Owner） | 请求与结果 |
|---|---|
| `GET /runs/{rid}/feedback` | `{items}`，禁止缓存；运行完整快照也返回 feedback 列表 |
| `POST /runs/{rid}/feedback` | 反馈字段另加 attempt_id；新建 201，同来源 request_key 同内容重发 200，不同内容 409 |
| `POST /runs/{rid}/feedback/{fid}/actions` | `{expected_revision,request_key,action,reason,...}`；返回最新反馈，版本冲突 409 |

处置 reason 不得为空、最多 4000 字符。action 及额外参数：

- `assign`：handler_role_id 与 handler_node_ids 必填，必须匹配本运行未派发的角色节点；记录精确 handler activation。先通过已有图服务安排任务，再关联反馈。
- `wait` / `review`：分别等待外部条件或复核，保留具体依据。
- `resolve`：模型必须引用 verification_attempt_id；该尝试须属于关联激活、仍为选中尝试、实际完成且 `values.feedback_resolved=true`，所有关联处理尝试均已完成。Owner 可明确 `manual_verification=true` 并填写核验依据。完成执行与验证结论分开，工具不靠一句文字承诺关闭反馈。
- `dismiss` / `accept` / `obsolete`：Owner 记录未采纳、接受遗留或已失效；accept 不是验证通过。
- `reopen`：Owner 重新打开已关闭记录，不覆盖原历史。
- `coordinate`：Owner 显式委托当前群协调者处理该反馈，包含处理完成后的复核；不把整个手动运行改为 automatic。

模型工具另传 feedback_id，目标 run 从本次 replan 授权绑定；只允许 assign/wait/review/resolve。每个处置有独立反馈 revision；重复请求不增加事件，同键异参拒绝。返回来源、source_current、category/summary/details、blocking/status/revision、处理角色/节点、验证尝试、协调会话、capability_check、history 和 graph_changes；历史区分 owner/role/system，图修改关联真实修订及采用状态。

反馈状态为 open/in_progress/waiting/review/resolved/dismissed/accepted/obsolete。节点模型结束保持原 attempt/execution 终态。当前选中来源的未关闭阻塞反馈，使尚未派发的后继成为 waiting_feedback，并暂停所在循环进入下一轮；明确关联的处理节点可以执行，无关分支继续。已启动的副作用不撤回，历史来源不阻塞新的选中结果。关闭阻塞反馈后只重新评估未派发依赖，原节点不重跑。已结束运行上补充意见不会把原完成记录改成失败。

automatic 运行在来源执行结束后将待处理项合并到新的 replan 协调会话；每次最多 20 项，同运行同一时间只认领一批，认领与授权创建原子提交。普通成功不唤醒协调模型。每反馈版本至多自动派发一次；处理尝试真实结束后再交回复核，沿用原 chain、预算和父 execution 关联。协调者无处置就结束、失败、取消或能力不足时，问题继续保留；不靠后台无限重试或重置预算。Owner 可核对后明确再次委托。

自动交接和复核重新检查当前任命、资源、会话、Owner 与原授权；撤权/停止/取消不复活旧授权。重启时含阻塞反馈的等待运行降为 interrupted，由 Owner 核对后 resume；浏览器刷新只读持久快照。WS 继续使用 workflow_updated 和 workflow_coordination_updated 的安全身份/版本提示，不广播反馈正文或另建 Trace。

## 状态与控制

运行返回 id、definition_id/revision、name、graph、runtime_version、mode、coordinator_role_id、appointment_revision、phase、status、revision、error_code、workspace_binding_id、input_text、decision_limit、used_decisions、created_at，以及 activations、attempts、loop_states。v2 cursor 返回 null；v1 仍为零基串行游标。

activation 返回 id、node_id、loop_id、iteration、status、attempt_id、error_code、current（是否为当前有效图/轮次选中的激活）。attempt 保留所有历史，含 id、node_id、number、activation_id、iteration、loop_id、phase、status、current、selected_in_activation、upstream_ids、retry_source_id、instruction、message/generation/execution_id、assigned_role_id、assigned_tools、waiting_resource、result、usage、created_at/ended_at。attempt.current 表示当前轮的选中尝试，selected_in_activation 表示该历史激活仍选择它。不可用的 usage 保持未知。人工/汇合/规则节点无模型执行身份。

运行状态 queued/running/waiting/stopping/stopped/failed/interrupted/blocked/completed；waiting 可能等待人工确认或反馈处置，激活还有 pending/active/waiting_feedback/skipped/dormant/superseded。waiting_resource 为 read/write/null，与模型排队状态分开。completed 表示 execution 结束，业务效果仍以工具事实为准。

`POST /runs/{run_id}/control` 总是携带 expected_revision：

- confirm：携带等待中的 attempt_id 和可选 decision（布尔，缺省 true），保存 `values.approved`。只确认该激活，其他分支继续。
- stop：先保存 stopping 封闭派发与回边，再取消本 chain 的全部排队/执行任务，等待收口。已提交事实保留，不影响其他 chain。
- retry：携带当前选中 attempt_id、acknowledge_facts=true、instruction、rerun_downstream。复核权限、事实与依赖后创建新尝试；有活跃下游则拒绝。只失效真实后继和被回退循环的未来轮次，独立并行结果及更早历史保留。false 仅重试本节点，结束后停止；true 允许继续下游。
- resume：显式继续剩余路径；当前必要尝试仍失败/停止/中断时必须先 retry。不会重跑已完成上游、清零预算或重放审批。

控制快照读取、角色/成员撤销及状态转移共用短控制边界，避免旧 revision 与新激活状态混合、或旧派发状态覆盖撤销标记。操作用短事务和 revision 冲突检测串行化；控制锁不跨 Provider、文件等待或人工等待。多 worker 仍不支持。

## 工具分配、资源与恢复

execution_allocations 冻结每个尝试的工具、资源与授权来源；上下文策略、工具 schema 和实际工厂使用同一分配。每次调用复核 Owner、成员、角色、任命、binding、generation 和精确选中尝试。全局工具变化不扩大已有分配；所需权限撤销会收口相关节点。取消/更换任命封闭旧协调运行，重新任命不自动接管。

共享读、排他修改发生在真实资源操作边界，lease 仅保存执行上下文。别名和祖先/子目录共用仲裁；等待有界、可取消，冲突写者不被后来读者插队。模型推理、人工确认和 diff 不持锁。准入后重新鉴权；等待期间保留 schema，不要求模型重传参数。详见[工作区](workspaces.md)。

复用既有 generation/execution/reducer 和父 execution 引用，无另建 Trace。普通聊天按会话串行，v2 流程使用有容量的并行队列，按 QueueJob 原子认领防重复。

服务重启把未完成运行降级 interrupted，不自动重放；人工 waiting 保留，但确认需重新鉴权。已完成原 execution 及结果保留，活动尝试被中断，资源等待元数据清除。浏览器刷新/重连只读快照；workflow_updated 仍只广播 run_id/status/revision，未知客户端可忽略。Owner 通过原 message/facts 接口核对精确历史尝试，权限撤销后原消息仍可读但事实接口可能拒绝。

## v1 兼容与画布

缺省 runtime_version=1：保存仍允许通用图，启动仅支持唯一完整串行路径。新增工具分配、节点类型或循环声明需要显式选择 v2；不猜测旧环路语义。v1 控制仍按串行游标及 selected 工作，历史运行不迁移成并行。

React Flow 负责选择、连线和位置；配置与操作在右侧工作流模块。Tab 在选中聚焦的编辑态节点后插入任务；Delete 仅删除选中连线或无连接节点。非编辑态和文本输入不触发删除。节点类型与颜色可修改；定义编辑不影响当前运行，运行图须通过明确修订入口更新。

数据迁移见[数据模型](../../internal/data-model.md) 0021–0023；错误码见[注册表](../../error-codes.md)。字段校验仅返回安全的 type/loc/msg 定位，不回显 Prompt 或无效输入。
