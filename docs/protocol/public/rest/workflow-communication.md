# 协作消息来源、广播与执行输入

状态：已实现，2026-09-23。适用于普通聊天、世界委派、群协调与 v1/v2 工作流。任务和工具授权仍由原服务负责；消息文字、显示身份和 @ 都不能授予权限。实施记录见[修复计划](../../../plan/world-coordination-message-provenance-v1.md)。

## 公开消息与作者

现有 Message payload 兼容新增可空 communication；来源保存在服务端 meta_json，客户端消息创建不能指定或覆盖它。包含 version=1、kind、actor、recipients、authorized_by_user_id、source、report_to 和可空 via。

actor/recipients/report_to/via 是受信身份引用：kind 为 user/role/world_manager/system，id 限当前 World，name 为创建时公开名称快照。世界管理者 id 为岗位身份，role_id 为当时实际执行角色；角色引用可带 duty=group_coordinator。historical=true 表示历史名称没有可靠快照，使用原角色/用户 ID 展示，不能用新管理者名字替换历史作者。

| kind | 含义 |
|---|---|
| chat | 普通用户输入/角色回复，收件人来自显式 mentions 或触发者 |
| delegation | 世界管理者向真实群协调者委派目标 |
| coordination_request / coordination_reply | 人工规划要求或已授权协调者答复；后台反馈交接使用 system 主体 |
| workflow_goal / workflow_started | Owner 启动目标，或既有委派启动流程的状态；后者引用目标，不再复制全文 |
| workflow_dispatch | 系统按图派发的批次卡，via 标明依据的群协调者或 Owner 配置 |
| workflow_result | 实际角色的节点结果/判断/汇总，关联原执行和默认报告对象 |
| legacy_execution_input / legacy_workflow_record | 已确认的旧执行输入，或关联不完整的历史工作流记录 |

公开 sender_type/sender_id 使用已核实主体；老数据库中的原始字段和正文作为证据保留，不能当成新的授权依据。实际角色回复保留原角色身份。source 只含允许的 coordination/run/batch/图版本/轮次/execution/回复等引用，不包含模型配置、完整输入或私有工具结果。

回复以 reply_to_id 关联原消息。真人请求的 reply_to_id 必须属于当前会话；不存在/越界返回 MESSAGE_NOT_FOUND。回复关联、通知对象与执行触发分开：普通群聊仍按真人 mentions 顺序回复；系统广播、角色正文 @、读取、重连和回放均不会再调用普通发送路由。

## 一条广播与多个尝试

v2 的批次身份由 run、激活图版本、循环域/轮次、阶段、依赖及条件边语义构成；显式重试单独分批。相同公共阶段的角色共享一条消息，即使执行槽位不同步，也分别显示等待/运行/终态。不同条件分支、图版本、循环或重试不会因为正文相同而合并。

广播的成员快照记录 activation、节点、角色及准确 attempt。状态从原尝试读取；未派发项关联本激活最早的相应尝试，后续重试不覆盖旧结果。v1 串行流程也采用来源明确的节点卡和独立输入，每个串行尝试有单独批次。

节点依然独立调用模型、计量、提交结果及停止。批次/消息、输入与执行队列由宿主在同一事务关联；公开卡片读取没有派发副作用。状态变化通过已有 workflow_updated/消息流及按需查询反映，不反复写成共享上下文的新指令。

| 接口 | 权限与结果 |
|---|---|
| GET /api/conversations/{cid}/messages/{mid}/dispatch | 当前会话成员；id/message_id/run_id/revision/status/items。items 只提供原节点/角色/尝试、execution、状态和错误，不含执行专用文字 |
| GET /api/conversations/{cid}/workflows/runs/{rid}/attempts/{aid}/input | Owner 且当前仍拥有并加入会话；text、source、execution_id、legacy。按准确尝试返回完整输入，未调用模型的节点可为 null |
| GET /api/conversations/{cid}/messages/{mid}/input | Owner 回读已标识为 legacy_execution_input 的原文；关联缺失仍保留原始记录，其他消息返回 EXECUTION_INPUT_NOT_FOUND |

全部读取禁止 HTTP 缓存。未知批次/越界返回 WORKFLOW_DISPATCH_NOT_FOUND，原尝试错误沿用 WORKFLOW_ATTEMPT_NOT_FOUND。

## 执行输入与模型上下文

ExecutionInput 以原 execution_id 关联 owner/conversation、公开消息锚点、可空 batch_id、冻结的节点输入 text 和 source_json。它存放任务输入及来源，不是全量系统 Prompt 或第二套执行账本。工作节点不再把私有输入保存为 Owner 聊天消息。

ContextBuilder 通过实际 execution、目标角色、Owner、消息锚点、generation 状态及原 allocation 核对输入。普通用户消息继续原校验，系统消息不能单靠 sender_type 放宽准入。工作流仍只读取准确选中的上游，输出与权限约束保持原规则；每次模型调用/自动维护继续校验已采用输入的指纹。

Context schema 10 增加本次 speaker/duty、input_from、reply_to、mode 和通信提示；材料凭据记录 execution_input 的身份/指纹，提示词来源记录 communication 层。普通预览和实际聊天共用默认对象；世界对话会告知当前查看模式，执行模式工具仍由真实授权决定。平台默认协作模板为 v2，Owner 自定义覆盖不被重写。

共享上下文只投影公开目标和来源明确的说明/结果；广播的进度查询不复制为多条指令。来源指纹包括通信语义，私有输入按原受权详情回读，不进入公共摘要或通用 Memory 搜索。

## 历史升级与恢复

启动时依据原 coordination/run/attempt/execution/世界子关系分批识别旧自动消息，保存 communication 并递增受影响消息 revision；原文字、旧作者字段、ID、文件事实、调用与用量不删除。没有可靠关联时显示来源未完整记录，不按相似正文猜测批次，也不合并旧消息。

旧节点输入的共享投影标为 excluded/execution_input，公开正文提供历史输入提示，Owner 可以展开原文。上下文投影版本为 3，依赖旧身份/输入的摘要和 Memory 引用失效；原摘要版本仍保留，回退也须重新验证来源。升级本身不调用模型重写摘要、不重新执行旧任务。

来源回填产生 communication_updated（payload.reload_history=true）；客户端清理旧窗口缓存并重新读取来源，沿原鉴权、事件序列、快照及请求失效机制恢复。表结构由 0032/0033 创建，业务回填可重复；存在新专用身份或执行输入时无损降级无法保证，迁移明确拒绝，详见数据模型。
