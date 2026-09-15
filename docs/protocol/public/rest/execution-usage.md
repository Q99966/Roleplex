# 角色执行用量

| 元数据 | 值 |
|---|---|
| 受众 | 公开 Owner API |
| 状态 | 已实现待验收，仅观测，不新增预算限制 |
| 协议版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-15 |
| 事实来源 | ModelCallUsage、AgentExecution、领域 ProviderCallStarted/Completed、execution_usage 服务及对应测试 |

GET /api/conversations/{conversation_id}/roles/{role_id}/usage：要求 Owner、有效 Token、会话成员、未删除会话及属于该 Owner 的未删除角色；角色须为当前成员或在本会话有 execution。Guest 为既有 Owner 鉴权拒绝，无归属会话返回404，角色不可用返回ROLE_NOT_FOUND。响应no-store。运行中查询是近实时观察，不承诺多个聚合读取构成同一计费事务快照，下一次刷新可能补齐在途记录。
返回 latest（最近execution或null）与 cumulative（本会话此角色全部execution，非跨会话/跨World总量）。latest 包含 execution_id、message_id、status、error_code、stop_reason、model_name、duration_ms 和 summary。
summary：executions、untracked_executions、untracked_messages、recorded_calls、completed_calls、missing_call_records；metrics 的 input_tokens/output_tokens/cache_hit_tokens/cache_write_tokens/model_duration_ms 各为 {total,known,missing_calls}。
全部历史execution可追踪、决策计数无缺口且每次调用都提供该字段，total才是完整的已报告累计；否则total=null。known仅对已报告字段求和，没有报告则null；已知没有调用且无缺口时为0，不把丢失记录当作0。
recorded_calls是模型框架开始/完成的已记录调用身份数，SDK内部HTTP重试不单独计数，未报告的重试消耗无法纳入，不代表完整费用账单；missing_call_records由已消费决策数与已记录调用数的缺口保守判断，不能断言每个缺口都是一个已发HTTP请求。
latest.duration_ms使用generation的实际开始/结束时间，运行中或未开始为null；model_duration_ms 是已记录模型调用耗时合计，不能把并行调用耗时相加当作整轮墙钟耗时。stop_reason沿用消息协议，正常结束不代表任务验收。
缓存读取量是输入统计子项，不再次加到输入；缓存写入同理。厂商缺失的字段保持null，fake Provider不写伪造Token。旧execution不从日志或当前文件反推用量，标为未采集。早期缺少execution关联的角色回复以untracked_messages标记，不能误当零用量；存在这类回复时累计total同样为null。

每条model_call_usage以execution_id/call_index唯一，不同execution不会串账；开始与完成更新同一记录。完成重复交付不能重复累计或清空已保存字段；未知/失败/取消留存已知记录。
采集失败不得阻止已授权任务继续，记录缺口按未知显示；正式日志只保存允许的失败类型与关联标识。原始请求/响应、凭据和源码不进入用量表或接口。
该接口不提供价格估算、费用硬封顶、资源追加授权或自动恢复，也不改变模型提示词/工具定义。

前端展示缓存未命中输入、输出、缓存命中和输入缓存命中率；原始 input_tokens/cache_write_tokens 保留于接口。
metrics 兼容新增 cache_miss_tokens 和 input_cache_hit_ratio，同样使用 {total,known,missing_calls}。
仅当同一调用的 input_tokens 与 cache_hit_tokens 都存在且 0≤命中≤输入时，该调用参与派生统计：
未命中为各有效配对的（输入−命中）之和；命中率为这些配对的命中总量÷输入总量，取值0..1，不平均各调用比例。
缺失或无效配对计入 missing_calls；存在缺失、旧执行、旧回复或记录缺口时 total=null，known 可返回有效配对部分，前端明确标为已记录部分。
输入分母为0时命中率的 total/known 均为null；已知无调用时未命中量为0。不能通过不完整的原始累计值直接相减或相除。
