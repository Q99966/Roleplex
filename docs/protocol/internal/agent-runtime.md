# Agent 运行时：领域事件、工具安全与 MCP 生命周期

| 元数据 | 值 |
|---|---|
| 受众 | 内部（不承诺客户端兼容性） |
| 状态 | 原型（M0 风险验证结论，产品接入见后续里程碑） |
| 维护者 | Roleplex 后端 |
| 事实来源 | `backend/app/agent/`、`backend/app/mcp/manager.py` |
| 关联测试 | `backend/tests/test_agent_loop.py`、`test_agent_pipeline.py`、`test_mcp_manager.py`、`tests/contract/` |
| 复核日期 | 2026-08-18 |

本文记录 Agent 运行时的内部约定和 M0 风险验证的实测结论。这些是内部契约：客户端不得
依赖，公开行为只出现在 [消息协议](../public/messaging/messages.md) 与
[WebSocket 协议](../public/websocket/conversation-stream.md)。

## 领域事件

业务层（调度、数据库、WebSocket、前端）只消费这一组事件，框架私有事件名不得越过防腐层。

| 事件 | 含义 | 关键字段 |
|---|---|---|
| `TextDelta` | 模型输出的一段文本增量 | `text` |
| `ToolCallStarted` | 一次工具调用开始 | `call_id`、`tool_name`、`args_summary` |
| `ToolCallFinished` | 一次工具调用结束 | `call_id`、`status`、`duration_ms`、`output_summary` |
| `MessageDone` | 本轮正常结束 | `text`（最终全文）、`usage` |
| `ProviderError` | 本轮失败 | `code`（稳定错误码）、`message` |

`MessageDone` 与 `ProviderError` 互斥，且必然是流的最后一个事件。取消不是事件：
`asyncio.CancelledError` 原样向上传播，由调度层按 `stopped` 收尾。

`ToolCallFinished.status` 取值：`ok`（正常返回）、`rejected`（危险级别在执行层被拒绝）、
`error`（工具自身抛错）。被拒绝属于正常结束路径，本轮生成仍应完成。

## 防腐层边界与实测结论

框架事件到领域事件的转换全部收敛在 `app/agent/loop.py`，测试用源码扫描保证其他模块
不出现框架事件名。锁定版本（langgraph 0.2.74 / langchain-core 0.3.36）下的实测结论：

- **步数上限不会抛异常**。达到 `recursion_limit` 时事件流直接结束，不抛 `GraphRecursionError`，
  留下一条带未配对工具调用的助手消息。因此防腐层自己统计"最近一轮模型回合里还没拿到
  结果的工具调用数"，非零就判定为触顶。异常路径同时保留，兼容会抛异常的版本。
- **收尾调用不能带未配对的工具调用**。触顶后追加一次禁用工具的收尾调用时，只发送原始
  输入加收尾提示；把那条含未完成 `tool_use` 的助手消息回传厂商会被直接拒绝，反而让整轮失败。
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

- API Key 只在 provider 工厂内经解密边界进入模型对象；日志只记录厂商类型、模型名、
  角色标识和参数键名。
- 采样参数按"厂商白名单 + 模型能力"过滤：不在白名单的键直接丢弃；开启思考模式或能力表
  声明不支持时，剔除 `temperature` / `top_p`。
- 能力表（是否支持视觉、并行工具调用等）内置默认值，可被模型配置的能力覆盖字段按厂商覆盖；
  真实差异以契约测试结论为准，不在业务代码里写死。
- 是否连真实厂商由配置开关控制，默认使用确定性 fake provider，保证普通回归与端到端测试
  不联网、不计费。

### 契约测试实测结论

真实厂商验证放在独立的契约测试层（运行方式见 README），下表是已实测厂商的结论。
新增厂商时补一行；数字是观测值而不是承诺值。

| 厂商 / 模型 | 复核日期 | 结论 |
|---|---|---|
| OpenAI 兼容（DeepSeek，`deepseek-v4-flash`） | 2026-08-18 | 文本按小分片真流式到达（44 字回复分 23 片，首片 2 字）；工具调用单次往返正常并能继续作答；`temperature` / `max_tokens` 直接可用；无效 Key 返回 401 并映射为 `PROVIDER_AUTH_FAILED`；取消在 0.02 秒内返回 |
| Anthropic | 未验证 | 尚未配置凭据，能力表沿用内置默认值 |

其他共性结论：

- **用量口径已被 LangChain 统一**：领域事件里的 `usage` 直接取模型返回的 `usage_metadata`，
  字段为 `input_tokens` / `output_tokens` / `total_tokens`，并可能带 `input_token_details.cache_read`
  （命中提示缓存的输入量）与 `output_token_details.reasoning`（思考输出量）。不需要按厂商各写一套解析。
- **厂商错误信息本身可能带打码后的 Key 片段**，所以错误信息只能按稳定错误码消费，
  不得原样回显给客户端，日志侧也保留脱敏处理。

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
