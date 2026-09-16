# Agent 运行时：领域事件、工具安全与 MCP 生命周期

| 元数据 | 值 |
|---|---|
| 受众 | 内部（不承诺客户端兼容性） |
| 状态 | 聊天执行、工具与上下文已实现；MCP 为内部验证，Orchestrator 未接入产品 |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/agent/`、`backend/app/context/`、`backend/app/scheduling/`、`backend/app/mcp/manager.py` |
| 关联测试 | `backend/tests/test_agent_loop.py`、`test_agent_pipeline.py`、`test_context_builder.py`、`test_group_chat.py`、`test_mcp_manager.py`、`tests/contract/` |
| 复核日期 | 2026-09-16 |

本文记录 Agent 运行时的内部约定和 M0 风险验证的实测结论。这些是内部契约：客户端不得
依赖，公开行为只出现在 [消息协议](../public/messaging/messages.md) 与
[WebSocket 协议](../public/websocket/conversation-stream.md)。

## 领域事件

业务层（调度、数据库、WebSocket、前端）只消费这一组事件，框架私有事件名不得越过防腐层。

| 事件 | 含义 | 关键字段 |
|---|---|---|
| `TextDelta` | 模型输出的一段文本增量 | `text` |
| `ToolCallStarted` | 一次工具调用开始 | `call_id`、`tool_name`、`args_summary` |
| `ToolCallFinished` | 一次工具调用结束 | `call_id`、`status`、`duration_ms`、`output_summary`；W1b 可选 `command_summary`，见 [命令契约](workspace-commands.md) |
| `ProviderCallStarted` | 一次框架模型调用开始（不等于逐次 HTTP 尝试） | `call_index` |
| `ProviderCallCompleted` | 一次框架模型调用成功结束 | `call_index`、`ttft_ms`、`duration_ms`、输入/输出/缓存读写 token 与可选命中比 |
| `MessageDone` | 本轮正常或预算停止收口 | `text`、`usage`、`stop_reason`、`undispatched_proposals`；见[执行事实](execution-facts.md) |
| `ProviderError` | 本轮 Provider 或协议失败 | `code`、安全 `message`、`stop_reason` |

`MessageDone` 与 `ProviderError` 互斥，且必然是流的最后一个事件。取消不是事件：
`asyncio.CancelledError` 原样向上传播，由调度层按 `stopped` 收尾。

ToolCallStarted/Finished 新增 repr 隐藏的 private_input/private_output，仅显式采集当前工作区工具的有界内容，
交给消息所有者加密保存。不得直接序列化领域事件到日志/共享 WS；公开状态与私有记录在同一消息事务维护。
采集、权限与保留见[工具执行详情](../public/messaging/tool-details.md)。文本增量由聊天所有者按节流批次或
工具边界落库，delta_seq 计数持久事件批次，不代表 Provider Token 数。

`ToolCallFinished.status` 取值：`ok`（正常返回）、`rejected`（危险级别在执行层被拒绝）、
`error`（工具自身抛错）。被拒绝属于正常结束路径，本轮生成仍应完成。
W1b/W1c 命令取消时调度层在等待进程回收后，以 `cancelled` 补齐未收到结束事件的审计与工具卡；不恢复模型调用。
W1c 使用 GuardedTool 的宿主调用身份绑定审批，与 ToolCallStarted/Finished 的 call_id 一致，不接受模型传入身份。
Shell 始终 dangerous，Owner safe override 无效；等待/执行语义见 [Shell 审批](../public/messaging/shell-approvals.md)。

## ContextBuilder 边界

E1 编辑工具只提取 path_fingerprint、old_text_bytes、new_text_bytes、has_expected_sha256 作为参数安全摘要，
结果仅向共享工具卡增加显式白名单中的固定错误码。匹配/参数/版本拒绝是正常 rejected 终态，不记录片段、路径或异常原文。
工作区策略版本随工具集合/说明升级；仅在实际暴露 workspace_edit 时向 write 的说明追加局部编辑建议。
E2 workspace_read 的 items 形式参数审计只含 item_count，不提取私有路径/内容；共享结果只含固定读取/批量错误码。
read/write/edit 各自的单文件与 items 共用原权限，不新增独立批量工具；策略版本随实际定义变化，不把 E2 当时的版本 13 当成当前常量。
S2 原生修改以类型化拒绝交付准确原因；身份/归属失败仍不披露资源信息，能力与服务/清理门槛分别判断。
诊断推荐工具取本轮实际暴露集合与当前权限的交集；完整诊断仅供模型与 Owner 详情，审计/共享事件只提取固定错误码。
S1 workspace_service_status 的工厂与策略描述独立于文件 lease/启动开关，仍要求当前 Owner single 与角色显式权限。
共用身份/成员/execution 复核，查询不取得文件访问或控制能力；查询审计只提取 mode/has_cursor 与合法 limit，
不记录 runtime_id 或游标原文。模型输出列表仅通过 Owner 私有详情采集，不进入共享工具事件或机器日志。
批量修改参数审计仅 item_count；输入私有采集保留目标/版本/长度，差异和逐项状态通过 write-batch-v1 私有作用域交给消息所有者。
批量修改先全批预检、逐项授权并提交，锁外等待 D 计算；非事务、不自动重放、不擅停服务，完整边界以工作区协议为准。
逐项读取及取消结果复用下述文件采集作用域，
由宿主 call_id 关联，不新建子项 Agent execution；仅模型工具结果和 Owner 加密详情可见逐项内容。

D write 私有采集由 generation 所有者创建有界作用域，GuardedTool 的宿主 call_id 关联凭据，
写入处同步记录已提交的前后版本并预留有界计算槽，释放写锁后计算；防腐层在工具结束时仅通过
ToolCallFinished.private_output 交给消息所有者。原文不进入模型工具返回或共享框架事件，未消费凭据上限 32 个。
取消时消息所有者保存已观察到的写入事实并清除作用域；进程崩溃不补造差异，不另造 Trace 或业务调度器。
详情与兼容字段以[工具详情](../public/messaging/tool-details.md)为准。

单聊和串行群聊角色通过 `app/context/` 构造模型输入；Orchestrator 尚未接入。当前实现以已落库用户消息 ID
作为严格截止边界，只读取更早的终态消息，并按目标角色投影为 LangChain history。相同 message ID、
revision 和 context schema 必须产生相同投影；请求/执行随机标识不进入自然语言 Prompt。

M 多行输入将内部 context schema 从 1 升为 2：非空正文的首尾空格、缩进和换行不再被投影裁剪，
纯空白正文仍视为空。稳定身份前缀、未知 part 降级、timeline 拼接和终态历史选择规则不变。
新构建使用新版本及对应指纹；不重写旧消息/日志，不重放已有执行，不新增数据库字段或公开消息版本。

M4a `group_role` 在上述历史之外，还读取当前真人消息之后、同一 chain 中已经提交的前序角色终态回复；
后一个角色只有在前一个 `message_done` 提交后才开始 build。其他 chain、generating 占位和失败半成品仍不可见，
单聊继续保持原有严格截止边界。

## T2 搜索与范围读取

workspace_search 按现有 Owner 单聊或串行群聊/dangerous 边界独立启用，查询参数审计只提取 mode/query_bytes。
read 的字节/行/批次与 search 共用有界扫描准入；取得槽位后及返回前复核权限，逐项结果只给模型和 Owner 加密详情。
工具策略和真实工具说明同步包含主机正文/扫描预算，规则见[搜索与范围读取](workspace-search-read.md)。
扫描阶段在同一 tool_call_id/Trace 下记录队列与扫描耗时/字节，不生成每轮统计摘要或额外模型请求。

## M4a 会话串行调度

- 每个会话一个进程内 worker，顺序消费已提交的 `queue_jobs`；不同会话 worker 可以并行。
- 同一真人消息创建一个 chain ID；每个目标角色拥有独立 generation、queue job 和 execution ID。
- job payload 只保存消息/角色/触发用户等稳定 ID、权限类别和 request/execution/chain 关联，不保存 Prompt。
- worker 开始前重新校验 generation 仍为 queued；角色成员关系、Owner 归属、active/墓碑状态在生成入口再次
  校验，成员变化后不能靠旧 job 恢复权限。
- 停止按 chain 标记 queued job cancelled、queued generation stopped，并取消当前子执行；其他 chain 与会话
  不受影响。服务重启不重新调用 Provider，遗留 queued/running job 降级为取消状态。

## E0 持久 execution 身份

- 每个迁移后新建的 single/group_role generation 在同一请求短事务中创建唯一 `agent_executions` 行；任一写入
  失败整条消息接收事务回滚，不能留下只有 generation 没有 execution 的半状态。
- scheduler 通过 `generation_id` 读取 execution 的 `execution_id/role_id/chain_id/execution_kind`；queue payload
  只负责当前消息、触发用户、权限类别和 request ID，不是执行身份来源。
- job 开始、正常完成、失败和停止时，execution 与 queue job 在同一调度事务更新；generation/message 仍由
  唯一消息 reducer 管理，调度器不抢写流式消息。
- 服务关闭先取消实际 active runner；若 generation 已由 reducer 收口为 stopped/completed/failed，同一 shutdown
  流程立即对齐 execution/job。runner 未收口则 execution 记 interrupted，不能遗留假 running。
- 服务启动独立扫描所有 queued/running execution，即使旧 generation 已先成为 stopped 也会改为 interrupted，
  写入 `EXECUTION_INTERRUPTED` 和 ended_at；active generation/job 沿用 stopped/cancelled，Provider 不重放。
- E0 不为迁移前已经结束的 generation 解析 JSON 回填 execution；M4b 父子、dispatch 和 attempt>1 尚未实现。

角色上下文窗口默认 200K，并受部署 ceiling 约束。未知 tokenizer 使用明确标记的保守 UTF-8 估算，不能
冒充 Provider usage。预算不足时产生 `CONTEXT_BUDGET_EXCEEDED`，在调用模型前失败。

## 防腐层边界与实测结论

框架事件到领域事件的转换全部收敛在 `app/agent/loop.py`，测试用源码扫描保证其他模块
不出现框架事件名。下面记录锁定版本（langgraph 0.2.74 / langchain-core 0.3.36）在早期阶段的探针结果；当前决策配置/图保护以[执行预算](agent-budget.md)为准，中断事实以[中断上下文](interruption-context.md)为准：

- **同一锁定版本既会抛异常，也会静默结束**。2026-09-14 T0 探针纠正原“不会抛异常”的结论：
  重复工具脚本在上限 1/3/5/15 抛 `GraphRecursionError`，2/4/6 静默结束；这是该脚本的观测，
  不是对任意图的奇偶规则。静默路径原始模型回调含工具提议，但图最终状态已将最后消息替换为无调用兜底。
  旧版 pending 计数只证明模型回调结果未配对，不能单独证明触顶或未执行。
- **T1 已移除不完整的额外收尾调用**。工具事实由消息所有者保留，不生成每轮统计摘要；图预算停止与协议异常分别处理；
  不再用 pending 数量猜测触顶，不再追加缺少事实/角色前缀的模型请求。上下文 schema 4 移除逐轮统计摘要注入，
  正常历史保留原正文，失败/中断消息不自动进入历史。契约见[执行事实与可信收尾](execution-facts.md)，
  修复后实测见[T1 验证记录](../../testing/tool-reliability-t1.md)。
- **T0 真实 Provider 对照**（2026-09-14）：DeepSeek V4 Flash 正常预算下确认实际提交；专用 3 步探针中，
  文件已提交但额外收尾返回 `write_status=not_done`。完整两轮浏览器产物验收通过，第二轮仍触发默认 15 步上限。
  样本、实际用量与覆盖边界仅在上述验证记录维护；不是所有厂商或任意任务的普遍结论。
- **fake provider 与真实 provider 走同一条路径**。确定性模型实现完整的 `BaseChatModel`
  接口（含 `bind_tools`），因此普通回归测试覆盖的是真实链路而不是旁路。

### 厂商错误码映射

不导入任何厂商 SDK，按状态码与异常类名判断：

| 条件 | 错误码 |
|---|---|
| 429 或限流类异常 | `PROVIDER_RATE_LIMITED` |
| 401/403 或认证、权限类异常 | `PROVIDER_AUTH_FAILED` |
| 400 或非法请求类异常 | `PROVIDER_BAD_REQUEST` |
| 超时类异常 | `PROVIDER_TIMEOUT` |
| 其他 | `PROVIDER_ERROR` |

## Provider 工厂与能力表

- API Key 只在 provider 工厂内经解密边界进入模型对象；日志记录厂商类型、模型名、角色标识、参数键名，
  以及移除 userinfo/query/fragment 和疑似凭据 path 后的实际 base URL。
- 采样参数按"厂商白名单 + 模型能力"过滤：不在白名单的键直接丢弃；开启思考模式或能力表
  声明不支持时，剔除 `temperature` / `top_p`。
- 能力表（是否支持视觉、并行工具调用等）内置默认值，可被模型配置的能力覆盖字段按厂商覆盖；
  真实差异以契约测试结论为准，不在业务代码里写死。
- 正常运行默认连接角色绑定的真实厂商；普通回归与端到端测试在各自入口显式启用确定性
  fake provider，保证自动化测试不联网、不计费。

### 契约测试实测结论

真实厂商验证放在独立的契约测试层（运行方式见 README），下表是已实测厂商的结论。
新增厂商时补一行；数字是观测值而不是承诺值。

另有显式执行的真实浏览器 smoke，用于验证前端、后端、provider 和消息持久化的完整链路。
它使用独立数据库且默认不进入普通回归；Key 仅由后端播种器加密落库，浏览器测试关闭
trace/video，具体运行与留存约定见 README。

| 厂商 / 模型 | 复核日期 | 结论 |
|---|---|---|
| OpenAI 兼容（DeepSeek，`deepseek-v4-flash`） | 2026-08-24 | 文本按小分片真流式到达；工具调用单次往返正常并能继续作答；`temperature` / `max_tokens` 直接可用；无效 Key 返回 401 并映射为 `PROVIDER_AUTH_FAILED`；取消可及时传播；真实浏览器链路已取得首分片耗时、输入/输出/总 token 和缓存命中 token |
| Anthropic | 未验证 | 尚未配置凭据，能力表沿用内置默认值 |

其他共性结论：

- **用量口径在防腐层统一**：优先读取 LangChain `usage_metadata`，并兼容 OpenAI-compatible、
  DeepSeek 和 Anthropic 的原始 usage 形态，统一为 `input_tokens`、`output_tokens`、`total_tokens`、
  `cache_hit_tokens`、`cache_write_tokens` 和可选 `cache_hit_ratio`。Anthropic 原始 `input_tokens` 不含
  cache read/create，防腐层恢复为完整输入口径；DeepSeek 的 `prompt_cache_miss_tokens` 只表示未命中，
  不冒充厂商没有报告的缓存写入。厂商未报告的字段保持为空，不按字符数估算。多轮工具调用会为每次模型
  调用成功时产生一个 `ProviderCallCompleted`，`MessageDone.usage` 只在每次调用都报告对应字段时才汇总。
- **厂商错误信息本身可能带打码后的 Key 片段**，所以错误信息只能按稳定错误码消费，
  不得原样回显给客户端，日志侧也保留脱敏处理。
- **两轮真实历史与缓存复核（2026-08-28）**：浏览器第一轮要求模型记住随机验证码，第二轮能准确回显，
  证明业务生成链路确实传入 ContextBuilder 历史而非测试旁路。DeepSeek V4 Flash 本次观测为第一轮
  input/cache=`229/0`、第二轮=`267/128`，第二轮 TTFT 从 396ms 降至 122ms；数字只是一轮样本，不能作为
  Provider 命中率或时延承诺。
- **正常世界 real-world 复核（2026-08-29）**：临时世界通过 `run_world_server.py` 启动，健康检查确认
  `world_managed=true`，真实 Key 使用世界独立加密密钥落库；相同两轮验证码对话通过。本轮两次
  input/cache 为 `229/128`、`264/128`，证明真实世界路径与兼容数据库 smoke 使用同一 ContextBuilder，
  但缓存数字仍只属于该次 Provider 观测。

## W1a 原生工作区工具

- 只在 Owner 触发的 single 或 group 会话、角色显式启用、会话绑定 active/available 工作区且 execution lease ready
  时暴露 `workspace_list/read/search/write/edit`；Guest、未绑定和能力关闭时连工具 schema 都不可见。
- ContextBuilder 在 Provider 调用前按实际暴露集合把 W1a 工具策略计入预算和 `tool_policy_hash`；随机
  execution ID 不进入稳定 hash。
- 每次实际调用重新读取 execution、Owner、角色、会话、binding 和 lease，重新 canonicalize Workspace 绝对根；
  注册时通过不能替代执行时授权。
- list 稳定分页并只报告 symlink，read/write 不跟随；write 使用 exclusive create 或 expected hash +
  同目录临时文件 + fsync + atomic replace。
- 工具输入/结果只回模型。日志和 `tool_calls.args_summary` 只保存路径 SHA-256、字节数、分页/offset、hash
  是否提供、workspace/execution ID、状态与耗时，不保存目录名、文件正文、写入内容或绝对路径。
- 服务启动不重放文件操作；遗留 ready lease 记为 retained/interrupted，物理目录保持原样供 Owner 检查。

## 工具危险分级与审计

- `safe` 是显式白名单，只包含只读或纯生成类内置工具；MCP 工具、未知工具和有副作用的
  工具一律 `dangerous`。Owner 可以按具体工具名显式放开，但不能整体放开某个 MCP server。
- 分级判断和拦截发生在工具执行层的包装工具里，不依赖系统提示词、工具名自述或模型配合。
  被拒绝的调用返回结构化拒绝结果回传模型，让本轮正常收尾，而不是抛异常中断整条链。
- Owner 触发的链路允许 dangerous 工具（本机可信主体），Guest 触发的链路默认拒绝。
- 每次工具调用在结束时写入审计表：触发用户、角色、消息、工具名、参数摘要、状态、耗时。
  摘要只做截断，不保存凭据或完整敏感内容。

## MCP 生命周期（stdio）

- **宿主任务模型**：每个 server 一个常驻任务，在同一个任务内打开 `stdio_client` 与
  `ClientSession` 并顺序消费请求队列，结果通过 Future 回传。关闭即取消该任务，
  保证底层取消作用域同进同出。
- **连接池键** = `(Owner, 配置指纹)`。配置 MCP 等价于授予本机代码执行权，即使当前只有
  单 Owner 也不跨主体共享。
- **超时分层**：初始化超时远大于调用超时（首次启动可能需要下载依赖），调用超时到期后
  向调用方抛出超时，由调用方决定重试还是重建。

Windows 实测结论（mcp 1.2.1）：

- SDK 直接把命令名交给子进程 API，**不做任何 `.cmd` 解析**，因此 `npx` 这类批处理包装器
  必须先自行解析出真实路径（按 `PATHEXT` 查找）。
- SDK 的默认继承环境变量列表**不含** `PATHEXT`、`COMSPEC`、`PYTHONIOENCODING`，需要显式补齐，
  否则包装器解析和中文输出都会出问题。合并后的环境可能含服务自身凭据，不得整体写入日志。
- SDK 退出时只终止直接子进程，并且**会等待它自然结束**。`.cmd` 包装器下真正的服务进程是
  孙进程，只靠 SDK 会留下孤儿；卡在长任务里的服务还会让关闭一直挂着。因此关闭顺序是
  **先按进程树清理，再取消宿主任务**，退出后再兜底清理一次。

## 关联代码

- 领域事件：`app/agent/domain.py`
- 防腐层：`app/agent/loop.py`
- Provider 工厂与能力表：`app/agent/providers.py`
- 工具分级与执行层拦截：`app/agent/tools.py`
- 确定性模型：`app/agent/fake_provider.py`
- MCP 生命周期：`app/mcp/manager.py`
- 生成链路与审计写入：`app/services/chat.py`
- W1a 路径、文件与工具：`app/workspaces/`

原生修改的独立准入与审批等待分离已实现并通过人工验收；等待阶段/原因见[工作区契约](../public/rest/workspaces.md#原生修改的有界等待)，进程内等待不增加模型请求，不改变 Agent 图预算。

小阶段 5 增加 ToolCallsNotDispatched 领域事件：同响应未派发提议整体交接，不伪造工具开始；状态、私有输入与取消边界见[执行事实](execution-facts.md#小阶段-5图预算阻止派发的提议)。

T3 将文件工具策略版本递增至 17，使工具定义缓存更新；workspace_edit 的兼容参数和原始版本匹配语义见[工作区契约](../public/rest/workspaces.md#t3-多片段编辑)。不改变调用预算或权限。

模型决策、框架模型调用、SDK HTTP 重试、工具和图步数的口径，以及当前预算停止边界，见[执行预算与计数](agent-budget.md)。

### 可恢复的工具参数拒绝

有可靠调用 ID 的参数 JSON/schema 错误或不可用工具，在工具正文执行前返回 TOOL_ARGUMENT_INVALID / TOOL_ARGUMENT_JSON_INVALID / TOOL_NOT_AVAILABLE。模型可在原决策预算内另发修正请求，不自动修补 JSON 或重放已执行操作。
ToolCallsNotDispatched.reason 兼容增加 arguments_invalid / tool_unavailable，UndispatchedTool 兼容可选 argument_error（固定 error_code、issues 数组，元素为 path/reason；无参数值），记录中使用宿主 UUID，不新增 Trace。
终止错误分为 AGENT_TOOL_CALL_ID_INVALID、AGENT_TOOL_CALL_MISSING、AGENT_TOOL_RESULT_MISSING、AGENT_TOOL_RESULT_MISMATCH、AGENT_EVENT_STREAM_INCOMPLETE、AGENT_RUNTIME_ERROR；AGENT_PROTOCOL_ERROR 保留历史兼容。Provider 响应明确截断仍为 PROVIDER_RESPONSE_INCOMPLETE，不派发其中任何工具。

ProviderError 兼容可选 error_type/error_phase，仅传输固定安全诊断标签至既有失败日志，不返回原始异常。

工具主动抛出的 ToolException 使用 TOOL_EXECUTION_FAILED 返回模型并保留实际开始/结束；不回显异常正文，也不声称未执行。其他未知执行异常仍终止为 AGENT_RUNTIME_ERROR，避免把已发生副作用的故障当成安全参数重试。

工具说明整理阶段使用策略版本 18；后续版本以 `workspaces/tools.py` 的实际 `workspace_tool_policy` 为准。现行结构：用途/关键规则/典型示例置于工具 description，参数用途和互斥关系置于 Field.description，实际数值约束仍由原 schema/执行层强制。workspace_tool_policy 的 exposed_tools 增加 parameters，预算估算与指纹覆盖完整原生参数 schema；该对象只用于内部估算与指纹，不重复注入 system_prompt。没有新增动态发现或“先标题再加载详情”的行为。

Context schema 5兼容增加中断事实交接，原文投影规则保持；具体边界以[中断上下文](interruption-context.md)为准，不恢复旧图状态或新增关键词入口。
