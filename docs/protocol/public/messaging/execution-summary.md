# 执行摘要

| 元数据 | 值 |
|---|---|
| 受众 | 公开 |
| 状态 | 废弃（停止生成新摘要；旧记录只兼容读取、不注入模型历史） |
| 协议版本 | 1（兼容新增 part） |
| 维护者 | Roleplex 前后端 |
| 事实来源 | [内部事实契约](../../internal/execution-facts.md)、现有消息/revision/工具详情实现 |
| 关联代码 | `app/services/chat.py`、`app/schemas.py`、`frontend/src/api/client.ts`、`store/chat.ts`、`components/MessageParts.tsx` |
| 关联测试 | `backend/tests/test_execution_summary.py`、`test_tool_reliability_t0.py`、`frontend/tests/commands/execution-summary.spec.ts` |
| 复核日期 | 2026-09-14 |

当前停止原因与工具证据见[消息协议](messages.md)。下列 v1 字段仅保留为旧记录兼容说明，不代表新回复继续生成摘要。

## 请求、事件与权限

不新增动作 endpoint。报告随原消息历史/快照和既有完整消息更新事件发送，复用会话成员鉴权、消息 revision、
event_seq 及断线恢复。只接受服务器创建的报告，客户端发送的同名 part 不能作为服务器事实或模型可信输入。
共享报告仅包含安全身份、状态与计数；Owner 私有目标/版本/输出仍通过原 message/call 详情端点读取。
详情端点每次复核 Owner 与会话归属，过期、撤权和不存在沿用原协议，不泄露外会话资源是否存在。

## 新增 part

实现约定：已有 `tool_call` 兼容增加 `effect_state` 与 `confirmed_applied_items`，只由消息所有者从提交凭据
提取；它们是本调用的安全事实，摘要从原工具卡派生，不能用 success 或原始输出长度推测。
新增消息的内部 meta 标记 `execution_summary_version=1`，用于区分服务器事实与旧记录/用户输入。
无工具的正常聊天不展示摘要；异常结束或存在工具时保存。客户端提交 execution_summary part 返回现有参数校验错误。

| 字段 | 约束与含义 |
|---|---|
| `type` | 固定 `execution_summary` |
| `version` | 固定整数 `1` |
| `part_id` | 原消息内稳定身份，同一执行仅一份，更新沿用，不随刷新产生新身份 |
| `stop_reason` | 运行中为 null；终态为 `completed/user_cancelled/graph_budget/provider_failed/protocol_error/interrupted/context_rejected` |
| `coverage` | `complete/partial/unknown`，只描述被记录的调用事实覆盖，不表示业务验收完成 |
| `calls` | 最多 32 个引用，按已观察的原调用顺序；不重排已有工具卡 |
| `calls[].call_id` | 已有宿主调用身份；没有宿主身份的未派发提议不伪造 call_id |
| `calls[].execution_state` | `running/succeeded/failed/rejected/cancelled/unconfirmed` |
| `calls[].effect_state` | `applied/not_applied/unknown/not_applicable`；批次只要不能完整表达即 unknown，私有逐项结果保留已确认部分 |
| `omitted_calls` | 非负整数，已知但因报告上限未列出的宿主调用数，不静默省略 |
| `undispatched_proposals` | 非负整数或 null；只有派发控制证据充分才计数，未知为 null |
| `confirmed_applied_items` | 已确认提交的原生文件节点数，不按路径去重，不等同不同文件总数或任务完成数 |
| `unconfirmed_calls` | 已知宿主调用中结果或副作用仍未确认的数量，包含被省略项 |

整个紧凑 UTF-8 JSON part 上限 16 KiB；先删调用引用并更新 omitted_calls/coverage，保留停止原因和汇总。
无法证明完整时 coverage 不得为 complete。Shell 等通用命令退出成功只确认执行状态，其未采集文件副作用仍为 unknown；已明确只读的原生文件/结构化命令为 not_applicable。
不暴露路径、hash、runtime 细节、脚本、Provider 调用 ID、原始参数/输出或自由文本诊断。
不新增 message/generation/tool_call 终态；stop_reason 与这些状态独立，错误码以注册表为准。

终态映射：正常结束沿用 generation completed / message done；用户停止与图预算触顶沿用 stopped；
Provider/协议失败沿用 generation failed / message error，后者使用注册表的 AGENT_PROTOCOL_ERROR；
启动中断沿用现有恢复规则。done 只表示生成结束，不承诺用户任务验收通过。context_rejected 表示模型调用前的上下文/权限检查失败，沿用对应已有错误码，不误称为 Provider 失败。

## 原位展示、幂等与兼容

报告仍保存在原消息中用于事实与上下文恢复，但聊天界面不渲染 execution_summary part，不展示计数、说明或摘要占位。
工具卡保持原身份和位置；图预算停止在已有消息状态处显示简短原因，失败/用户停止沿用原状态提示。
消息所有者按同一 part_id 更新而不是追加多份；完整消息更新/终态与报告在同一消息事务维护。
客户端按 revision/event_seq 幂等处理；快照覆盖陈旧报告，迟到旧事件不回退状态。

旧消息没有该 part 时不显示摘要缺失提示，不用当前文件重建。新版客户端也隐藏该 part 的未知版本；
不识别此类型的旧客户端仍可能显示未知内容占位。其他未知 part 的通用降级不变。
七天后仍可保留安全摘要，Owner 详情按原期限显示过期，不延长私有证据保留，也不把过期解释为未执行。
停服、服务跨轮保留及最终回收规则不受摘要影响；报告没有再次执行或自动重试入口。
