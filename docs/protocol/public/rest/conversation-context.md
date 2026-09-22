# 持久会话上下文与输入占用

状态：B 已实现，C 增加[主动压缩](context-compression.md)与[Memory 检索回读](memory.md)；自动策略阈值仍按 D 实施。入口为单聊/群聊右侧“上下文”，分为“占用与输入”“会话材料”“提示词”。提示词字段见[提示词配置](prompt-settings.md)。

## 内容、材料和调用

| 对象 | 当前行为 |
|---|---|
| 原消息 | 聊天页面的完整内容与执行事实；保留流式片段、失败、中断和工具记录 |
| 共享会话材料 | 每个会话唯一的 `conversation_contexts` 版本及逐消息投影；不按角色复制，不受页面分页影响 |
| 本次角色输入 | 从确定版本选材，加入规则、当前消息、角色相对身份、获准工具和中断事实；记录来源，不另存整份 Prompt |
| 每次模型调用 | 在防腐层从该次实际消息和绑定的工具 Schema 估算，包含执行内已经返回的工具结果；厂商 usage 单独记录 |

同步与消息所有者的 ORM 写事务一起提交或回滚。真人 `done` 消息及时纳入；`pending/generating` 只登记来源并标记 pending，流式增量不重复保存投影或增加材料版本。`done/stopped` 的非空投影成为 included，停止原因保留明确标记。空正文、仍有 running 工具、`error/interrupted` 为 excluded，记录原因；失败片段和已提交/未知副作用仍在原消息、工具详情及[中断事实](../../internal/interruption-context.md)中，不据此推断工作未执行。

普通请求排除当前消息及后来的其他消息；`group_role` 可额外读取同一 chain 中、构建开始时已可见的前序角色终态。自己的回复投影为 assistant，其他发言带 `[sender_type:sender_id]` 身份。当前用户消息只提供一次。工作流协调和节点继续只使用明确授权的任务及上游尝试，不混入整群历史。

构建冻结可见消息 ID、材料 revision、当前消息及采用来源的 revision/status。共享材料并发变化时用新短读事务最多重试三次，仍不稳定则拒绝；Provider 与工具操作不因这种重试重放。已发出的调用保持原输入，随后角色重新构建。每次模型调用前复核角色有效性、会话及触发者/角色成员资格，失效以 `context_rejected` 收口；原文件结果和执行记录保留。

## 读取共享材料

`GET /api/conversations/{conversation_id}/context`

仅当前 World 的 Owner 且为该会话成员可读取；回收站会话不可读取，恢复后沿用原材料。所有响应 `Cache-Control: no-store`。

参数：`limit` 默认 30，范围 1–100；可选 `before` 为正消息 ID；可选 `expected_revision` 为非负材料版本。继续翻页须带上上一页的版本；版本变化返回 409，客户端重新读取，不能拼接两个版本。

响应：

| 字段 | 含义 |
|---|---|
| `conversation_id/revision/projection_version/updated_at` | 共享材料身份、版本、投影算法版本和更新时间；不是会话配置 revision |
| `counts` | included/pending/excluded 来源数量 |
| `excluded_reasons` | 按 generating/failed/interrupted/empty/tool_pending 计数；包括待完成来源 |
| `text_bytes` | 已保存稳定投影的 UTF-8 字节数，不是 Token 或完整聊天大小 |
| `through_message_id` | 已登记的最高来源 ID，包括 pending；不是“所有消息已完成”的断言 |
| `entries` | 按消息 ID 降序；各项含 message_id/revision/status、sender_type/sender_id、state/reason、text/text_truncated/text_bytes、created_at |
| `next_before` | 下一页边界；无更多为 null |

每项展示正文最多前 4,000 字符，`text_truncated=true` 表示列表展示截断，材料本身不截断。pending 的 revision 是最初登记时的来源版本，收口后同步最新版本；它不提供仍在流式变动的正文。没有提供编辑/删除原消息或再生成接口；主动压缩使用[独立维护请求](context-compression.md)。

C 兼容新增 active_summary（id/source_count/through_message_id/text/text_bytes/created_at）、summary_unavailable 和 material_text_bytes。原 text_bytes/counts 仍表示稳定来源投影；material_text_bytes 用当前有效摘要替换覆盖原文后计算，未叠加角色规则/身份和工具。无有效摘要时使用原文。material 增加 summary_id/summary_omitted_reason，request.breakdown 增加 summary；预算内条数包含一段摘要，覆盖原消息不重复发送。

## 按角色预览

`POST /api/conversations/{conversation_id}/context/preview`

只读操作，无模型调用、生成记录、消息写入或压缩。Owner、会话归属、当前成员和目标角色有效性必须通过检查，长预览返回前重新鉴权。

```json
{"role_id": 12, "draft": "可选的本次消息草稿", "include_content": false}
```

严格字段；`role_id` 正整数；`draft` 默认空串、最多 100,000 字符；`include_content` 默认 false。目标角色必须属于当前 Owner 且在此会话中。只能预览普通角色请求，不接受任意 execution、World 或工作流权限参数。

返回 `role_id/role_name/model_name`、`shared`（上节统计，不含分页条目）、`material`、`request`、`latest_call`，以及可选 `messages`。前端在面板打开时对输入框草稿做防抖预估；草稿仅存当前页面内存，跨账号/World 不复用。预览与实际执行共用 ContextBuilder 的规则、能力和选材逻辑，期间新消息/配置变化可能使最终执行不同。

`material`：

- `scope`：conversation / workflow_upstream / workflow_coordination。普通预览总为 conversation；执行快照可为工作流范围。
- `revision`：采用的共享材料版本；工作流明确上游/协调任务为 null。
- `visible_through_message_id`、`current_message_id/current_message_revision`（未发送草稿为 null）。
- `sources`：按实际顺序采用的 `{message_id, revision, status}`；不包括当前消息或另行提供的中断事实正文。

`request`：

| 字段 | 口径 |
|---|---|
| `estimated_tokens/safety_margin_tokens` | 预算内已选输入及其安全余量 |
| `estimator_kind/estimator_version/is_provider_exact` | 当前 conservative_utf8_v2 / 2 / false |
| `effective_context_window` | 角色窗口与部署 ceiling 的较小值 |
| `output_reserved_tokens/input_budget_tokens` | 最大输出预留，以及扣除它后的输入预算 |
| `before_truncation_tokens/before_truncation_safety_margin_tokens` | 选取历史前的全部候选历史压力，不用裁剪后的低数值冒充未丢历史 |
| `included_message_count/truncated_message_count` | 采用与因预算未采用的稳定来源数量；pending/excluded 另看 shared |
| `blocked` | 固定规则、工具与当前消息已超过输入预算；允许展示原因，但实际发送会以 CONTEXT_BUDGET_EXCEEDED 拒绝 |
| `recovery_omitted` | 中断事实连最小提示也无法装入；未知副作用不能视为未执行 |
| `breakdown` | system（含 inline 技能）、tools、current、history、interruption 的 Token 估算；和为 estimated_tokens，不含输出及余量 |

显示压力为 `(before_truncation_tokens + before_truncation_safety_margin_tokens + output_reserved_tokens) / effective_context_window`，允许超过 100%。只对可裁剪历史执行预算选择：优先预算内 pinned，再选连续的近期后缀；工作流指定上游无法完整装入时拒绝，不静默裁掉。

`messages` 在 `include_content=true` 时包含按发送顺序排列的 `{type,content}`（system / human / ai）；工具定义在 A 的能力预览中单独查看。它包含 Owner 私有提示词与上下文，不能广播、缓存、记录原始请求/响应或用于正式日志产物。

## 最近实际调用与更新

`latest_call` 无记录时为 null。它是该角色最近真正开始的单次调用，可能属于工作流，不是整轮/累计消耗：

- execution_id/execution_kind/execution_status，call_index/status（started/completed/unconfirmed）。
- recorded_at、调用时 model_name/provider_mode，`input_estimate`（可空）和 `provider_usage`。
- input_estimate 包含估算器口径、估算值与余量，以及 message_tokens/tool_schema_tokens/message_count/tool_message_count；工具调用参数、名称、结果关联和已返回结果均计入。不包含 Prompt、工具输入输出或凭据原文。
- provider_usage 仅包含厂商报告的 input_tokens/output_tokens/cache_hit_tokens/cache_write_tokens；fake、未报告与旧记录均为 null，不用本地估算补齐。
- snapshot 为该 execution 首次实际采用的提示词/能力来源及 material/request。后续工具轮的估算记录在各次调用中，不覆盖首次来源；未开始模型调用的预检拒绝不伪造快照。

历史调用的窗口/预留取其 snapshot.request，不能套用角色现在的新配置。执行内每次调用从该次框架事件取输入，不读取可能已经推进到下一轮的可变图状态。停止/重启将未完整结束的用量标记 unconfirmed，保留已经报告的数字与原输入估算；缺失采集保持未知。

面板使用既有消息 WS 刷新稳定边界，运行期间每 2.5 秒读取调用级统计，不逐 Token 请求。C 兼容增加只含状态与版本的 context_updated 事件，没有含私有上下文的广播。断线/刷新从上述业务接口恢复，不依赖浏览器历史缓存；累计统计仍见[角色执行用量](execution-usage.md)。

## 存储、升级和错误

迁移 0025 建立所有旧会话的材料身份；服务启动先收口中断消息，再按每批 200 条差异来源回填，每批独立提交。重复启动只修复缺失/版本或状态失配的来源；中途退出保留已提交进度，未完成前不接受执行。查询用 SQL 聚合计算全量压力，按页选择来源，正文只加载预算内采用项。新会话包括空会话也有独立上下文。

`context/store.py` 在 ORM flush 后同步消息创建、终态、修订与删除，原事务失败一起回滚；回收站只改变读取资格。未来原消息修改仍须遵守 revision 和同事务同步，批量 SQL 绕开 ORM 时必须显式调用 sync_sources。新压缩段及发布版本由 C 扩展，不以启动回填覆盖未来用户发布的压缩内容。模型及索引定义见[数据模型](../../internal/data-model.md)。

错误沿用公开错误信封：未登录 401，Guest 403，无权会话/角色 404；参数不合法 422；`CONTEXT_SOURCE_CHANGED` 409（源版本变化，重新读取）；`CONTEXT_PREVIEW_UNAVAILABLE` 422（上下文无法组装）；`CONTEXT_STORAGE_UNAVAILABLE` 503（存储读取失败，无 SQL 参数或正文）。执行侧用既有消息终态报告错误。代码与说明见[错误注册表](../../error-codes.md)。
