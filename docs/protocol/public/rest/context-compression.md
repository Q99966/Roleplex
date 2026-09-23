# 会话压缩策略与摘要版本

状态：主动压缩、自动策略和执行内容量检查已接入。原材料与统计口径见[会话上下文](conversation-context.md)，原文查找见[Memory](memory.md)。

## 范围与存储

主动压缩的对象是当前单聊/群聊的共享会话上下文。来源范围、维护任务和活动摘要都由 conversation_id 确定，来源不按发言角色筛选；会话内各角色在自己的预算与执行边界允许时采用同一摘要。

Owner 在“上下文 → 主动压缩”查看当前会话，独立选择生成摘要的模型、最近保留消息数、目标摘要长度及本次保留重点。现阶段模型选择借用本会话角色已有配置，角色只承担模型配置与执行用量关联，不是被压缩的对象。压缩页不沿用占用/检索的“查看角色”，模型选择与未提交要求按会话保留。压缩不创建聊天消息，原消息、工具详情和文件事实保留；一次性要求不覆盖平台、世界、角色、会话提示词或未来自动策略。

维护请求创建 `Generation`（无 assistant_message_id）、`AgentExecution.execution_kind=context_compact`、独立 chain、`WorkflowBudget` 和既有 QueueJob。trigger_message_id 可空只用于此维护链。调度、停止和 usage 仍由既有设施管理，聊天快照及普通“停止生成”排除维护 generation。压缩任务在自己的面板停止，角色执行用量包含其真实模型调用并标记维护类型。

每个会话最多一个 queued/running/stopping 的共享材料压缩请求；执行私有压缩不占用该指针。共享请求由可空唯一 active_conversation_id 约束。来源另以 context_compression_sources 保存消息 ID、revision/status/text_hash；ID 是历史凭据，原消息删除后仍保留，供旧摘要判失效。发布的 context_summaries 不可变，活动指针独立；会话材料 summary_revision 只在发布/回退时递增，防止指针来回切换后旧任务误采用。

## 创建任务

`POST /api/conversations/{conversation_id}/context/compressions` → 202

全部入口要求当前 World Owner、会话归属和实际成员；响应不缓存。所选角色必须存活、启用、属于当前 Owner 且在本会话内，模型配置也归当前 Owner。

```json
{
  "request_key": "本次维护请求的唯一标识",
  "expected_revision": 12,
  "role_id": 3,
  "keep_recent": 6,
  "target_tokens": 1024,
  "instructions": "保留当前目标、关键决定和未完成事项。"
}
```

严格字段：request_key 1–128 字符；expected_revision 非负；role_id 正整数；keep_recent 默认 6、范围 0–200；target_tokens 默认 1024、范围 128–100000，且小于所选有效窗口；instructions 默认空、最多 10000 字符。可选 through_message_id 为当前会话已稳定消息的 ID，提供时显式指定上界，否则根据 keep_recent 计算。数值是当前产品参数边界，不是跨任务工程约束。

role_id 指定用于生成摘要的模型配置和维护执行身份，不是来源角色过滤器；更换它不会产生一份独立的角色上下文或改变会话归属。

同一 `(conversation_id, owner_id, request_key)` 幂等。相同请求重复返回原任务；同键不同参数 409，不重新调用模型。前端丢失响应先按 request_key 核对，未确认的重试复用原身份；只在 sessionStorage 保留请求键，不在那里保存私有压缩输入。

默认保留最近未压缩的完整消息；置顶消息不压缩。既有有效摘要作为不可拆分单元，与新选来源一起压缩；范围不得越过生成中消息或拆掉已有摘要的一部分。没有可压缩来源时明确拒绝，不偷偷生成空摘要。来源快照和排队在短事务中提交，模型等待不持有写事务。

冻结所选模型名、有效窗口、实际支持的采样参数、配置身份、提示词模板版本、会话要求与本次输入。只保存 Provider 实际允许的参数，不复制 API Key 或无效的额外参数；配置身份只用不可逆 hash。后续模型配置变化会拒绝旧任务，不切换到另一个模型。

## 执行与发布

压缩专用模型不获得工具。使用固定的历史/事实规则，不把角色 persona 或历史指令当作当前执行任务。每项输出须为 `{text,sources:[消息整数ID]}`，分为 facts/open_items/conflicts/inferences；校验结构、引用和长度，拒绝未知来源。语义质量仍需实际模型验证，结构合法不等于事实永远正确。

单次输入按所选窗口、目标输出和安全余量计量。长历史按完整消息分组，再合并摘要；一条必需来源装不入时拒绝，不截断后冒充完整压缩。合并每轮减少段数，单段直接沿用，不能合并两段时终止，避免不限决策下反复收费。每次模型调用消费同一维护 chain 额度，调用索引在同一 execution 内连续；Provider usage 只用已报告字段。

服务器把原消息的停止原因、工具状态/副作用计数作为 execution_facts 加回结果，模型不能删改这些记录。它们只表达已观察的状态，不等同任务验收；未知不等于未执行。必要事实装不入目标时保持原版本。

发布前重新检查 Owner、角色/成员、模型配置、每条来源、当前摘要指针和 summary_revision。新消息追加不使任务失效，发布后仍在尾部；来源修改、删除、重新置顶、撤权或回退竞争使旧结果不采用。结果须有实际缩减。摘要、活动指针、材料 revision 和任务结果同事务提交，失败回滚到当前材料。

状态：queued/running/stopping；终态 completed（已采用）、unchanged（无收益/必要事实过大）、cancelled（Owner 停止）、interrupted（服务中断）、stale（来源或授权变化）、failed。失败/取消不重放模型、工具或文件操作。重启对未结束请求记 interrupted，保留已发布摘要、已返回用量；未完整结束调用为 unconfirmed。相同请求键仍返回该终态。

## 进度、停止、回退

- `GET /api/conversations/{id}/context/compressions`：最近 30 个任务和摘要版本；可传 request_key 精确核对旧请求。
- `POST /api/conversations/{id}/context/compressions/{compression_id}/cancel`：幂等停止；queued 可直接收口，running 先记停止请求再取消实际 runner。已发布结果保留。
- `POST /api/conversations/{id}/context/restore`：`{expected_revision,summary_id}`，null 恢复原文，其他值采用本会话仍有效的历史摘要；不删除后来消息。

job 返回 id/request_key、角色/模型/execution、来源版本与上界、保留设置、要求、source_count、input_tokens_estimate/output_tokens_estimate、completed_calls、phase/status/error_code、取消标记和 UTC 时间。材料估算是共享来源的本地口径；它不是整次任务厂商消耗。GET 的 usage 复用[执行用量](execution-usage.md)，可核对已记录/已返回调用、缺失和厂商累计 Token；模型输出无效也不抹掉实际调用。

versions 返回 id/active/valid、text、source_count、through_message_id 及前后估算。来源失效时 text 为 null，不能重新采用旧版本。普通上下文预览也会退回原文；当前执行边界不覆盖全部摘要来源、或该角色预算装不入摘要时单独降级，不混入未来消息。工作流明确上游继续单独组装。

兼容事件 `context_updated` 的 payload 只有 revision、compression_id（可空）、status，不广播指令、摘要或引用原文；Owner 面板读取 REST 获取正文，旧客户端可忽略该事件。事件用于版本变化提示，运行时另按调用边界轮询进度。

## 错误

新错误以[注册表](../../error-codes.md)为准。创建前的无权资源 404、参数/范围/窗口问题 422、版本/幂等/在途冲突 409；存储异常 503，不回显 SQL 参数。执行开始后的 source_changed、model_changed、budget_exceeded、source_too_large、merge_too_large、result_invalid、output_too_large、no_gain 等保存在任务 error_code，不能用 failed 统一猜测调用没有发生。

## 自动策略（0027）

Owner 通过 `GET/PUT /api/context-policy` 管理当前世界默认，`GET/PUT /api/conversations/{id}/context/policy` 管理会话覆盖。会话仍检查归属与成员，响应 `Cache-Control: no-store`。PUT 为 `{expected_revision,policy}`；`policy=null` 在会话表示恢复继承，在世界表示恢复软件默认。读取、保存策略不调用模型。

| policy 字段 | 默认值与含义 |
|---|---|
| enabled | false，需显式启用自动维护 |
| trigger_tokens | null，动态使用推荐值；显式值 256–10000000，会话保存时不得超过上限 |
| reserve_tokens | 50000，仅影响推荐值，可设 0–10000000；不是硬性扣除 |
| target_tokens | null，触发值的一半；显式值 128–10000000 且小于显式 trigger_tokens |
| summary_tokens | 1024，摘要长度上限 128–100000，实际生成时还考虑目标和保留尾部 |
| keep_recent | 6，保留 0–200 条最近未压缩消息；置顶消息始终保留原文 |
| model_role_id | null 表示显式选择“跟随当前执行的角色模型”；指定时必须是可用成员配置，失效暂停而不静默换模型 |
| instructions | 自动保留要求，最多 10000 字符；与主动压缩的一次性 instructions 独立 |
| cooldown_seconds / min_new_tokens | 60 秒 / 1024；冷却及新增稳定材料门槛，范围分别 0–86400 / 0–1000000 |

世界读返回 revision/policy。会话另返回 inherited、world_revision、effective、limits、notices、stamp。limits 包含全部有效成员的 role_id/name/window_tokens、ceiling_tokens、recommended_reserve_tokens 和 recommended_trigger_tokens。有效成员是启用、未删除、属于会话 Owner 且模型仍可用的角色；窗口沿用 `min(角色窗口, 部署窗口限制)`。

公式：`ceiling = min(全部有效角色窗口)`；`recommended_trigger = ceiling - recommended_reserve`。默认建议预留 50k；若建议大于等于窗口，推荐改为窗口的一半并返回 reserve_adjusted。举例 200k、1M、100k 的上限为 100k，推荐触发 50k。推荐值可低于用户选择的阈值，实际调用始终另算固定规则、Schema、输出与安全余量。

每次读取/执行重算窗口。窗口缩小后不覆盖原 policy，effective 的阈值和目标收紧，并返回 threshold_clamped/target_clamped；无有效角色或指定模型不可用时 effective.enabled=false。未设置显式触发值就跟随最新推荐值，不创建多个角色各自的长期会话上下文。

前端在压缩页展开“自动压缩设置”，展示公式、各角色窗口、实际值及提示；继承、未保存草稿、版本冲突分别显示。世界默认位于设置的“提示词与规则”。设置变化供之后的调用边界采用，已发出的请求不改写。

## 自动执行与私有材料

自动维护创建 `trigger=automatic` 的 context_compact 子 execution，parent_execution_id 指向触发任务、chain_id 沿用原链，不创建新预算，不排到当前会话队列后等待。每次维护调用原子消费原 chain 额度，并为原任务留至少一次后续调用。Owner 取消维护可继续原任务；停止原任务会取消并等待子任务收口。创建已提交但尚未开始执行的取消间隙也会收口，重启不重放。

共享材料采用 `scope=conversation`。在原任务初始构建、裁剪前判断压力，范围不越过当前用户任务或任何生成中来源。相容的竞争者复用同一已发布摘要，不把未来消息纳入旧任务；正在排队的手动维护不会阻塞原任务等待自身。来源指纹、数据库幂等键、共享材料唯一在途请求及冷却/增量共同避免重复收费。私有工具轮在实际窗口超限时可提前维护新闭合内容；相同输入失败不在同执行中重试，原链额度仍生效。失败保留原材料，下一次实际调用仍经过硬预算。

每次实际 Provider 调用前计算完整消息、实际工具 Schema、原输出预留和安全余量。执行期间工具增长、工作流长上游正文需要维护时，创建 `scope=execution` 的私有维护任务；只替换完整配对的闭合工具轮或本尝试明确上游的正文。工具结果先由原消息所有者落库，再交接给私有压缩，避免图状态领先执行记录时把已提交误标为未知；交接缺失时拒绝派发。原图状态和工具执行记录不改写。迭代、attempt、结构化判断值、当前任务及中断事实保持原文；不能用全群摘要替换明确上游。旧 Memory 来源在维护调用及继续原任务前都重新鉴权。

私有正文和摘要仅存于该 execution 的内存，进程结束后不恢复运行；数据库只保存来源版本/hash、计量、服务器效果事实及维护状态，不建立 ContextSummary 或 Memory 条目。每次采用私有材料的 `model_call_usage.input_estimate_json.runtime_context` 记录压缩任务 ID、来源 hash/数量和该次窗口，不覆盖首次 context_snapshot。框架子图回调隔离在 Agent 防腐层，避免维护模型被父执行重复统计。

私有任务 completed 只表示有效摘要已生成；真正送入模型的采用事实以对应调用的 runtime_context 为准，不能把生成成功等同原任务已经继续。

job 兼容增加 trigger（manual/automatic）、scope（conversation/execution）、outcome。共享自动成功后的 outcome 为 `{target_tokens,material_tokens,target_reached}`，按该次冻结边界的摘要及保留尾部估算，不包括后来追加消息。目标是期望值；最近消息、置顶及必要执行事实可能使结果仍高于目标，界面展示实际值。不能装入必需输入时返回 CONTEXT_BUDGET_EXCEEDED，不拆断工具对、不删掉未知副作用，也不自动再执行已产生效果的工具。
