# T1 执行事实与可信收尾验证

> 本页保留对应阶段的验证记录与参考步骤，不表示本次已重新运行。当前测试选择、环境与收费执行条件见[测试指南](README.md)；历史排期和验收关口不自动约束新任务。

| 元数据 | 值 |
|---|---|
| 受众 | 开发者、测试与人工验收人员 |
| 状态 | 已实现，自动验证与人工验收通过 |
| 版本 | 1 |
| 维护者 | Roleplex |
| 复核日期 | 2026-09-14 |
| 事实来源 | `test_execution_summary.py`、Agent/文件/恢复测试、`execution-summary.spec.ts`、独立真实 Provider 报告 |

## 本轮收窄后的当前行为

用户确认将 T1 收窄到中断处理：新回复不再生成 execution_summary，任何历史中的旧摘要也不传给模型；
不再添加“非权威正文”前缀或用统计摘要替代超预算消息。Context schema 升为 4，正常历史保留原正文与工具占位，
停止回复仅保留简短原因，失败/中断不自动进入历史；不会把下一轮聊天自动当作恢复工作。

调用级工具证据和原加密详情继续保存；停止原因改为消息 meta_json.stop_reason，并由消息响应提供 stop_reason。
该字段不携带计数或正文，旧记录可兼容读取原服务器摘要中的停止原因。旧已结束记录不批量重写。
新摘要生成与历史注入代码已删除；只保留旧类型的拒绝伪造/隐藏/忽略处理，以及旧停止原因读取。

本轮沿用后端、fake 浏览器和独立真实 Provider 测试，验证“没有摘要但仍保留提交证据和无额外收尾”。
下面早期 T1 的统计摘要截图与测试结果保留为变更历史，不代表当前行为。

本轮前端构建通过；执行证据/Owner 详情浏览器 2 passed，普通单聊与连接恢复 14 passed。
最终后端全量 **340 passed、3 skipped、12 deselected**（239.6 秒）。
首次全量回归 338 passed、1 failed：测试等待函数只检查活动任务清空，在跨终态提交的历史快照中提前返回 generating 消息。
等待条件已补充消息自身终态，并增加确定性时序回归，没有放宽产品完成状态或重试业务操作。

真实 Provider 对照 2 passed、2 deselected（DeepSeek V4 Flash）：正常 15 步下完成写入/核验，3 步触顶时保留
原调用 applied=true，模型请求只有 2 次，无额外收尾。报告版本 3 使用逐调用 tool_facts，不构造 system_summary。
外部报告位于 `roleplex-command/test-20260914132519/` 的 `case-dho1w27z`（正常）与 `case-2jsm1zjb`（触顶）。

完整两轮真实浏览器 1 passed（33.7 秒），日志 `logs/tests/e2e/real/2026-09-14/13-25-19_df1ef2b9/`，世界为
`data/roleplex-real-world-e2e-20260914132519/default/`。第一轮 done，第二轮真实触发默认图预算停止；两个角色回复
均没有 execution_summary part，各自原工具卡保留 2 项提交事实。文件、页面、diff、服务保留与最终正常回收全部通过。
实际批量读取 3 次、批量修改 2 次、edit 1 次；本轮未触发无 ID 服务列表或写入拒绝诊断，未将它们冒充覆盖。

本轮全部真实验证共 18 次 Provider 请求，厂商统计 input=69,737、output=3,732、total=73,469、cache hit=61,184。
它们不是启用/关闭摘要的受控缓存 A/B，不能据此宣称缓存命中率提升。测试关闭真实截图/trace/video，结束后服务端口均释放。


## 修复与范围

[T0](tool-reliability-t0.md) 已真实复现“文件写入成功，触顶后的额外收尾却报告未写入”。T1 移除了这次
不完整的额外模型请求，改为原消息所有者从实际提交凭据生成系统执行摘要；正常模型回答保持原链路。
图上限保持 15，不增加文件预算或工具权限，不取消停服保护，不实现 T2/T3/T4。

文件提交状态与工具执行状态分开保存。错误、取消、diff/加密降级不覆盖已确认提交；结果未知不当作未执行。
图预算只凭框架异常或受控 remaining_steps/派发状态确认，未配对结果使用 AGENT_PROTOCOL_ERROR。
同一消息在工具边界、终态与启动恢复时维护摘要，保留原 part/call 身份、revision 和事件顺序。
上下文 schema 3 读取服务器事实，正文太大时可只保留预算内摘要；失败/中断的模型正文仍不进入下一轮。

公开字段、隐私与兼容以[执行摘要](../protocol/public/messaging/execution-summary.md)为准，
来源与控制流以[内部事实协议](../protocol/internal/execution-facts.md)为准。没有表/列/索引变化，不需要 Alembic 迁移。

## 确定性后端与浏览器

backend：

```bash
python -m pytest -q --tb=short --show-capture=no
```

最终产品代码的完整回归 **337 passed、3 skipped、12 deselected**（233.1 秒）。随后新增测试时钟隔离与
未来/过期 JWT 拒绝验证，相关 Shell、文件差异和执行摘要专项 **33 passed**；不将不同轮次的计数相加冒充单次全量运行。

专项为 `test_execution_summary.py`、`test_tool_reliability_t0.py`、`test_agent_loop.py`、`test_agent_pipeline.py`、
`test_write_diff.py`、`test_workspace_batch_mutation.py`、`test_tool_timeline.py`、`test_context_builder.py`。
覆盖真实原生写后触顶、厂商失败、提交后取消、部分提交/未知、32 个引用上限、累计不丢失、输入伪造拒绝、
失败正文隔离、历史事实预算降级、重复终态不覆盖、重启后的未知结果，以及原 WS 断线重放/快照回归。
T0 的收尾缺陷断言已改为 T1 目标行为，T2 读取缺陷仍保留。

frontend：

```bash
npm run build
npm run test:e2e:commands -- execution-summary.spec.ts
npm run test:e2e:commands -- timeline.spec.ts write-diff.spec.ts
npm run test:e2e -- m2-chat.spec.ts connection-session.spec.ts
```

新增用例用真实前后端、原生文件与 fake Provider，核对实际文件、“已确认文件提交：1 项”、真实图预算停止、
断线与刷新恢复、迟到旧 revision 不覆盖、Guest 可见安全摘要但详情 403、伪造摘要 422，以及 1280/390px 截图。
窄屏沿用已有收起侧栏操作。旧时间线、diff 与正常单聊/取消/连接恢复另行回归。

新增截图验收暴露报告器同一用例多个附件覆盖的问题，现以重试/附件序号分配独立文件；不记录原始附件名称，
最终两张截图均保留且索引 SHA-256 与文件匹配。该规则先同步日志 v2，不改变日志 schema 或业务 Trace。
最终截图见 `logs/tests/e2e/fake/2026-09-14/11-54-37_f75fdd58/artifacts/screenshots/`，通过 `artifacts.json` 索引。

浏览器结果：新增执行摘要用例 1 passed，既有时间线/diff 2 passed，普通单聊/连接恢复 14 passed，前端构建通过。
修复了旧断言对上下文版本/part 数量的假设，以及“已停止”文本在摘要出现后不再唯一的选择器，未放宽功能断言。
一轮后端全量回归中，Shell 篡改用例在工作区准备请求收到 401 后失败，未进入篡改操作；
完整 Shell 组随后 14 passed，准备 fixture 已增加状态/安全错误码断言，不以 KeyError 掩盖准备失败。
另一轮文件用例也曾在读取消息时出现 401。独立时钟观测记录到宿主墙钟回退约 743.75ms，这可能使新 JWT 的
iat 暂时落在校验时刻之后；早先响应未保存 JWT 异常分类，不能声称已逐条证明两次 401 的同一根因。
隔离命令测试现在为签发/校验共用单调推进的 UTC 时钟，保留正常过期与未来 iat 拒绝验证；产品 Token 时钟、
leeway、签名、撤销与权限规则均未修改。若再出现 Owner 401，仅记录固定错误码、异常类别和有界时间差，
不记录 Token、签名密钥或完整声明，也不自动重试业务操作。

## 修复后的真实 Provider 对照

显式命令（backend）：

```bash
python -m pytest tests/contract/test_tool_reliability_real.py -m contract -k openai_compatible -q -s --tb=no
```

2026-09-14 使用 DeepSeek `deepseek-v4-flash`，**2 passed、2 deselected**。运行前告知联网费用；不反复计费追求特定回答。
探针复用原生文件提交与 WriteCaptureScope，真实领域事件通过产品事实投影构造安全摘要；没有替换真实模型结果。
这是执行层/循环对照，不冒充完整产品工作区鉴权或浏览器验证。

| 项目 | 15 步正常对照 | 3 步受控触顶 |
|---|---|---|
| 实际文件提交并独立读回 | 是 | 是 |
| 模型驱动核验执行 | 是 | 否 |
| 终态原因 | completed | graph_budget |
| 系统摘要确认提交 | 1 项 | **1 项** |
| 明确尚未派发 | 0 | **1 项** |
| 额外模型收尾请求 | 无 | **无** |
| 实际 Provider 调用 | 3 | **2**（T0 同类触顶场景为 3） |
| 模型最终判断 | confirmed、回执匹配 | 不追加模型解释，由系统摘要交付事实 |

正常对照中的 `probe_verify` 是测试自定义工具，不属于产品只读白名单，所以其副作用保守标为 unknown；
不会仅因工具名称含 verify 或成功退出就提高可信度。文件提交事实仍来自真正的原生提交凭据。
3 步触顶不等于用户任务全部完成：核验尚未执行，系统明确保留这一边界。

观察报告格式升为 2（测试产物，不是业务协议版本），不再观察已删除的 `_wrap_up`；不保存 Prompt、回执原文或完整模型输出。
外部命令测试工作区的本轮目录为 `roleplex-command/test-20260914115230/`：
正常报告 `case-s5h9frqe/real-t0-observation.json`，触顶报告 `case-ace1m4zq/real-t0-observation.json`。
沿用整轮五次保留，文件名保留 T0 来源以便追溯。

## 修复后的完整两轮真实浏览器

显式命令（frontend）：

```bash
npm run test:e2e:real-world -- runtime-service-provider.spec.ts
```

**1 passed（27.2 秒）**；真实前端/后端/世界包装器与 DeepSeek，关闭 screenshot/trace/video。
用例允许“正常完成”或“明确图预算停止”进入独立产物核验，不接受普通错误/任意 stopped；文件与页面标准未放宽。
本轮自然完成，**未触发图预算停止**；不将其宣称为真实触顶覆盖，触顶由上述专用对照证明。

页面两轮、保留内容、note.txt、原生 diff、服务跨轮存续和最终正常回收均通过。
实际观察到批量读取 3 次、批量修改 2 次、edit 1 次、服务列表发现 1 次，均成功且对应详情核验通过；
写入拒绝诊断 0 次，未覆盖该真实分支。没有要求模型换工具绕过保护，没有为取得某路径重跑。

世界：`data/roleplex-real-world-e2e-20260914115305/default/`；外部工作区使用同 stamp 的独立目录。
日志：`logs/tests/e2e/real/2026-09-14/11-53-05_d4f5b076/summary.json`，工具路径见其 artifacts 索引。
测试库含加密真实 Key，仅供本机核对；不发送到浏览器或日志。

## 厂商实际用量与未覆盖项

只累加厂商返回的统计，不估算金额、token 或缺失缓存字段。

| 场景 | 调用 | input | output | total | cache hit |
|---|---:|---:|---:|---:|---:|
| 正常预算对照 | 3 | 1,841 | 236 | 2,077 | 896 |
| 低步数触顶 | 2 | 1,090 | 92 | 1,182 | 640 |
| 两轮浏览器 | 12 | 60,336 | 2,751 | 63,087 | 52,864 |
| 合计 | 17 | 63,267 | 3,079 | 66,346 | 54,400 |

这是一组厂商/模型样本，不能证明所有模型都遵从输出格式或所有任务都能在 15 步内完成。
未验证 Anthropic、Windows 实机、PostgreSQL 实例；没有新增模型解释策略或可配置运行预算。
文件提交到数据库之间仍无分布式事务，崩溃窗口没有证据时保持未知，不从当前文件补造历史。

## 人工验收

用户随后要求移除可见摘要：本轮仅调整聊天展示，后台事实、接口、上下文和 Owner 详情保持原实现。
`execution-summary.spec.ts` 已改为验证正常/触顶/刷新/Guest 均不显示摘要及计数，后台仍保留提交事实；
触顶仅在消息状态处简短提示。旧摘要截图是历史验收证据，不代表当前界面；本次不需要重复真实 Provider 计费验证。

重启后端并刷新前端。按测试指南启动保留的本轮 fake 命令测试世界，以该轮 Owner 登录“可信执行验收”：
查看原生写入工具卡和“达到本轮执行步数上限”状态，确认消息底部没有系统摘要、计数或缺失提示；刷新后仍如此。
正常单聊继续显示模型回答，停止后保留原有“已停止”状态。Owner 仍可展开工具详情。
后台不再生成或注入统计摘要，具体工具证据仍保留，也不会重读当前文件。2026-09-14 用户已确认本版本人工验收通过并授权提交。
