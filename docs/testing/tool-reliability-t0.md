# T0 工具可靠性基线与版本探针

| 元数据 | 值 |
|---|---|
| 受众 | 开发、协议与测试维护者 |
| 状态 | T0 确定性基线、真实 Provider 验证与最小设计完成，待人工验收；T1 产品修复未实施 |
| 版本 | 1 |
| 维护者 | Roleplex |
| 事实来源 | `backend/tests/test_tool_reliability_t0.py`、`tests/contract/test_tool_reliability_real.py`、本页列出的既有测试、锁定依赖源码 |
| 复核日期 | 2026-09-14 |

## 范围与可重复命令

本页保留修复前取证结果。T1 已实施并复测，当前状态与人工验收见[T1 验证记录](tool-reliability-t1.md)；
同名探针的收尾断言已随修复更新，下列结果不是修复后重复运行的预期输出。

本阶段按用户“开始下一步开发”推进 T0，交付确定性复现、事实盘点和 T1 最小契约。
没有提高图步数或读取额度，没有接入新增产品工具、系统摘要或自动重试；没有升级依赖或修改数据库结构。
测试通过说明现状与缺陷可重复，不表示缺陷已经修复。标有 `current_*` 的探针应在对应修复时改为目标行为。

在 backend 目录、既有 roleplex Python 环境执行：

```bash
python -m pytest tests/test_tool_reliability_t0.py tests/test_agent_loop.py tests/test_agent_pipeline.py tests/test_workspace_read_many.py tests/test_workspace_batch_mutation.py tests/test_write_diff.py tests/test_context_builder.py -q
```

上述确定性组合 **91 passed**，全部 fake、不联网计费，没有启动浏览器或持久服务进程。
用户随后明确要求补充真实 Provider 验证，下文单独记录联网计费结果，不将两类证据混同。
目录/留存复用[测试指南](README.md)：数据库由各集成 fixture 隔离并重放迁移，文件探针使用
`roleplex-command/test-<时间戳>/case-*/`。本页只记录无敏感数据的验证结论，不记录模型原始消息或文件内容。

## 锁定版本实测

本机实际安装与 requirements 一致：LangGraph 0.2.74、langchain-core 0.3.36。
探针直接运行真实 `create_react_agent` 与 GuardedTool；非流式模型分支仅用于检查现状额外收尾。

| 图上限 | 模型调用前 remaining_steps | 实际工具完成数 | 原始图结束方式 |
|---:|---|---:|---|
| 1 | 0 | 0 | GraphRecursionError |
| 2 | 1 | 0 | 正常返回兜底消息 |
| 3 | 2, 0 | 1 | GraphRecursionError |
| 4 | 3, 1 | 1 | 正常返回兜底消息 |
| 5 | 4, 2, 0 | 2 | GraphRecursionError |
| 6 | 5, 3, 1 | 2 | 正常返回兜底消息 |
| 15（现默认） | 14, 12, 10, 8, 6, 4, 2, 0 | 7 | GraphRecursionError |

上述次数只对本探针“每次模型请求都提议一个非 return_direct 工具”的图成立，不能推导通用奇偶规则或工具额度。
框架源码 `prebuilt/chat_agent_executor.py` 的 `acall_model` 在 remaining_steps 不足时替换模型结果；
模型结束回调发生在替换前。因此静默退出时最后回调仍有调用，图最终消息已没有调用，已完成的工具结果仍在图历史内。
生产识别不能依赖兜底文本。T1 必须在防腐层使用受控状态/派发证据，不能在业务层读取框架私有事件。

身份探针验证：外层 GuardedTool 的工具开始/结束 run ID、工具 ContextVar 与领域 call_id 对应；
结束 ToolMessage 的 tool_call_id 对应模型提议 ID，两种 ID 不相等。只有已观察配对才有映射，不按工具名推断。
未配对历史在模型调用前被框架以 INVALID_CHAT_HISTORY 拒绝；这证明当前图校验，不冒充真实厂商契约验证。

## 缺陷与保护基线

| 场景 | 证据与现状 | 归属 |
|---|---|---|
| 正常回复/工具往返 | 原 `test_agent_loop` 验证正文、领域事件配对和真实 fake 路径 | 保留 |
| 文件写入后异常/静默触顶 | 新探针在本轮隔离目录实际写文件并独立读回；收尾输入只有原用户输入与额度提示，模型无法取得本轮结果 | T1 |
| 角色规则遗漏 | 图的 system_prompt 不在额外收尾输入；当前累积正文也只用于输出拼接 | T1 |
| 未派发提议与已发生部分副作用后事件缺失 | 分别注入两种事件流，无 GraphRecursionError；现状都以额度提示收尾 | T1 |
| 收尾模型失败 | 新探针注入超时；只有 ProviderError，没有独立系统摘要，不递归收尾 | T1 |
| 用户取消等待工具 | 真图中副作用后阻塞，再取消生成；实际工具 finally 完成，不追加模型调用，已有副作用仍在 | 保留并扩展 T1 摘要 |
| 提交后取消、迟到线程/计算与清理 | 原批量修改/write diff 测试验证已提交凭据、未知写入、清理未确认不再准入和过期排队项不迟到启动 | 保留；不声称已实现终态后事实合并 |
| 两个 4 字节文件各申请 32 KiB | 新集成探针通过真实工厂、消息 reducer 与详情端点，确认任何 read 前即拒绝，read_batch=null | T2 |
| 预检拒绝误显示“未记录” | 集成探针固定上游 payload；`ToolCallCard.tsx` 对非 running 的 null read_batch 显示“逐项结果未记录” | T2；本阶段为源码定位，未做浏览器截图验证 |
| 第三个短时并发读取 | 原 read_many 准入测试在两批授权占用时立即返回 WORKSPACE_BATCH_BUSY，无排队 | T2 |
| 崩溃/加密失败/七天过期 | 原 write diff、批量修改/读取测试验证不补造历史、不重写文件、保存降级与私有边界 | 保留 |

“否认修改”由输入敏感的确定性测试替身复现：有结果才确认，缺失结果返回无法确认。
这证明收尾缺少证据，不代表所有真实模型都会说同一句话；随后取得的真实矛盾证据见下一节。原生提交/取消凭据由既有文件层测试另行验证，
不把探针闭包文件写入声称为完整产品工作区调用。

## 真实 Provider 补充验证（2026-09-14）

用户已明确授权，运行前已告知联网与费用。实际使用 **DeepSeek `deepseek-v4-flash`**，
OpenAI-compatible 工厂，经 `https://api.deepseek.com` 调用；Anthropic 未配置，本次未执行。
环境为 Linux/WSL2、Python 3.12.13、Node v24.14.0。没有提高产品默认图上限或重跑以获得特定答案。

### 1. 真实模型的正常/触顶对照

显式命令（backend 目录）：

```bash
python -m pytest tests/contract/test_tool_reliability_real.py -m contract -k openai_compatible -q -s --tb=no
```

结果 **2 passed，2 deselected**。这里只选择已配置的 OpenAI-compatible 两个预算场景；默认不带
`-m contract` 的收集检查为 **4 deselected**，不会因 `.env` 有凭据自动计费。
“通过”表示实验完成、实际写入且触顶分支被观测到，不表示模型回答正确；回答一致性独立列出。

真实模型依次调用受控 `probe_commit` / `probe_verify`；工具复用 `WorkspaceFileService` 实际提交/读取。
提交后生成的随机回执只通过工具结果返回，用户输入中没有回执。最后要求模型给出受限 JSON 判断。
测试透明包装 `_wrap_up` 观察其输入，未替换真实模型、修改收尾输入或伪造工具结果。
这属于文件层/Agent 循环的受控对照，不包含浏览器和产品工作区授权工厂；完整产品路径由下一项覆盖。

| 观察 | 默认 15 步对照 | 专用探针 3 步触顶 |
|---|---|---|
| 文件确已提交且独立读回一致 | 是 | 是 |
| 模型驱动的核验工具已执行 | 是 | 否 |
| 额外 `_wrap_up` 已执行 | 否 | 是 |
| 收尾输入有提交回执/工具消息/角色系统前缀 | 不适用 | 均无 |
| 真实模型 `write_status` | `confirmed` | **`not_done`** |
| 真实模型 `verification_status` | `done` | `not_done` |
| 回执匹配 | 是 | 否 |
| Provider 请求完成数 | 3 | 3（包括额外收尾） |
| 应用领域终态 | MessageDone | MessageDone |

**本轮已真实复现：文件已写入，收尾模型却报告未写入。** 核验未执行的判断本身正确，但不能因此否认提交。
这是一个厂商/模型的一次受控样本，不证明发生频率，也不是所有厂商的普遍行为。
它把确定性基线中的“输入事实丢失”与真实输出矛盾连接起来，不再只依赖测试替身话术。

脱敏报告保存在外部测试工作区，沿用五轮保留：

- 正常：`roleplex-command/test-20260914112119/case-9laaobbo/real-t0-observation.json`
- 触顶：`roleplex-command/test-20260914112119/case-5_amt8kg/real-t0-observation.json`

报告仅含枚举、布尔比对、模型路由、工具状态和真实 usage；不保存随机回执、Prompt 或模型原始输出。
凭据复用契约测试的后端加密/解密边界，本探针不新增凭据数据库或浏览器传参。

### 2. 完整工具集两轮真实浏览器场景

显式命令（frontend 目录）：

```bash
npm run test:e2e:real-world -- runtime-service-provider.spec.ts
```

结果 **1 passed（39.4 秒）**。实际前端、后端、世界包装器及真实 Provider；测试用例显式关闭 screenshot/trace/video。
独立验证了 index.html / note.txt、两轮实际页面与保留内容、服务跨轮存续、停服修改再启动、私有 diff 和最终正常回收。
模型自行选择工具路径，未强制使用批量形式；本轮实际观察到：

- 第 1 轮：list → read → write → start_service → run_shell。
- 第 2 轮：read → service_status → stop_service → service_status → edit → read → start_service → run_shell。
- 批量 read：3 次，均成功且详情通过；批量 write/edit：2 次，均成功且详情通过。
- edit：1 次成功，diff 已核验；服务列表发现：2 次成功，匹配首轮服务。
- 写入拒绝诊断：0 次，**未覆盖**该真实分支，不将模型正确停服视为拒绝路径验证。

后端事件进一步显示第 2 轮 generation 2 触发 `generation.recursion_limit_reached`，上限仍为产品默认 **15**。
首轮完成 5 次 Provider 请求，第二轮完成 9 次（含收尾），共 14 次。
因此“文件/页面任务通过”与“达到图上限”可以同时发生；现有浏览器用例没有评分自然语言收尾的一致性，
不能用其 passed 推断回答可信。确定的真实回答矛盾来自上一项受控实验。

保留证据：

- 世界：`data/roleplex-real-world-e2e-20260914111817/default/`（含加密真实 Key，仅供本机核对）。
- 独立工作区：`roleplex-real-world-e2e-20260914111817/default/runtime-service/`（测试指南的外部工作区根下）。
- 正式测试报告：`logs/tests/e2e/real/2026-09-14/11-18-17_e852867a/summary.json`。
- 脱敏工具观察：同目录 `artifacts/diagnostics/__70309682.json`，由 `artifacts.json` 索引和校验。

用例正常回收成功，没有用紧急回收掩盖失败；结束后确认 8004/51177 测试端口已释放，后端进程已退出。
用例亦核验预览端口不再可连接。测试产物索引仅有脱敏诊断，无截图、trace 或 video。

### 3. 实际用量与限制

下表逐次累加厂商返回的统计，未估算 token 或金额；未返回缓存写入用量，不补零冒充厂商统计。

| 场景 | Provider 调用 | input tokens | output tokens | total tokens | cache hit tokens |
|---|---:|---:|---:|---:|---:|
| 正常预算对照 | 3 | 1,909 | 303 | 2,212 | 896 |
| 低步数触顶 | 3 | 1,246 | 640 | 1,886 | 640 |
| 两轮浏览器场景 | 14 | 68,478 | 4,308 | 72,786 | 59,776 |
| 合计 | 20 | 71,633 | 5,251 | 76,884 | 61,312 |

本轮没有另测真实 Provider 断网、限流、取消或并发读取拒绝，也没有 Anthropic、Windows 实机或 PostgreSQL 实例验证。
没有修复产品逻辑。T1 应保留运行时事实并独立展示系统摘要，不能把提高图上限作为这次矛盾的修复。

## T1 最小实现决定与验收关口

事实来源、关联缺口与收尾控制流只维护在[内部事实协议](../protocol/internal/execution-facts.md)；
公开字段只维护在[执行摘要协议](../protocol/public/messaging/execution-summary.md)。两者均明确为预留。

最小实现选择是原消息所有者生成安全摘要，复用原消息 part/revision 与 Owner 加密详情，不新增表或另一套 Trace。
异常模型解释首版关闭，可靠系统摘要优先；后续解释仍须经 ContextBuilder 鉴权与预算。
这一设计需要随 T0 人工验收确认，再进入 T1。T2/T3/T4 候选额度与产品配置尚未定稿。

人工验收可检查本页探针结论、内部来源表、公开摘要的隐私/兼容边界及上述最小实现选择。
本阶段不修改用户可见流程，因此不要求浏览器里验收新功能；T1 必须补真实前后端的成功/失败权限路径、
刷新/重连与截图，并按其阶段验收运行独立真实 Provider 场景。
本轮已补充上文真实 Provider 范围，不声称覆盖 Windows 实机、PostgreSQL 实例或完整前端回归。
